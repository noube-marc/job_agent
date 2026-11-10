"""SAP SuccessFactors connector, through the public HTML search page.

`careers_url` is the career site, optionally with its own search filters,
for example "https://carrieres.generali.fr" or
"https://jobdetails.nestle.com/search/?q=&locationsearch=France".

The search page is read newest first (10 or 20 offers per page, depending
on the site). Reading stops
at the first page that holds only offers already known (usually the
first page after the first run). The offer page (full text, date,
contract) is opened only for NEW offers whose title matches the search.
"""

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.extract import detect_contract
from job_agent.processing.filters import title_is_candidate

MAX_PAGES = 15
_OFFER_ID = re.compile(r"/(\d{5,})/?$")
# "ST DENIS, FR, 93210": the two-letter country code of the location.
_COUNTRY = re.compile(r",\s*([A-Z]{2})\s*(?:,|$)")
_IGNORED_PROPERTIES = {"title", "description", "location", "adcode"}


def search_url(careers_url: str, offset: int) -> str:
    parts = urlsplit(careers_url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params.setdefault("q", "")
    params.update({"sortColumn": "referencedate", "sortDirection": "desc", "startrow": str(offset)})
    return f"{parts.scheme}://{parts.netloc}/search/?{urlencode(params)}"


def _country(location: str) -> str | None:
    match = _COUNTRY.search(location or "")
    return match.group(1) if match else None


def parse_search_page(html: str, base_url: str) -> list[dict]:
    """Offers listed on a search page: id, title, location, url."""
    soup = BeautifulSoup(html, "html.parser")
    offers: dict[str, dict] = {}
    for row in soup.select("tr.data-row"):
        link = row.select_one("a.jobTitle-link")
        if link is None or not link.get("href"):
            continue
        url = urljoin(base_url, link["href"])
        match = _OFFER_ID.search(urlsplit(url).path)
        offer_id = match.group(1) if match else urlsplit(url).path
        location = row.select_one("td.colLocation .jobLocation") or row.select_one(".jobLocation")
        offers.setdefault(offer_id, {
            "id": offer_id,
            "title": link.get_text(" ", strip=True),
            "location": location.get_text(" ", strip=True) if location else None,
            "url": url,
        })
    return list(offers.values())


def _posted_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for pattern in ("%a %b %d %H:%M:%S %Z %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_offer_page(html: str) -> dict:
    """Details of an offer page: description, date, contract, reference."""
    soup = BeautifulSoup(html, "html.parser")
    properties = {
        element.get("data-careersite-propertyid"): element.get_text(" ", strip=True)
        for element in soup.select("[data-careersite-propertyid]")
    }
    description = soup.select_one("[itemprop=description]") or soup.select_one(".jobdescription")
    date = soup.select_one("meta[itemprop=datePosted]")
    contract = next(
        (value for key, value in properties.items()
         if key not in _IGNORED_PROPERTIES and detect_contract(value)),
        None,
    )
    return {
        "description": html_to_text(description.decode_contents()) if description else "",
        "published_at": _posted_date(date.get("content") if date else None),
        "contract": contract,
        "reference": properties.get("adcode"),
        "location": properties.get("location"),
        "properties": properties,
    }


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"successfactors:{company.name}"
    country = context.config.location.country
    offers: list[Offer] = []
    seen: set[str] = set()
    offset = 0

    for page_number in range(MAX_PAGES):
        url = search_url(company.careers_url, offset)
        listed = parse_search_page(context.client.get(url).text, url)
        offset += len(listed)
        if page_number == 0 and not listed:
            raise SourceError("aucune offre lisible sur la page de recherche (structure changée ?)")
        new_on_page = [item for item in listed if item["id"] not in seen]
        if not new_on_page:
            break
        all_known = True
        for item in new_on_page:
            seen.add(item["id"])
            item_country = _country(item["location"] or "")
            if item_country and item_country != country:
                continue
            known = context.is_known(source_name, item["id"])
            all_known = all_known and known
            details: dict = {}
            if not known and title_is_candidate(item["title"], context.config):
                details = parse_offer_page(context.client.get(item["url"]).text)
            contract_raw = details.get("contract") or (item["title"] if detect_contract(item["title"]) else None)
            published_at = details.get("published_at")
            offers.append(
                Offer(
                    source="career_page",
                    source_name=source_name,
                    source_offer_id=item["id"],
                    url=item["url"],
                    apply_url=item["url"],
                    reference=details.get("reference"),
                    title=item["title"],
                    company=company.name,
                    location=details.get("location") or item["location"],
                    description=details.get("description", ""),
                    description_is_partial=not details.get("description"),
                    contract_type_raw=contract_raw,
                    published_at=published_at,
                    published_at_source="career_site" if published_at else None,
                    first_seen_at=context.now,
                    raw_payload={"listing": item, "properties": details.get("properties", {})},
                )
            )
        if all_known:
            break
    return offers
