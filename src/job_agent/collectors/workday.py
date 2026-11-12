"""Workday connector, through the JSON interface of Workday career sites.

`careers_url` is the Workday career site of the company, for example
"https://ag2rlamondiale.wd3.myworkdayjobs.com/Candidats". This interface
is the one the public page itself calls; it is not a documented API, so
requests are kept to a minimum:
- the list is filtered on France and read page by page, and reading stops
  when a whole page is older than `freshness.max_age_days`;
- the details (full text, exact date) are fetched only for NEW offers
  whose title matches the search. Other offers are stored from the list
  alone, so they still appear in the "Écartées" sheet.
"""

import re
from contextlib import suppress
from datetime import datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

PAGE_SIZE = 20
MAX_PAGES = 25
# Workday uses the same identifier for France on every career site.
FRANCE_FACET = ("locationCountry", "54c5b6971ffb4bf0b116fe7651ec789a")
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_REFERENCE = re.compile(r"^[A-Z]{1,6}[-_]?\d{3,}")
_REMOTE_TYPES = {
    "hybrid": "hybrid",
    "hybride": "hybrid",
    "remote": "full_remote",
    "teletravail": "full_remote",
    "onsite": "onsite",
    "sur site": "onsite",
}


class Site:
    def __init__(self, careers_url: str) -> None:
        parts = urlsplit(careers_url)
        segments = [s for s in parts.path.split("/") if s and not _LOCALE.match(s)]
        if "myworkdayjobs.com" not in parts.netloc or not segments:
            raise SourceError(f"careers_url Workday inattendue : {careers_url}")
        self.host = parts.netloc
        self.tenant = parts.netloc.split(".")[0]
        self.site = segments[0]
        self.api = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    def public_url(self, external_path: str) -> str:
        return f"https://{self.host}/{self.site}{external_path}"


def posted_days_ago(text: str | None) -> int | None:
    """Days since publication from "Offre publiée hier", "Posted 3 Days Ago"..."""
    if not text:
        return None
    lowered = text.lower()
    if "aujourd" in lowered or "today" in lowered:
        return 0
    if "hier" in lowered or "yesterday" in lowered:
        return 1
    match = re.search(r"(\d+)", lowered)
    return int(match.group(1)) if match else None


def _published_at(day_text: str | None, days_ago: int | None, context: CollectContext) -> datetime | None:
    zone = ZoneInfo(context.config.schedule.timezone)
    today = context.now.astimezone(zone).date()
    candidates = []
    if day_text:
        with suppress(ValueError):
            candidates.append(datetime.fromisoformat(day_text[:10]).date())
    if days_ago is not None:
        candidates.append(today - timedelta(days=days_ago))
    if not candidates:
        return None
    # A reposted offer keeps its first start date: the most recent wins.
    day = min(max(candidates), today)
    return datetime.combine(day, time(0, 0), zone)


def _list_page(site: Site, context: CollectContext, offset: int, facet: tuple[str, str] | None) -> dict:
    body = {
        "appliedFacets": {facet[0]: [facet[1]]} if facet else {},
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }
    response = context.client.post(f"{site.api}/jobs", json=body, headers={"Accept": "application/json"})
    try:
        return response.json()
    except ValueError:
        raise SourceError("réponse Workday illisible (JSON attendu)") from None


def _find_france_facet(facets: list[dict]) -> tuple[str, str] | None:
    """Search the facets of an unfiltered answer for a "France" value."""
    for facet in facets:
        for value in facet.get("values") or []:
            if "facetParameter" in value:
                found = _find_france_facet([value])
                if found:
                    return found
            elif value.get("descriptor", "").strip().lower() == "france" and facet.get("facetParameter"):
                return facet["facetParameter"], value["id"]
    return None


def _details(site: Site, external_path: str, context: CollectContext) -> dict:
    response = context.client.get(f"{site.api}{external_path}", headers={"Accept": "application/json"})
    try:
        return response.json().get("jobPostingInfo") or {}
    except ValueError:
        return {}


def _reference(posting: dict, info: dict) -> str | None:
    if info.get("jobReqId"):
        return info["jobReqId"]
    for field in posting.get("bulletFields") or []:
        if isinstance(field, str) and _REFERENCE.match(field):
            return field
    return None


def _work_mode(info: dict) -> str | None:
    remote = str(info.get("remoteType") or "").lower()
    for key, mode in _REMOTE_TYPES.items():
        if key in remote:
            return mode
    return None


def _offer(site: Site, company: WatchedCompany, posting: dict, info: dict, context: CollectContext) -> Offer:
    path = posting["externalPath"]
    days_ago = posted_days_ago(info.get("postedOn") or posting.get("postedOn"))
    published_at = _published_at(info.get("startDate"), days_ago, context)
    locations = [info.get("location") or posting.get("locationsText") or ""]
    locations += [loc for loc in info.get("additionalLocations") or [] if isinstance(loc, str)]
    description = html_to_text(info.get("jobDescription") or "")
    return Offer(
        source="career_page",
        source_name=f"workday:{company.name}",
        source_offer_id=path,
        url=info.get("externalUrl") or site.public_url(path),
        apply_url=(info.get("externalUrl") or site.public_url(path)) + "/apply",
        reference=_reference(posting, info),
        title=(info.get("title") or posting.get("title") or "").strip(),
        company=company.name,
        location="; ".join(loc for loc in locations if loc) or None,
        description=description,
        description_is_partial=not description,
        contract_type_raw=info.get("timeType"),
        work_mode=_work_mode(info),
        published_at=published_at,
        published_at_source="career_site" if published_at else None,
        first_seen_at=context.now,
        raw_payload={"posting": posting, "details": info},
    )


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    site = Site(company.careers_url)
    source_name = f"workday:{company.name}"
    max_age = context.config.freshness.max_age_days

    facet: tuple[str, str] | None = FRANCE_FACET
    try:
        page = _list_page(site, context, 0, facet)
    except SourceError as error:
        if error.status_code != 400:  # 400: this site rejects the standard filter
            raise
        page = {}
    if not page.get("total"):
        # This site names its country filter differently, or has none
        # (French companies with offers in France only): read it unfiltered.
        page = _list_page(site, context, 0, None)
        facet = _find_france_facet(page.get("facets") or [])
        if facet is not None:
            page = _list_page(site, context, 0, facet)

    offers: list[Offer] = []
    total = int(page.get("total") or 0)
    for page_number in range(MAX_PAGES):
        postings = page.get("jobPostings") or []
        for posting in postings:
            path = posting.get("externalPath")
            if not path:
                continue
            title = posting.get("title") or ""
            wanted = not context.is_known(source_name, path) and title_is_candidate(title, context.config)
            info = _details(site, path, context) if wanted else {}
            country = ((info.get("jobRequisitionLocation") or {}).get("country") or {}).get("alpha2Code")
            if country and country != context.config.location.country:
                continue
            offers.append(_offer(site, company, posting, info, context))

        ages = [posted_days_ago(p.get("postedOn")) for p in postings]
        all_old = bool(ages) and all(age is not None and age > max_age for age in ages)
        offset = (page_number + 1) * PAGE_SIZE
        if all_old or offset >= total or not postings:
            break
        page = _list_page(site, context, offset, facet)
    return offers
