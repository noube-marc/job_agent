"""Cornerstone OnDemand connector (career sites "xxx.csod.com").

`careers_url` is the home page of the career site, for example
"https://tereos.csod.com/ux/ats/careersite/1/home?c=tereos&lang=fr-FR".

The career page hands every visitor an anonymous session token and the
address of the job search service; the connector reads them from the page
and calls the service as the page does, 100 offers per request: the whole
list, with the full description, the date and the place of each offer.
No offer page needs to be opened. The token is never logged nor stored.

robots.txt of the career sites allows everything, with a 10 s pause,
which the client respects.
"""

import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer

PAGE_SIZE = 100
MAX_PAGES = 10
_SITE = re.compile(r"/careersite/(\d+)")
_TOKEN = re.compile(r'"token"\s*:\s*"([^"]+)"')
_CLOUD = re.compile(r'"cloud"\s*:\s*"([^"]+)"')
_CULTURE = re.compile(r'"cultureID"\s*:\s*(\d+)')


def site_parts(careers_url: str) -> tuple[str, int, str, str]:
    """(host, career site id, company code, language) of a career site address."""
    parts = urlsplit(careers_url)
    match = _SITE.search(parts.path)
    query = parse_qs(parts.query)
    if not match or "c" not in query:
        raise SourceError(f"adresse Cornerstone inattendue : {careers_url} (attendu : …/careersite/N/home?c=…)")
    language = (query.get("lang") or ["fr-FR"])[0]
    return f"{parts.scheme}://{parts.netloc}", int(match.group(1)), query["c"][0], language


def session(page_html: str) -> tuple[str, str, int]:
    """(token, search service address, culture id) handed out by the career page."""
    token, cloud, culture = _TOKEN.search(page_html), _CLOUD.search(page_html), _CULTURE.search(page_html)
    if not token or not cloud:
        raise SourceError("jeton de recherche introuvable dans la page (structure changée ?)")
    return token.group(1), cloud.group(1).rstrip("/") + "/", int(culture.group(1)) if culture else 1


def search_body(site_id: int, culture_id: int, language: str, page: int) -> dict:
    return {
        "careerSiteId": site_id,
        "careerSitePageId": site_id,
        "pageNumber": page,
        "pageSize": PAGE_SIZE,
        "cultureId": culture_id,
        "searchText": "",
        "cultureName": language,
        "states": [],
        "countryCodes": [],
        "cities": [],
        "placeID": "",
        "radius": None,
        "postingsWithinDays": None,
        "customFieldCheckboxKeys": [],
        "customFieldDropdowns": [],
        "customFieldRadios": [],
    }


def _published(value: str | None, zone: ZoneInfo) -> datetime | None:
    try:
        day = datetime.strptime((value or "").strip(), "%d/%m/%Y")
    except ValueError:
        return None
    return day.replace(tzinfo=zone)


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"cornerstone:{company.name}"
    host, site_id, code, language = site_parts(company.careers_url)
    token, service, culture_id = session(context.client.get(company.careers_url).text)
    zone = ZoneInfo(context.config.schedule.timezone)
    country = context.config.location.country.upper()

    requisitions: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        answer = context.client.post(
            f"{service}rec-job-search/external/jobs",
            json=search_body(site_id, culture_id, language, page),
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        data = answer.get("data") or {}
        if page == 1 and "requisitions" not in data:
            raise SourceError("réponse inattendue du service de recherche (structure changée ?)")
        batch = data.get("requisitions") or []
        requisitions += batch
        if not batch or len(requisitions) >= int(data.get("totalCount") or 0):
            break

    offers: list[Offer] = []
    for item in requisitions:
        locations = item.get("locations") or []
        if locations and not any((place.get("country") or "").upper() == country for place in locations):
            continue
        offer_id = str(item.get("requisitionId") or "")
        if not offer_id:
            continue
        place = next((p for p in locations if (p.get("country") or "").upper() == country), {})
        url = f"{host}/ux/ats/careersite/{site_id}/home/requisition/{offer_id}?c={code}&lang={language}"
        published = _published(item.get("postingEffectiveDate"), zone)
        description = html_to_text(item.get("externalDescription") or "")
        title = (item.get("displayJobTitle") or "").strip()
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=offer_id,
                url=url,
                apply_url=url,
                title=title,
                company=company.name,
                location=(place.get("city") or "").title() or None,
                description=description,
                description_is_partial=not description,
                contract_type_raw=title,  # Cornerstone gives no contract field: read in the title
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={
                    "requisitionId": offer_id,
                    "date": item.get("postingEffectiveDate"),
                    "locations": locations,
                    "title": title,
                },
            )
        )
    return offers
