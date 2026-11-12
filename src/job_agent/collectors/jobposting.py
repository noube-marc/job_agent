"""Generic connector: site map of offers + schema.org JobPosting data.

`careers_url` is the site map that lists the offers of a career site, for
example "https://emplois.lidl.fr/sitemap-jobs.xml". A site map index is
followed one level down.

Many career sites publish a site map of their offers (made for robots) and
describe each offer page with the standard schema.org "JobPosting" data
(the format read by search engines): title, publication date, contract,
place, description. Some sites put a republication date in "datePosted"
(Isagri: 05/11 while the page says "Publié le 16/10/2026"): the oldest of
that date and the date written on the page is kept. The connector reads the site map at each run (the
whole list) and opens an offer page only for NEW offers whose title, read
in the address, matches the search. When the address holds no words (a
mere code, as "/offre/tq3g3vmorv"), every new offer page is opened.
"""

import json
import re
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

_URL_BLOCK = re.compile(r"<(url|sitemap)>(.*?)</\1>", re.S)
_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
_LD_JSON = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_ID_SEGMENT = re.compile(r"^\d{4,}$")
_EDGE_ID = re.compile(r"^(\d{4,})-|-(\d{4,})$")
COUNTRY_CODES = {"FR": {"fr", "fra", "france"}, "BE": {"be", "bel", "belgique", "belgium"}}
CONTRACTS = {
    "FULL_TIME": None,
    "PART_TIME": None,
    "CONTRACTOR": "freelance",
    "TEMPORARY": None,
    "INTERN": "stage",
    "PER_DIEM": None,
    "VOLUNTEER": None,
    "OTHER": None,
}


def _address_parts(url: str) -> tuple[str | None, str]:
    """(offer id found in the address or None, words of the address).

    The id is a number of 4 digits or more: the last segment
    ("/3441/46789675648"), or the start or end of the last segment
    ("/148324-boucher-vichy", "/equipier-lyon-749288"). The words come from
    the last segment that is not a number ("/emploi/paris/data-analyst/3441/123").
    """
    segments = [re.sub(r"\.html?$", "", unquote(s)) for s in urlsplit(url).path.split("/") if s]
    if not segments:
        return None, ""
    offer_id = None
    if _ID_SEGMENT.match(segments[-1]):
        offer_id = segments[-1]
    else:
        edge = _EDGE_ID.search(segments[-1])
        if edge:
            offer_id = edge.group(1) or edge.group(2)
    textual = [s for s in segments if not s.isdigit()]
    words = _EDGE_ID.sub("", textual[-1]) if textual else ""
    return offer_id, words.replace("-", " ").replace("_", " ").strip()


def parse_site_map(xml: str) -> tuple[list[dict], list[str]]:
    """(offers listed: id, url, words of the address; nested site maps).

    When some addresses carry an offer id, only those are offers (the
    others are category or presentation pages). A site whose offer
    addresses carry no number at all ("/offre/tq3g3vmorv") keeps them all.
    """
    entries, nested = [], []
    for kind, block in _URL_BLOCK.findall(xml):
        match = _LOC.search(block)
        if not match:
            continue
        url = match.group(1).replace("&amp;", "&")
        if kind == "sitemap":
            nested.append(url)
            continue
        offer_id, words = _address_parts(url)
        entries.append({"id": offer_id, "url": url, "words": words})
    if any(entry["id"] for entry in entries):
        entries = [entry for entry in entries if entry["id"]]
    for entry in entries:
        entry["id"] = entry["id"] or urlsplit(entry["url"]).path
    return entries, nested


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


_VISIBLE_DATE = re.compile(
    r"(?:publi[ée]+e?\s+le|date\s+de\s+publication\s*:?)\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE
)


def visible_date(html: str, zone: ZoneInfo) -> datetime | None:
    """The publication date written on the page ("Publié le 16/10/2026"), if any."""
    match = _VISIBLE_DATE.search(re.sub(r"<[^>]+>", " ", html))
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, tzinfo=zone)
    except ValueError:
        return None


def earliest(*dates: datetime | None) -> datetime | None:
    """The oldest known date: a republished offer must never look new."""
    known = [value for value in dates if value is not None]
    return min(known) if known else None


def _date(value: str | None, zone: ZoneInfo) -> datetime | None:
    """A JobPosting date; a date without time zone ("2026-11-10") is local."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


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
        page = ""
        opaque = " " not in item["words"]  # an address without words: the title is only on the page
        if context.is_skipped(source_name, item["id"]):
            continue  # another country, seen before
        if not context.is_known(source_name, item["id"]) and (
            opaque or title_is_candidate(item["words"], context.config)
        ):
            page = context.client.get(item["url"]).text
            posting = job_posting(page)
            if not posting:
                continue  # not an offer page (or an offer already removed)
        address = _address(posting)
        code = _country_code(address)
        if code and code not in COUNTRY_CODES.get(country, {country.lower()}):
            context.mark_skipped(source_name, item["id"])
            continue
        place = " ".join(str(address.get(key) or "") for key in ("postalCode", "addressLocality")).strip()
        zone = ZoneInfo(context.config.schedule.timezone)
        # "datePosted" may be a republication date: the date written on the page wins if older.
        published = earliest(_date(posting.get("datePosted"), zone), visible_date(page, zone))
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
                raw_payload={
                    "address_words": item["words"],
                    "datePosted": posting.get("datePosted"),
                    "employmentType": posting.get("employmentType"),
                    "address": address,
                },
            )
        )
    return offers
