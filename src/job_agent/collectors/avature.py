"""Avature connector, through the public HTML search page.

`careers_url` is the search page of the career site, for example
"https://jobs.totalenergies.com/fr_FR/careers/SearchJobs/".

The search page lists the offers of every country, newest first, 20 per
page, with their date, country and contract. Pages are read until a whole
page is older than `freshness.max_age_days`, so every new offer is seen at
each run. Only offers in the country of `location.country` are kept. The
offer page (full text, city, experience) is opened only for NEW offers
whose title matches the search.

robots.txt of these sites allows /careers pages but not addresses with a
"qtvc" parameter: the connector never uses it.
"""

import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate
from job_agent.text import plain

PAGE_SIZE = 20
MAX_PAGES = 20
_OFFER_ID = re.compile(r"/(\d+)/?$")
_AREA = re.compile(r"\bArea\s+(\d{2,3}|2[AB])\b", re.IGNORECASE)
# Country names shown by the French version of the sites.
COUNTRY_NAMES = {"FR": "france", "BE": "belgique", "CH": "suisse", "LU": "luxembourg"}


def search_url(careers_url: str, offset: int) -> str:
    parts = urlsplit(careers_url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}?jobRecordsPerPage={PAGE_SIZE}&jobOffset={offset}"


def _text(element) -> str:
    return element.get_text(" ", strip=True) if element is not None else ""


def parse_search_page(html: str) -> list[dict]:
    """Offers listed on a search page: id, title, url, date, country, contract."""
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for article in soup.select("div.article--result"):
        link = article.select_one("a.link[href*='JobDetail']") or article.select_one("h3 a[href]")
        if link is None:
            continue
        url = link["href"]
        match = _OFFER_ID.search(urlsplit(url).path)
        if not match:
            continue
        raw_date = _text(article.select_one(".list-item-jobCreationDate"))
        try:
            date = datetime.strptime(raw_date, "%d-%m-%Y").date()
        except ValueError:
            date = None
        offers.append(
            {
                "id": match.group(1),
                "title": _text(link),
                "url": url,
                "date": date,
                "country": _text(article.select_one(".list-item-jobCountry")),
                "contract": _text(article.select_one(".list-item-employmentType")) or None,
                "employer": _text(article.select_one(".list-item-jobEmployerCompany")) or None,
            }
        )
    return offers


def parse_offer_page(html: str) -> dict:
    """Labelled fields of an offer page, and its description."""
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    texts: list[str] = []
    for field in soup.select(".article__content__view__field"):
        label = field.select_one(".article__content__view__field__label")
        value = field.select_one(".article__content__view__field__value")
        if value is None:
            continue
        if label is not None and _text(label):
            fields[_text(label).rstrip(" :")] = _text(value)
        else:
            texts.append(html_to_text(value.decode_contents()))
    area = None
    for value in [*fields.values(), *(t[:80] for t in texts)]:
        match = _AREA.search(value)
        if match:
            area = match.group(1).upper()
            break
    # The first long text is the offer; the next ones are the group's presentation.
    description = next((t for t in texts if len(t) > 200), "")
    return {"fields": fields, "description": description, "department_code": area}


def _published(day, timezone: str) -> datetime | None:
    if day is None:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(timezone))


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"avature:{company.name}"
    max_age = context.config.freshness.max_age_days
    country = COUNTRY_NAMES.get(context.config.location.country.upper())
    timezone = context.config.schedule.timezone
    offers: list[Offer] = []
    seen: set[str] = set()

    for page_number in range(MAX_PAGES):
        listed = parse_search_page(context.client.get(search_url(company.careers_url, page_number * PAGE_SIZE)).text)
        if page_number == 0 and not listed:
            raise SourceError("aucune offre lisible sur la page de recherche (structure changée ?)")
        new_on_page = [item for item in listed if item["id"] not in seen]
        if not new_on_page:
            break
        for item in new_on_page:
            seen.add(item["id"])
            if country and plain(item["country"]) != country:
                continue
            details: dict = {"fields": {}, "description": "", "department_code": None}
            if not context.is_known(source_name, item["id"]) and title_is_candidate(item["title"], context.config):
                details = parse_offer_page(context.client.get(item["url"]).text)
            fields = details["fields"]
            city = fields.get("Ville")
            code = details["department_code"]
            location = f"{city} ({code})" if city and code else city or item["country"]
            published = _published(item["date"], timezone)
            offers.append(
                Offer(
                    source="career_page",
                    source_name=source_name,
                    source_offer_id=item["id"],
                    url=item["url"],
                    apply_url=item["url"],
                    title=item["title"],
                    company=company.name,
                    location=location,
                    department_code=code,
                    description=details["description"],
                    description_is_partial=not details["description"],
                    contract_type_raw=fields.get("Type de contrat") or item["contract"],
                    experience_raw=fields.get("Expérience"),
                    published_at=published,
                    published_at_source="career_site" if published else None,
                    first_seen_at=context.now,
                    raw_payload={"listing": {k: str(v) for k, v in item.items()}, "fields": fields},
                )
            )
        ages = [(context.now.date() - item["date"]).days for item in listed if item["date"]]
        if ages and min(ages) > max_age:
            break
    return offers
