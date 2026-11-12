"""Digital Recruiters connector (career sites such as recrute.groupe-casino.fr).

`careers_url` is the offers page of the career site, for example
"https://recrute.groupe-casino.fr/fr/annonces".

The offers are read through the public service of Digital Recruiters that
the page itself uses (api.digitalrecruiters.com/public/v1), 100 per
request: the whole list, with title, contract and city. The offer details
(description, profile, experience, address, date) are read only for NEW
offers whose title matches the search.

A group site may gather the offers of several brands (Monoprix,
Franprix...): the link given to the user is the offer page on the brand's
own career site. The date given by the service is the date of the last
(re)publication of the offer.
"""

from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

API = "https://api.digitalrecruiters.com/public/v1/careers-site/job-ads"
PAGE_SIZE = 100
MAX_PAGES = 30
COUNTRY_NAMES = {"FR": "France", "BE": "Belgique", "LU": "Luxembourg", "CH": "Suisse"}


def site_parts(careers_url: str) -> tuple[str, str, str]:
    """(domain name, locale for the service, language of the pages)."""
    parts = urlsplit(careers_url)
    segments = [segment for segment in parts.path.split("/") if segment]
    language = segments[0] if segments and len(segments[0]) == 2 else "fr"
    return parts.netloc, f"{language}_{language.upper()}", language


def _published(value: str | None, zone: ZoneInfo) -> datetime | None:
    try:
        return datetime.strptime((value or "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
    except ValueError:
        return None


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"digitalrecruiters:{company.name}"
    domain, locale, language = site_parts(company.careers_url)
    zone = ZoneInfo(context.config.schedule.timezone)
    country = COUNTRY_NAMES.get(context.config.location.country.upper(), context.config.location.country)

    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        answer = context.client.post(
            f"{API}?domainName={domain}&limit={PAGE_SIZE}&page={page}&locale={locale}", json={"filters": {}}
        ).json()
        if page == 1 and "items" not in answer:
            raise SourceError("réponse inattendue du service Digital Recruiters (structure changée ?)")
        batch = answer.get("items") or []
        items += batch
        if not batch or len(items) >= int(answer.get("count") or 0):
            break

    offers: list[Offer] = []
    seen: set[str] = set()
    for item in items:
        job_ad_id = str(item.get("job_ad_id") or "")
        offer_id = str(item.get("id") or job_ad_id)
        if not job_ad_id or offer_id in seen:
            continue
        seen.add(offer_id)
        title = item.get("title") or ""
        detail: dict = {}
        if not context.is_known(source_name, offer_id) and title_is_candidate(title, context.config):
            detail = context.client.get(f"{API}/{job_ad_id}?domainName={domain}&locale={locale}").json()
        address = detail.get("address") or {}
        if address.get("country") and address["country"] != country:
            continue
        parts = [html_to_text(detail.get(key) or "") for key in ("description", "profile")]
        parts += [detail.get(key) or "" for key in ("job_experience", "education_level")]
        description = "\n".join(part for part in parts if part)
        url = f"https://{item.get('career_domain') or domain}/{language}/annonce/{item.get('url') or job_ad_id}"
        published = _published(detail.get("republished_at"), zone)
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=offer_id,
                url=url,
                apply_url=url,
                title=title,
                company=company.name,
                location=detail.get("location") or item.get("location"),
                description=description,
                description_is_partial=not description,
                contract_type_raw=item.get("contract") or detail.get("contract"),
                experience_raw=detail.get("job_experience"),
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={
                    "listing": {k: item.get(k) for k in ("id", "title", "contract", "location", "career_domain")},
                    "brand": detail.get("brand_name"),
                },
            )
        )
    return offers
