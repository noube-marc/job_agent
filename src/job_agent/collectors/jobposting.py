"""Generic connector: site map of offers + schema.org JobPosting data.

`careers_url` is the site map that lists the offers of a career site, for
example "https://emplois.lidl.fr/sitemap-jobs.xml". A site map index is
followed one level down.

Many career sites publish a site map of their offers (made for robots) and
describe each offer page with the standard schema.org "JobPosting" data
(the format read by search engines): title, publication date, contract,
place, description. The connector reads the site map at each run (the
whole list) and opens an offer page only for NEW offers whose title, read
in the address, matches the search. When the address holds no words (a
mere code, as "/offre/tq3g3vmorv"), every new offer page is opened.
"""

import json
import re
from datetime import datetime
from urllib.parse import unquote, urlsplit

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

_URL_BLOCK = re.compile(r"<(url|sitemap)>(.*?)</\1>", re.S)
_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
_LD_JSON = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_TRAILING_ID = re.compile(r"[-_/]?(\d{4,})/?$")
COUNTRY_CODES = {"FR": {"fr", "fra", "france"}, "BE": {"be", "bel", "belgique", "belgium"}}
CONTRACTS = {"FULL_TIME": None, "PART_TIME": None, "CONTRACTOR": "freelance", "TEMPORARY": None,
             "INTERN": "stage", "PER_DIEM": None, "VOLUNTEER": None, "OTHER": None}


def parse_site_map(xml: str) -> tuple[list[dict], list[str]]:
    """(offers listed: id, url, words of the address; nested site maps)."""
    offers, nested = [], []
    for kind, block in _URL_BLOCK.findall(xml):
        match = _LOC.search(block)
        if not match:
            continue
        url = match.group(1).replace("&amp;", "&")
        if kind == "sitemap":
            nested.append(url)
            continue
        path = urlsplit(url).path.rstrip("/")
        last = re.sub(r"\.html?$", "", unquote(path.rsplit("/", 1)[-1]))
        found = _TRAILING_ID.search(path)
        offer_id = found.group(1) if found else path
        words = _TRAILING_ID.sub("", last).replace("-", " ").replace("_", " ").strip()
        offers.append({"id": offer_id, "url": url, "words": words})
    return offers, nested


def job_posting(html: str) -> dict:
    """The schema.org JobPosting of an offer page ({} if none)."""
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def _address(posting: dict) -> dict:
    location = posting.get("jobLocation") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    address = location.get("address") or {}
    return address if isinstance(address, dict) else {}


def _country_code(address: dict) -> str:
    country = address.get("addressCountry") or ""
    if isinstance(country, dict):
        country = country.get("name") or country.get("identifier") or ""
    return str(country).strip().lower()


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _contract(posting: dict) -> str | None:
    kinds = posting.get("employmentType") or []
    for kind in [kinds] if isinstance(kinds, str) else kinds:
        label = CONTRACTS.get(str(kind).upper(), str(kind))
        if label:
            return label
    return None


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"jobposting:{company.name}"
    listed, nested = parse_site_map(context.client.get(company.careers_url).text)
    for url in nested[:20]:
        listed += parse_site_map(context.client.get(url).text)[0]
    if not listed:
        raise SourceError("aucune offre dans le plan du site (structure changée ?)")
    country = context.config.location.country.upper()

    offers: list[Offer] = []
    seen: set[str] = set()
    for item in listed:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        posting: dict = {}
        opaque = " " not in item["words"]  # an address without words: the title is only on the page
        if not context.is_known(source_name, item["id"]) and (opaque or title_is_candidate(item["words"], context.config)):
            posting = job_posting(context.client.get(item["url"]).text)
        address = _address(posting)
        code = _country_code(address)
        if code and code not in COUNTRY_CODES.get(country, {country.lower()}):
            continue
        place = " ".join(str(address.get(key) or "") for key in ("postalCode", "addressLocality")).strip()
        published = _date(posting.get("datePosted"))
        description = html_to_text(posting.get("description") or "")
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=item["id"],
                url=item["url"],
                apply_url=item["url"],
                title=(posting.get("title") or item["words"]).strip(),
                company=company.name,
                location=place or None,
                description=description,
                description_is_partial=not description,
                contract_type_raw=_contract(posting),
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={"address_words": item["words"], "datePosted": posting.get("datePosted"),
                             "employmentType": posting.get("employmentType"), "address": address},
            )
        )
    return offers
