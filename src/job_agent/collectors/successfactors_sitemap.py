"""SAP SuccessFactors connector, through the public site map of the career site.

`careers_url` is the career site, for example "https://jobs.limagrain.com".

On these sites the search service (/services/) is forbidden by robots.txt,
but /sitemap.xml lists every open offer, with the city and the title in
its address ("…/job/Chappes-Documentaliste-HF-63720/1372675455/"). The
connector reads the site map at each run (the whole list), and opens the
offer page only for NEW offers whose title, read in the address, matches
the search.

The offer page gives "Label: value" fields (publication date, country,
place, contract) in the language of the posting: French ("Date de début
de publication: 13/10/2026") or English ("Posting Start Date: 3/25/26").
When no country is given, the region of the posting language (fr-FR)
tells it.
"""

import re
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
_JOB = re.compile(r"/job/([^/]+)/(\d+)/?$")
_POSTCODE = re.compile(r"\s+\d{4,5}$")
COUNTRY_NAMES = {"FR": {"france"}, "BE": {"belgique", "belgium"}, "CH": {"suisse", "switzerland"}}
LABELS = {
    "date": ("date de début de publication", "date de publication", "posting start date", "posting date"),
    "country": ("pays", "country"),
    "place": ("localisation", "site de l’emploi", "site de l'emploi", "job location", "location", "lieu"),
    "contract": ("type de contrat", "type of contract", "contract type"),
    "title": ("titre du poste", "job title"),
}


def site_map_url(careers_url: str) -> str:
    parts = urlsplit(careers_url)
    return f"{parts.scheme}://{parts.netloc}/sitemap.xml"


def parse_site_map(xml: str) -> list[dict]:
    """Offers of the site map: id, url, and the words of their address."""
    offers = []
    for raw in _LOC.findall(xml):
        url = raw.replace("&amp;", "&")
        match = _JOB.search(urlsplit(url).path)
        if not match:
            continue
        words = unquote(match.group(1)).replace("-", " ")
        offers.append({"id": match.group(2), "url": url, "words": _POSTCODE.sub("", words).strip()})
    return offers


def _label_of(field_text: str, value: str) -> str:
    label = field_text[: len(field_text) - len(value)] if value and field_text.endswith(value) else field_text
    return label.strip().rstrip(":").strip().lower()


def parse_offer_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    language = ""
    for span in soup.select("span.rtltextaligneligible"):
        language = language or (span.get("lang") or "")
        if span.get("itemprop") == "description":
            continue
        value = span.get_text(" ", strip=True)
        label = _label_of(span.parent.get_text(" ", strip=True), value)
        for key, names in LABELS.items():
            if label in names and key not in fields:
                fields[key] = value
    description = soup.select_one("[itemprop=description]")
    title = soup.select_one("meta[property='og:title']")
    return {
        "fields": fields,
        "language": language,
        "title": fields.get("title") or (title.get("content", "").strip() if title else ""),
        "description": html_to_text(description.decode_contents()) if description else "",
    }


def published(value: str | None, language: str, zone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    patterns = ("%m/%d/%y", "%m/%d/%Y") if language.lower().startswith("en-us") else ("%d/%m/%Y", "%d/%m/%y")
    for pattern in patterns:
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=zone)
        except ValueError:
            continue
    return None


def in_country(details: dict, country: str) -> bool:
    names = COUNTRY_NAMES.get(country, {country.lower()})
    stated = details["fields"].get("country")
    if stated:
        return stated.strip().lower() in names
    region = details["language"].replace("_", "-").split("-")[-1].upper() if "-" in details["language"] else ""
    return region == country if region else True


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"successfactors_sitemap:{company.name}"
    listed = parse_site_map(context.client.get(site_map_url(company.careers_url)).text)
    if not listed:
        raise SourceError("aucune offre dans le plan du site (structure changée ?)")
    country = context.config.location.country.upper()
    zone = ZoneInfo(context.config.schedule.timezone)

    offers: list[Offer] = []
    seen: set[str] = set()
    for item in listed:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        details = {"fields": {}, "language": "", "title": "", "description": ""}
        if context.is_skipped(source_name, item["id"]):
            continue  # another country, seen before
        if not context.is_known(source_name, item["id"]) and title_is_candidate(item["words"], context.config):
            details = parse_offer_page(context.client.get(item["url"]).text)
            if not in_country(details, country):
                context.mark_skipped(source_name, item["id"])
                continue
        fields = details["fields"]
        date = published(fields.get("date"), details["language"], zone)
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=item["id"],
                url=item["url"],
                apply_url=item["url"],
                title=details["title"] or item["words"],
                company=company.name,
                location=fields.get("place"),
                description=details["description"],
                description_is_partial=not details["description"],
                contract_type_raw=fields.get("contract"),
                published_at=date,
                published_at_source="career_site" if date else None,
                first_seen_at=context.now,
                raw_payload={"address_words": item["words"], "fields": fields, "language": details["language"]},
            )
        )
    return offers
