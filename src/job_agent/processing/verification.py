"""Check offers seen on a job site (e-mail alerts) against the career site.

Statuses of a group whose first offer came from a job site:
- "verified": the same offer was found on the company's career site;
- "pending": the company has a connector, the offer is not there yet. The
  offer is notified at once (speed first), with this status;
- "missing": still not on the career site after `grace_period_hours`: the
  offer is rejected and logged, the user is warned if already notified;
- "unverifiable": company not watched, no connector, career site in error,
  or a listing too short to conclude. Kept: an error never means "missing".

Daily recheck: an offer of the tracking file that a career site no longer
lists is marked "disappeared".

Every change of status is recorded (verification_events) and appended to
the "Vérifications" sheet of the tracking file.

When can the agent say "missing"? Only when the career site was read
without error in this run, and:
- for connectors that catch every new offer at each run (they read until
  the offers are already known or too old), the database holds every
  career offer published since the connector started;
- for a short feed (latest offers only), when the feed reaches back over
  the whole freshness window.
Before saying "missing", a looser comparison on the title alone is made:
a close title on the career site gives "verified (correspondance
probable)" rather than a rejection.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from job_agent.config.models import AppConfig, WatchedCompany
from job_agent.processing.dedup import signature
from job_agent.storage import Storage

VERIFIED = "verified"
PENDING = "pending"
MISSING = "missing"
UNVERIFIABLE = "unverifiable"
DISAPPEARED = "disappeared"

LABELS = {
    VERIFIED: "vérifiée à la source",
    PENDING: "vérification en attente",
    MISSING: "absente du site carrière",
    UNVERIFIABLE: "non vérifiable",
    DISAPPEARED: "disparue du site carrière",
}
CAREER_SITE_LABEL = "site carrière"

# Connectors that read every new offer at each run.
CATCHES_EVERY_NEW_OFFER = {"ashby", "workday", "successfactors_search", "avature", "phenom"}
# Title closeness (0 to 100) for a probable match on the title alone.
LOOSE_TITLE_MATCH = 85
RECHECK_STATE_KEY = "verification_last_recheck"
MISSING_REASON = "absente du site carrière après {hours} h"


@dataclass
class Listing:
    """What a career site gave in this run, for one company."""

    ats: str
    ok: bool
    # Oldest publication date among the offers listed (None if unknown).
    covered_since: datetime | None = None
    # Source ids listed in this run.
    seen_ids: frozenset[str] = frozenset()


def status_label(row) -> str:
    """Text of the "Statut de vérification" column."""
    if row["source"] == "career_page" and row["verification"] != DISAPPEARED:
        return CAREER_SITE_LABEL
    return LABELS.get(row["verification"] or "", "")


def watched_company(config: AppConfig, company_key: str) -> WatchedCompany | None:
    for company in config.companies.watchlist:
        if company.name == company_key:
            return company
    return None


def initial_status(config: AppConfig, company_key: str) -> tuple[str | None, str]:
    """Status of a new group started by a job site offer, and its detail."""
    from job_agent.collectors import CONNECTORS

    if not config.source_verification.enabled:
        return None, ""
    company = watched_company(config, company_key)
    if company is None:
        return UNVERIFIABLE, "entreprise hors de la liste surveillée"
    if not config.sources.career_pages.enabled or company.ats not in CONNECTORS or not company.careers_url:
        return UNVERIFIABLE, "pas de connecteur pour ce site carrière"
    return PENDING, ""


def _loose_match(storage: Storage, main) -> str | None:
    """A career offer of the same company with a close title, if any."""
    wanted = " ".join(sorted(signature(main["company_key"], main["title"], None, None, "", None,
                                       main["location"]).title_words))
    for row in storage.career_offers_of_company(main["company_key"]):
        words = " ".join(sorted(signature(row["company_key"], row["title"], None, None, "", None,
                                          row["location"]).title_words))
        if wanted and words and fuzz.token_set_ratio(wanted, words) >= LOOSE_TITLE_MATCH:
            return row["title"]
    return None


def _can_conclude_missing(listing: Listing | None, config: AppConfig, now: datetime) -> tuple[bool, str]:
    if listing is None:
        return False, "site carrière non lu pendant ce passage"
    if not listing.ok:
        return False, "site carrière en erreur"
    if listing.ats in CATCHES_EVERY_NEW_OFFER:
        return True, ""
    window_start = now - timedelta(days=config.freshness.max_age_days)
    if listing.covered_since is not None and listing.covered_since <= window_start:
        return True, ""
    return False, "liste du site carrière trop courte pour conclure"


def check_pending(storage: Storage, config: AppConfig, listings: dict[str, Listing], now: datetime) -> dict[str, int]:
    """Conclude on the pending offers whose grace period is over."""
    settings = config.source_verification
    counts = {MISSING: 0, UNVERIFIABLE: 0, VERIFIED: 0}
    grace = timedelta(hours=settings.grace_period_hours)
    for main in storage.groups_with_verification(PENDING):
        if now - datetime.fromisoformat(main["first_seen_at"]) < grace:
            continue
        can_conclude, why_not = _can_conclude_missing(listings.get(main["company_key"]), config, now)
        if not can_conclude:
            storage.set_verification(main["id"], UNVERIFIABLE, why_not, now)
            counts[UNVERIFIABLE] += 1
            continue
        close_title = _loose_match(storage, main)
        if close_title:
            storage.set_verification(main["id"], VERIFIED, f"correspondance probable : « {close_title} »", now)
            counts[VERIFIED] += 1
            continue
        reason = MISSING_REASON.format(hours=settings.grace_period_hours)
        storage.set_verification(main["id"], MISSING, reason, now)
        counts[MISSING] += 1
        if not settings.exclude_if_missing_on_career_site:
            storage.add_flag(main["id"], reason)
            continue
        storage.reject_offer(main["id"], reason)
        if storage.is_notified(main["id"]):
            company = main["company"] or main["company_key"]
            storage.add_to_outbox(
                f"missing:{main['id']}",
                f"❌ Offre introuvable sur le site carrière de {company} "
                f"{settings.grace_period_hours} h après l'alerte : {main['title']}. "
                "Elle a peut-être été pourvue ou retirée : inutile d'y consacrer du temps.",
            )
    return counts


def on_career_match(storage: Storage, group_id: int, now: datetime) -> None:
    """A career site offer joined a group started by a job site offer."""
    main = storage.get_offer(group_id)
    if main is None or main["source"] == "career_page":
        return
    if main["verification"] in (PENDING, UNVERIFIABLE, MISSING, None):
        storage.set_verification(group_id, VERIFIED, "même offre trouvée sur le site carrière", now)


def recheck_tracked(
    storage: Storage, config: AppConfig, listings: dict[str, Listing], run_started: datetime, now: datetime
) -> list:
    """Tracked offers no longer listed by their career site (once per interval).

    Only offers published after the oldest offer listed in this run are
    judged: older ones may simply be beyond what the site showed.
    Returns the main rows newly marked "disappeared".
    """
    settings = config.source_verification
    if not settings.enabled or not settings.recheck_tracked_offers:
        return []
    last = storage.get_state(RECHECK_STATE_KEY)
    if last and now - datetime.fromisoformat(last) < timedelta(hours=settings.recheck_interval_hours):
        return []
    gone = []
    for company_key, listing in listings.items():
        if not listing.ok or listing.covered_since is None:
            continue
        for member in storage.tracked_career_offers(company_key):
            if member["source_offer_id"] in listing.seen_ids or not member["published_at"]:
                continue
            if datetime.fromisoformat(member["published_at"]) <= listing.covered_since:
                continue  # beyond what the site listed in this run
            storage.set_verification(member["group_id"], DISAPPEARED, "n'apparaît plus sur le site carrière", now)
            gone.append(storage.get_offer(member["group_id"]))
    storage.set_state(RECHECK_STATE_KEY, now.isoformat())
    notified = [row for row in gone if storage.is_notified(row["id"])]
    if notified:
        lines = [f"🗑 {len(notified)} offre(s) de votre suivi ne sont plus en ligne sur le site carrière :"]
        lines += [f"• {row['title']} ({row['company'] or row['company_key']})" for row in notified[:20]]
        storage.add_to_outbox(f"gone:{now:%Y-%m-%dT%H}", chr(10).join(lines))
    return gone


def listing_from(ats: str, offers, stored_dates: dict[str, str | None]) -> Listing:
    """Listing of one company in this run, from the offers its connector returned.

    `stored_dates`: publication dates already known for these offers (the
    connectors do not read again the details of known offers).
    """
    dates = []
    for offer in offers:
        value = offer.published_at
        if value is None and stored_dates.get(offer.source_offer_id):
            value = datetime.fromisoformat(stored_dates[offer.source_offer_id])
        if value is not None:
            dates.append(value)
    return Listing(ats, True, min(dates) if dates else None, frozenset(o.source_offer_id for o in offers))

