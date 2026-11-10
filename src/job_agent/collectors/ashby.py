"""Ashby connector, through Ashby's documented public job posting API.

`careers_url` is the Ashby job board of the company, for example
"https://jobs.ashbyhq.com/alan". Only offers located in France are kept.
"""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from job_agent.collectors.base import PoliteClient, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"
# Ashby employment types. "FullTime" does not say CDI or CDD: left unknown.
_CONTRACTS = {"Intern": "internship", "Contract": "freelance", "Temporary": "fixed_term"}
_WORK_MODES = {"Remote": "full_remote", "Hybrid": "hybrid", "OnSite": "onsite"}


def board_name(careers_url: str) -> str:
    path = urlsplit(careers_url).path.strip("/")
    if not path:
        raise SourceError(f"careers_url Ashby sans nom de tableau d'offres : {careers_url}")
    return path.split("/")[0]


def _locations(job: dict) -> list[str]:
    names = [job.get("location") or ""]
    country = ((job.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
    names.append(country or "")
    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            names.append(str(secondary.get("location") or ""))
            address = (secondary.get("address") or {}).get("postalAddress") or {}
            names.append(str(address.get("addressCountry") or ""))
        else:
            names.append(str(secondary))
    return [name for name in names if name]


def is_in_france(job: dict) -> bool:
    return any("france" in name.lower() or "paris" in name.lower() for name in _locations(job))


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_board(data: dict, company: WatchedCompany, now: datetime) -> list[Offer]:
    offers = []
    for job in data.get("jobs") or []:
        if job.get("isListed") is False or not is_in_france(job):
            continue
        published_at = _published(job.get("publishedAt"))
        description = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
        employment = job.get("employmentType")
        offers.append(
            Offer(
                source="career_page",
                source_name=f"ashby:{company.name}",
                source_offer_id=str(job["id"]),
                url=job.get("jobUrl") or "",
                apply_url=job.get("applyUrl"),
                title=(job.get("title") or "").strip(),
                company=company.name,
                location=job.get("location"),
                description=description,
                contract_type=_CONTRACTS.get(employment or ""),
                contract_type_raw=employment,
                work_mode=_WORK_MODES.get(job.get("workplaceType") or ""),
                published_at=published_at,
                published_at_source="career_site" if published_at else None,
                first_seen_at=now,
                raw_payload=job,
            )
        )
    return offers


def collect(company: WatchedCompany, client: PoliteClient) -> list[Offer]:
    response = client.get(API_URL.format(board=board_name(company.careers_url)))
    try:
        data = response.json()
    except ValueError:
        raise SourceError("réponse Ashby illisible (JSON attendu)") from None
    return parse_board(data, company, datetime.now(timezone.utc))
