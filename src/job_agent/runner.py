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
) -> tuple[Counter[str], list[str]]:
    totals: Counter[str] = Counter()
    unavailable: list[str] = []
    if not config.sources.career_pages.enabled:
        log.info("Sites carrière désactivés dans config.yaml.")
        return totals, unavailable

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
                continue
            counts = process_offers(offers, config, storage, sectors, now)
            storage.record_source(run_id, source_name, len(offers), None)
            totals.update(counts)
            log.info(
                "  %s : %d collectée(s), %d nouvelle(s) retenue(s), %d écartée(s), %d doublon(s)",
                source_name, len(offers), counts["kept"], counts["rejected"], counts["duplicates"],
            )
            streak = storage.zero_streak(source_name)
            if streak >= ZERO_STREAK_ALERT:
                log.warning("  Attention : %s ne renvoie aucune offre depuis %d passages.", source_name, streak)
    finally:
        client.close()
    return totals, unavailable


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
            "l'application Google est en mode Test)." + NEWLINE
            + "Pour le renouveler, lancez dans le dossier du projet :" + NEWLINE
            + "uv run job-agent gmail-setup" + NEWLINE
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
        f"({html.escape(subject[:120])})." + NEWLINE
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


def _update_tracking(config: AppConfig, storage: Storage) -> None:
    if config.tracking.backend != "excel":
        log.warning("Suivi « %s » pas encore disponible : seul excel est géré.", config.tracking.backend)
        return
    pending = storage.pending_tracking()
    if not pending:
        return
    try:
        result = append_offers(
            config.tracking.excel.path, pending, storage.group_members,
            keep_rejected=config.tracking.keep_rejected, timezone_name=config.schedule.timezone,
        )
    except TrackingFileLocked:
        log.warning(
            "Fichier de suivi ouvert dans Excel : %d offre(s) seront ajoutées au prochain passage.", len(pending)
        )
        return
    storage.mark_tracked(result.written_ids)
    log.info("Fichier de suivi : %d offre(s) retenue(s) et %d écartée(s) ajoutées.", result.kept, result.rejected)


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
                log.info("Passage n°%d (profil %s)%s", run_id, config.profile_name,
                         " : premier passage silencieux" if silent else "")
                totals, unavailable = _collect_and_process(config, storage, run_id, now)
                if unavailable:
                    log.info("Connecteur non disponible pour %d entreprise(s) : %s",
                             len(unavailable), ", ".join(unavailable))
                totals.update(_collect_email_alerts(config, storage, run_id, now))
                _update_tracking(config, storage)
                totals.update(_notify(config, storage, silent))
                summary = dict(totals)
                storage.finish_run(run_id, summary)
                log.info(
                    "Bilan : %d collectée(s), %d nouvelle(s) retenue(s), %d écartée(s), %d doublon(s), "
                    "%d déjà vue(s), %d notifiée(s), %d source(s) en erreur.",
                    totals["collected"], totals["kept"], totals["rejected"], totals["duplicates"],
                    totals["already_seen"], totals["notified"], totals["source_errors"],
                )
            finally:
                storage.close()
    except AlreadyRunning:
        log.info("Un autre passage est déjà en cours : celui-ci est annulé.")
    return 0
