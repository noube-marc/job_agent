"""Oracle Recruiting Cloud connector (career sites "…/hcmUI/CandidateExperience/…").

`careers_url` is the jobs page of the career site, for example
"https://eoct.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/fr/sites/careers/jobs".

The page gives its site number ("CX_2005"); the offers are read through the
public search service that the page itself calls (hcmRestApi
recruitingCEJobRequisitions), newest first, 100 per request: the whole
list, with title, date and country. The details (description,
qualifications, exact date, place, remote work) are read only for NEW
offers in the configured country whose title matches the search.
"""

import re
from datetime import datetime
from urllib.parse import urlsplit

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

PAGE_SIZE = 100
MAX_PAGES = 20
_SITE_NUMBER = re.compile(r"siteNumber=(CX_\d+)")
_LANGUAGE = re.compile(r"/CandidateExperience/([a-z]{2})/")
_DEPARTMENT = re.compile(r",\s*(\d{2,3}|2[AB])-")
WORK_MODES = {"ORA_ON_SITE": "onsite", "ORA_HYBRID": "hybrid", "ORA_REMOTE": "full_remote"}


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"oracle:{company.name}"
    parts = urlsplit(company.careers_url)
    host = f"{parts.scheme}://{parts.netloc}"
    found = _SITE_NUMBER.search(context.client.get(company.careers_url).text)
    if not found:
        raise SourceError("numéro du site carrière introuvable dans la page (structure changée ?)")
    site = found.group(1)
    language = (_LANGUAGE.search(parts.path) or [None, "fr"])[1]
    api = f"{host}/hcmRestApi/resources/latest"
    country = context.config.location.country.upper()

    requisitions: list[dict] = []
    for page in range(MAX_PAGES):
        answer = context.client.get(
            f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
            f"&finder=findReqs;siteNumber={site},limit={PAGE_SIZE},offset={page * PAGE_SIZE},sortBy=POSTING_DATES_DESC"
        ).json()
        search = (answer.get("items") or [{}])[0]
        if page == 0 and "TotalJobsCount" not in search:
            raise SourceError("réponse inattendue du service de recherche (structure changée ?)")
        batch = search.get("requisitionList") or []
        requisitions += batch
        if not batch or len(requisitions) >= int(search.get("TotalJobsCount") or 0):
            break

    offers: list[Offer] = []
    for item in requisitions:
        if (item.get("PrimaryLocationCountry") or "").upper() != country:
            continue
        offer_id = str(item.get("Id") or "")
        if not offer_id:
            continue
        title = item.get("Title") or ""
        detail: dict = {}
        if not context.is_known(source_name, offer_id) and title_is_candidate(title, context.config):
            answer = context.client.get(
                f"{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                f'&finder=ById;Id="{offer_id}",siteNumber={site}'
            ).json()
            detail = (answer.get("items") or [{}])[0]
        parts_text = [
            html_to_text(detail.get(key) or "")
            for key in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
        ]
        description = "\n".join(part for part in parts_text if part)
        place = detail.get("PrimaryLocation") or item.get("PrimaryLocation")
        code = _DEPARTMENT.search(place or "")
        published = _date(detail.get("ExternalPostedStartDate")) or _date(
            item.get("PostedDate") and item["PostedDate"] + "T00:00:00+00:00"
        )
        url = f"{host}/hcmUI/CandidateExperience/{language}/sites/{site}/job/{offer_id}"
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=offer_id,
                url=url,
                apply_url=url,
                title=title,
                company=company.name,
                location=place,
                department_code=code.group(1) if code else None,
                description=description,
                description_is_partial=not description,
                contract_type_raw=title,  # no contract field: read in the title, then the description
                work_mode=WORK_MODES.get(detail.get("WorkplaceTypeCode") or item.get("WorkplaceTypeCode") or ""),
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={
                    "Id": offer_id,
                    "PostedDate": item.get("PostedDate"),
                    "country": item.get("PrimaryLocationCountry"),
                },
            )
        )
    return offers
