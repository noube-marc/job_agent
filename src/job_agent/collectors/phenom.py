"""Phenom connector (career sites such as careers.allianz.com).

`careers_url` is a search page of the site, for example
"https://careers.allianz.com/global/en/search-results?qcountry=France".
Its first two path segments give the site country and language
("global", "en").

The offers are read through the JSON service that the search page itself
calls (POST /widgets, allowed by the site's robots.txt), filtered on the
country of `location.country`, 100 per request: the whole list of the
country, usually a few requests. Each listed offer has its date, place
and remote work. The offer page (full description, contract) is opened
only for NEW offers whose title matches the search.

Only useful fields are kept from the site's data: it may hold names or
e-mail addresses of recruiters, which are never stored.
"""

import json
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.collectors.jobposting import _date, earliest, job_posting, phenom_created
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.extract import detect_contract
from job_agent.processing.filters import title_is_candidate

PAGE_SIZE = 100
MAX_PAGES = 20
COUNTRY_NAMES = {"FR": "France", "BE": "Belgium", "CH": "Switzerland", "LU": "Luxembourg"}
WORK_MODES = {
    "hybrid working": "hybrid",
    "hybrid": "hybrid",
    "in-office": "onsite",
    "on-site": "onsite",
    "remote": "full_remote",
    "fully remote": "full_remote",
}
_DDO = re.compile(r"phApp\.ddo\s*=\s*(\{.*?\});\s*phApp", re.S)
KEPT_FIELDS = (
    "jobId",
    "title",
    "postedDate",
    "location",
    "city",
    "state",
    "country",
    "remote",
    "employmentType",
    "type",
    "category",
    "employingEntity",
    "jobLevel",
)


def site_parts(careers_url: str) -> tuple[str, str, str]:
    """(host, site country, language) of a Phenom search page address."""
    parts = urlsplit(careers_url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        raise SourceError(f"adresse Phenom inattendue : {careers_url} (attendu : /pays/langue/search-results)")
    return f"{parts.scheme}://{parts.netloc}", segments[0], segments[1]


def search_body(site_country: str, language: str, country_name: str, start: int) -> dict:
    return {
        "lang": f"{language}_{site_country}",
        "country": site_country,
        "ddoKey": "refineSearch",
        "from": start,
        "size": PAGE_SIZE,
        "jobs": True,
        "keywords": "",
        "global": True,
        "selected_fields": {"country": [country_name]},
    }


def page_data(html: str) -> dict:
    """The data that a Phenom page carries for its own scripts."""
    match = _DDO.search(html)
    if not match:
        raise SourceError("données de la page introuvables (structure changée ?)")
    return json.loads(match.group(1))


def _utc(value: str | None) -> datetime | None:
    """ "2026-11-10T09:12:00.000+0000" as an aware datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:19] + value[-5:], "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"phenom:{company.name}"
    host, site_country, language = site_parts(company.careers_url)
    country_name = COUNTRY_NAMES.get(context.config.location.country.upper(), context.config.location.country)
    jobs: list[dict] = []
    for page_number in range(MAX_PAGES):
        answer = context.client.post(
            f"{host}/widgets", json=search_body(site_country, language, country_name, page_number * PAGE_SIZE)
        ).json()
        search = answer.get("refineSearch") or {}
        page = (search.get("data") or {}).get("jobs") or []
        if page_number == 0 and "totalHits" not in search:
            raise SourceError("réponse inattendue du service de recherche (structure changée ?)")
        jobs += page
        if not page or len(jobs) >= int(search.get("totalHits") or 0):
            break

    offers: list[Offer] = []
    seen: set[str] = set()
    for job in jobs:
        job_id = str(job.get("jobId") or "")
        if not job_id or job_id in seen or job.get("country") != country_name:
            continue
        seen.add(job_id)
        title = job.get("title") or ""
        url = f"{host}/{site_country}/{language}/job/{job_id}"
        description, contract, page = "", None, ""
        if not context.is_known(source_name, job_id) and title_is_candidate(title, context.config):
            page = context.client.get(url).text
            detail = (page_data(page).get("jobDetail") or {}).get("data", {}).get("job", {})
            description = html_to_text(detail.get("description") or "")
            contract = detail.get("employmentType")
            # "Temporary" covers internships and fixed-term jobs alike: the
            # contract is then read from the title, the description or the AI.
            if detect_contract(contract) == "interim":
                contract = None
        # "postedDate" is the LAST posting (a reposted offer looks new): the JobPosting date
        # of the offer page is older in that case, and the oldest one is kept.
        # "dateCreated" (creation of the offer) is older still when it was reposted.
        zone = ZoneInfo(context.config.schedule.timezone)
        posting_date = (job_posting(page).get("datePosted") or "") if page else ""
        published = earliest(
            _utc(job.get("postedDate")),
            _date(posting_date, zone),
            _date(job.get("dateCreated"), zone),
            phenom_created(page, zone) if page else None,
        )
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=job_id,
                url=url,
                apply_url=url,
                title=title,
                company=company.name,
                location=job.get("location") or job.get("city"),
                description=description,
                description_is_partial=not description,
                contract_type_raw=contract,
                work_mode=WORK_MODES.get((job.get("remote") or "").strip().lower()),
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={key: job.get(key) for key in KEPT_FIELDS},
            )
        )
    return offers
