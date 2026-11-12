"""One run of the agent: collect, process, track, notify, report."""

import html
import logging
from collections import Counter
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from job_agent.collectors import CONNECTORS
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import AppConfig
from job_agent.notify.telegram import NotificationError, notify_pending, telegram_sender_from_env
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.scheduling import AlreadyRunning, in_schedule, internet_available, run_lock
from job_agent.storage import Storage, utc_now
from job_agent.tracking.excel import TrackingFileLocked, append_offers

LOG_DIR = Path("private/logs")
LOCK_FILE = Path("private/job_agent.lock")
ZERO_STREAK_ALERT = 3
# A career site silent for this many runs in a row (about one day) is reported on Telegram.
SILENT_SOURCE_RUNS = 6
SILENT_STATE_PREFIX = "silent_source:"

log = logging.getLogger("job_agent")


def setup_logging(log_dir: Path = LOG_DIR) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "job_agent.log").resolve()
    current = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
    if current and Path(current[0].baseFilename) == log_file:
        return
    for handler in list(log.handlers):  # another folder: start again
        log.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(file_handler)
    log.addHandler(console)
    log.setLevel(logging.INFO)
    # httpx logs every requested URL, and the Telegram URL contains the bot
    # token: these logs are switched off.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _collect_and_process(
    config: AppConfig, storage: Storage, run_id: int, now: datetime
) -> tuple[Counter[str], list[str], dict]:
    """Read the career sites. Also returns, per company, what its site listed
    in this run (used by the career site verification)."""
    from job_agent.processing.verification import Listing, listing_from

    totals: Counter[str] = Counter()
    unavailable: list[str] = []
    listings: dict[str, Listing] = {}
    if not config.sources.career_pages.enabled:
        log.info("Sites carrière désactivés dans config.yaml.")
        return totals, unavailable, listings

    sectors = SectorIndex.build(config)
    settings = config.sources.career_pages
    client = PoliteClient(settings.request_delay_seconds, settings.timeout_seconds)
    context = CollectContext(client, config, now, storage.has_offer)
    try:
        for company in config.companies.watchlist:
            connector = CONNECTORS.get(company.ats)
            if connector is None or not company.careers_url:
                unavailable.append(company.name)
                continue
            source_name = f"{company.ats}:{company.name}"
            try:
                offers = connector(company, context)
            except Exception as error:  # one source never stops the others
                log.warning("  %s : erreur, %s", source_name, error)
                storage.record_source(run_id, source_name, 0, str(error))
                totals["source_errors"] += 1
                listings[company.name] = Listing(company.ats, ok=False)
                continue
            listings[company.name] = listing_from(company.ats, offers, storage.published_dates(source_name))
            counts = process_offers(offers, config, storage, sectors, now)
            storage.record_source(run_id, source_name, len(offers), None)
            totals.update(counts)
            log.info(
                "  %s : %d collectée(s), %d nouvelle(s) retenue(s), %d écartée(s), %d doublon(s)",
                source_name,
                len(offers),
                counts["kept"],
                counts["rejected"],
                counts["duplicates"],
            )
            streak = storage.zero_streak(source_name)
            if streak >= ZERO_STREAK_ALERT:
                log.warning("  Attention : %s ne renvoie aucune offre depuis %d passages.", source_name, streak)
    finally:
        client.close()
    return totals, unavailable, listings


def _watch_silent_sources(storage: Storage, run_id: int) -> None:
    """Telegram message when a career site returns nothing for about a day, and when it is back."""
    for source_name in storage.sources_of_run(run_id):
        if source_name.startswith("email"):
            continue  # no new alert e-mail is normal
        key = SILENT_STATE_PREFIX + source_name
        reported = storage.get_state(key)
        streak = storage.zero_streak(source_name)
        site = source_name.split(":", 1)[-1]
        if streak >= SILENT_SOURCE_RUNS and not reported:
            error = storage.last_error(source_name)
            detail = f" Dernière erreur : {error}." if error else " Aucune erreur : le site a peut-être changé."
            storage.add_to_outbox(
                f"silent:{source_name}:{run_id}",
                f"⚠️ Le site carrière de {site} ne renvoie plus aucune offre depuis {streak} passages.{detail} "
                "Ses offres restent couvertes par vos alertes e-mail ; "
                "si cela dure, signalez-le pour corriger le connecteur.",
            )
            storage.set_state(key, str(run_id))
            log.warning("  %s : muet depuis %d passages, signalé sur Telegram.", source_name, streak)
        elif streak == 0 and reported:
            storage.add_to_outbox(
                f"back:{source_name}:{run_id}", f"✅ Le site carrière de {site} renvoie de nouveau des offres."
            )
            storage.delete_state(key)


def _verify_at_source(config: AppConfig, storage: Storage, listings: dict, now: datetime) -> None:
    """Conclude on pending verifications, and recheck the tracked offers."""
    if not config.source_verification.enabled:
        return
    from job_agent.processing.verification import MISSING, UNVERIFIABLE, VERIFIED, check_pending, recheck_tracked

    counts = check_pending(storage, config, listings, now)
    if any(counts.values()):
        log.info(
            "Vérification à la source : %d vérifiée(s), %d absente(s) du site carrière, %d non vérifiable(s).",
            counts[VERIFIED],
            counts[MISSING],
            counts[UNVERIFIABLE],
        )
    gone = recheck_tracked(storage, config, listings, now, now)
    if gone:
        log.info("Revérification : %d offre(s) du suivi ne sont plus en ligne.", len(gone))


GMAIL_WARNING_KEY = "gmail_auth_warning_date"
NEWLINE = chr(10)


def _warn_gmail_authorisation(config: AppConfig, storage: Storage, now: datetime) -> None:
    """Telegram message, at most once a day, when the Gmail access expired."""
    today = now.date().isoformat()
    if config.notifications.channel != "telegram" or storage.get_state(GMAIL_WARNING_KEY) == today:
        return
    try:
        telegram_sender_from_env()(
            "⚠️ job-agent : l'accès Gmail a expiré (c'est normal chaque semaine tant que "
            "l'application Google est en mode Test)."
            + NEWLINE
            + "Pour le renouveler, lancez dans le dossier du projet :"
            + NEWLINE
            + "uv run job-agent gmail-setup"
            + NEWLINE
            + "Les sites carrière continuent d'être lus normalement."
        )
    except NotificationError as error:
        log.warning("Avertissement Gmail non envoyé : %s", error)
        return
    storage.set_state(GMAIL_WARNING_KEY, today)


def _collect_email_alerts(config: AppConfig, storage: Storage, run_id: int, now: datetime) -> Counter[str]:
    """Read new alert e-mails (Gmail, read-only) and process their offers."""
    totals: Counter[str] = Counter()
    settings = config.sources.email_alerts
    if not settings.enabled:
        return totals

    from job_agent.gmail.auth import GmailAuthError
    from job_agent.gmail.parsers import PARSERS
    from job_agent.gmail.reader import GmailLockError, GmailReader, build_service

    try:
        alerts = GmailReader(build_service(), settings).read_alerts(skip=storage.processed_emails())
    except GmailAuthError as error:
        log.warning("  Alertes e-mail : %s", error)
        storage.record_source(run_id, "email", 0, str(error))
        totals["source_errors"] += 1
        _warn_gmail_authorisation(config, storage, now)
        return totals
    except GmailLockError as error:
        log.warning("  Alertes e-mail : %s", error)
        storage.record_source(run_id, "email", 0, str(error))
        totals["source_errors"] += 1
        return totals
    except Exception as error:  # one source never stops the others
        log.warning("  Alertes e-mail : erreur de lecture Gmail (%s)", type(error).__name__)
        storage.record_source(run_id, "email", 0, type(error).__name__)
        totals["source_errors"] += 1
        return totals

    sectors = SectorIndex.build(config)
    per_provider: Counter[str] = Counter()
    for alert in alerts:
        parser = PARSERS.get(alert.provider)
        if parser is None:
            # Left unread: it will be processed once a reader exists.
            log.info("  Alertes e-mail : lecteur pas encore disponible pour %s.", alert.provider)
            continue
        try:
            offers = parser(alert)
        except Exception as error:
            log.warning("  Alertes e-mail : e-mail %s illisible (%s)", alert.provider, type(error).__name__)
            continue
        counts = process_offers(offers, config, storage, sectors, now)
        totals.update(counts)
        per_provider[alert.provider] += len(offers)
        _queue_more_offers_link(storage, alert)
        storage.mark_email_processed(alert.message_id, alert.provider, len(offers))
    for provider, count in per_provider.items():
        storage.record_source(run_id, f"email:{provider}", count, None)
        log.info("  email:%s : %d offre(s) lue(s) dans les alertes", provider, count)
    if not alerts:
        log.info("  Alertes e-mail : aucun nouvel e-mail.")
    return totals


def _queue_more_offers_link(storage: Storage, alert) -> None:
    """Queue the "see all offers" link of an alert, for the user to open."""
    from job_agent.gmail.parsers import MORE_OFFERS_LINKS

    extract = MORE_OFFERS_LINKS.get(alert.provider)
    link = extract(alert) if extract else None
    if not link:
        return
    subject = alert.subject.split(" : ", 1)[-1] if " : " in alert.subject else alert.subject
    storage.add_to_outbox(
        f"more:{alert.message_id}",
        f"🔎 Alerte {alert.provider.capitalize()} : l'e-mail n'affiche qu'une partie des offres "
        f"({html.escape(subject[:120])})."
        + NEWLINE
        + f'<a href="{html.escape(link, quote=True)}">Voir toutes les offres</a> '
        "(lien à ouvrir vous-même : l'agent ne l'ouvre jamais).",
    )


def _send_outbox(storage: Storage, sender) -> int:
    sent = 0
    for row in storage.pending_outbox():
        try:
            sender(row["message"])
        except NotificationError as error:
            log.warning("Telegram : %s. Messages restants envoyés au prochain passage.", error)
            break
        storage.mark_outbox_sent(row["id"])
        sent += 1
    return sent


def _enrich_with_ai(config: AppConfig, storage: Storage) -> Counter[str]:
    """AI reading of new kept offers (before Excel and Telegram)."""
    from job_agent.ai.enrich import enrich_new_offers
    from job_agent.ai.extraction import AIUnavailable, GeminiExtractor

    if config.ai.provider != "gemini":
        return Counter()
    try:
        extractor = GeminiExtractor.from_env(config.ai.model)
    except AIUnavailable as error:
        log.info("Analyse par IA désactivée : %s.", error)
        return Counter()
    counts = enrich_new_offers(storage, config, extractor)
    if counts["analysed"]:
        log.info(
            "IA : %d offre(s) analysée(s), %d écartée(s) d'après l'IA, %d remarque(s) ajoutée(s).",
            counts["analysed"],
            counts["rejected"],
            counts["flags"],
        )
    if counts["unavailable"] or counts["quota"]:
        log.warning(
            "IA indisponible pour l'instant (%s) : les offres seront analysées plus tard.",
            storage.get_state("ai_last_error"),
        )
    return Counter(ai_analysed=counts["analysed"], kept=-counts["rejected"], rejected=counts["rejected"])


FLAG_TITLE_ONLY_SCORE = "score estimé sur l'intitulé"


def _score_offers(config: AppConfig, storage: Storage) -> Counter[str]:
    """Offer / CV match score of new kept offers (local model)."""
    rows = storage.offers_needing_score()
    if not rows:
        return Counter()
    from job_agent.ai.matching import CVMatcher, MatchingUnavailable

    try:
        matcher = CVMatcher.from_files(
            config.ai.anonymized_cv_path, config.technical_profile.mastered_skills, config.ai.strip_contact_details
        )
    except MatchingUnavailable as error:
        log.info("Score de correspondance désactivé : %s.", error)
        return Counter()
    except Exception as error:  # model missing and no internet, for example
        log.warning("Score de correspondance indisponible (%s) : réessai au prochain passage.", type(error).__name__)
        return Counter()
    scores = matcher.scores([(row["title"], row["description"] or "") for row in rows])
    for row, score in zip(rows, scores):
        storage.set_match_score(row["id"], score)
        if not (row["description"] or "").strip():
            storage.add_flag(row["id"], FLAG_TITLE_ONLY_SCORE)
    log.info("Score de correspondance calculé pour %d offre(s).", len(rows))
    return Counter(scored=len(rows))


def _update_labelling_sheet(config: AppConfig, storage: Storage) -> None:
    """Add new offers to the labelling sheet, only if the user started it."""
    from job_agent.ai.labelling import SHEET_PATH, update_sheet

    if not SHEET_PATH.exists():
        return
    try:
        added, _, labelled, prefilled = update_sheet(
            storage, SHEET_PATH, [*config.labelling.not_relevant_companies, *config.search.excluded_companies]
        )
    except PermissionError:
        log.info("Feuille d'étiquetage ouverte dans Excel : mise à jour au prochain passage.")
        return
    if added or prefilled:
        log.info(
            "Feuille d'étiquetage : %d offre(s) ajoutée(s), %d « non » évident(s) mis par l'agent "
            "(%d étiquetée(s) au total).",
            added,
            prefilled,
            labelled,
        )


def _update_tracking(config: AppConfig, storage: Storage) -> None:
    if config.tracking.backend != "excel":
        log.warning("Suivi « %s » pas encore disponible : seul excel est géré.", config.tracking.backend)
        return
    pending = storage.pending_tracking()
    events = storage.unwritten_verification_events()
    if not pending and not events:
        return
    try:
        result = append_offers(
            config.tracking.excel.path,
            pending,
            storage.group_members,
            keep_rejected=config.tracking.keep_rejected,
            timezone_name=config.schedule.timezone,
            events=events,
        )
    except TrackingFileLocked:
        log.warning(
            "Fichier de suivi ouvert dans Excel : %d offre(s) seront ajoutées au prochain passage.", len(pending)
        )
        return
    storage.mark_tracked(result.written_ids)
    storage.mark_verification_events_written(result.events_written or [])
    if pending:
        log.info("Fichier de suivi : %d offre(s) retenue(s) et %d écartée(s) ajoutées.", result.kept, result.rejected)
    if events:
        log.info(
            "Fichier de suivi : %d changement(s) de vérification ajouté(s) à l'onglet « Vérifications ».", len(events)
        )


def _notify(config: AppConfig, storage: Storage, silent: bool) -> Counter[str]:
    counts: Counter[str] = Counter()
    channel = config.notifications.channel
    if channel == "none":
        return counts
    if silent:
        result = notify_pending(storage, config, lambda text: None, silent=True)
        _send_outbox(storage, lambda text: None)  # first run: nothing is sent
        counts["silenced"] = result.silenced
        log.info("Premier passage : %d offre(s) enregistrées sans notification.", result.silenced)
        return counts
    if channel != "telegram":
        log.warning("Canal « %s » pas encore disponible : seul telegram est géré.", channel)
        return counts
    try:
        sender = telegram_sender_from_env()
    except NotificationError as error:
        log.warning("Notifications Telegram impossibles : %s", error)
        return counts
    result = notify_pending(storage, config, sender, silent=False)
    counts["notified"] = result.sent
    if result.summary:
        log.info("Telegram : un message récapitulatif pour %d offres.", result.sent)
    elif result.sent:
        log.info("Telegram : %d offre(s) notifiée(s).", result.sent)
    if result.error:
        log.warning("Telegram : %s. Les offres restantes seront envoyées au prochain passage.", result.error)
        return counts
    extra = _send_outbox(storage, sender)
    if extra:
        log.info("Telegram : %d message(s) d'information envoyé(s) (liens, dates corrigées).", extra)
    return counts


def run(config: AppConfig, force: bool = False) -> int:
    setup_logging()
    now = utc_now()
    if not force and not in_schedule(config.schedule, now):
        log.info("Hors de la plage horaire de config.yaml (schedule) : aucun passage.")
        return 0
    if not internet_available():
        # Nothing is recorded: sources are not counted as empty.
        log.warning("Pas de connexion internet : passage reporté au prochain lancement.")
        return 0
    try:
        with run_lock(LOCK_FILE):
            storage = Storage(config.storage.database_path)
            try:
                silent = config.notifications.silent_first_run and not storage.has_completed_run()
                run_id = storage.start_run(silent)
                log.info(
                    "Passage n°%d (profil %s)%s",
                    run_id,
                    config.profile_name,
                    " : premier passage silencieux" if silent else "",
                )
                totals, unavailable, listings = _collect_and_process(config, storage, run_id, now)
                if unavailable:
                    log.info(
                        "Connecteur non disponible pour %d entreprise(s) : %s", len(unavailable), ", ".join(unavailable)
                    )
                totals.update(_collect_email_alerts(config, storage, run_id, now))
                _verify_at_source(config, storage, listings, now)
                _watch_silent_sources(storage, run_id)
                totals.update(_enrich_with_ai(config, storage))
                totals.update(_score_offers(config, storage))
                _update_tracking(config, storage)
                _update_labelling_sheet(config, storage)
                totals.update(_notify(config, storage, silent))
                summary = dict(totals)
                storage.finish_run(run_id, summary)
                log.info(
                    "Bilan : %d collectée(s), %d nouvelle(s) retenue(s), %d écartée(s), %d doublon(s), "
                    "%d déjà vue(s), %d notifiée(s), %d source(s) en erreur.",
                    totals["collected"],
                    totals["kept"],
                    totals["rejected"],
                    totals["duplicates"],
                    totals["already_seen"],
                    totals["notified"],
                    totals["source_errors"],
                )
            finally:
                storage.close()
    except AlreadyRunning:
        log.info("Un autre passage est déjà en cours : celui-ci est annulé.")
    return 0
