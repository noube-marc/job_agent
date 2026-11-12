"""Connector for the Crédit Agricole group job site (groupecreditagricole.jobs).

`careers_url` is the offers page of one brand of the group, for example
"https://groupecreditagricole.jobs/fr/nos-marques/credit-agricole-assurances/nos-offres/".

The pages "…/nos-offres/", "…/nos-offres/page/2/"… list the offers of
the brand (32 per page), newest update first, with title, contract, place
and country. They are read until a page holds only known offers: new
offers always come first. The list only shows an UPDATE date: the real
publication date is on the offer page ("Publiée le 03/11/2025"), opened
only for NEW offers whose title matches the search, with the full
description and the application criteria.

`careers_url` may also be the page of the whole group (".../fr/nos-offres/"):
each offer then takes the name of its brand (LCL, Crédit Agricole CIB...),
and the brands watched separately with this connector are skipped.

robots.txt only forbids the WordPress administration pages.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate
from job_agent.text import plain

# The whole group page holds about 1300 offers (41 pages).
MAX_PAGES = 60
COUNTRY_NAMES = {"FR": "France", "BE": "Belgique", "LU": "Luxembourg", "CH": "Suisse"}
_PUBLISHED = re.compile(r"Publi[ée]+e? le\s*(\d{2}/\d{2}/\d{4})")
_REFERENCE = re.compile(r"reference--([0-9-]+?)-*/?$")


def page_url(careers_url: str, page: int) -> str:
    base = careers_url if careers_url.endswith("/") else careers_url + "/"
    return base if page == 1 else f"{base}page/{page}/"


def parse_list_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for card in soup.select("article.card.offer"):
        link = card.select_one("h3.offer-title a[href]") or card.select_one("a[href*='/nos-offres-emploi/']")
        if link is None:
            continue
        url = link["href"]
        favorite = card.select_one("input[data-reference]")
        match = _REFERENCE.search(url)
        reference = favorite["data-reference"] if favorite else (match.group(1) if match else url)
        city = card.get("data-gtm-jobcity") or ""
        offers.append(
            {
                "id": reference,
                "title": card.get("data-gtm-jobtitle") or link.get_text(" ", strip=True),
                "url": url,
                "contract": card.get("data-gtm-jobcontract") or None,
                "country": card.get("data-gtm-jobcountry") or "",
                "city": re.sub(r"\s*-\s*[^-]+$", "", city).strip() or None,
                "entity": card.get("data-gtm-jobentity") or None,
            }
        )
    return offers


def parse_offer_page(html: str) -> dict:
    """Publication date, description and application criteria of an offer page."""
    soup = BeautifulSoup(html, "html.parser")
    # Several ".publication-date" lines may exist ("Modifiée le …", then "Publiée le …").
    dates = " ".join(element.get_text(" ", strip=True) for element in soup.select(".publication-date"))
    match = _PUBLISHED.search(dates)
    parts = []
    content = soup.select_one("section.offer-content")
    if content is not None:
        parts.append(html_to_text(content.decode_contents()))
    criteria = soup.select_one("section.accordions .accordion-item-content")
    if criteria is not None:
        parts.append(html_to_text(criteria.decode_contents()))
    return {
        "published": datetime.strptime(match.group(1), "%d/%m/%Y").date() if match else None,
        "description": "\n".join(part for part in parts if part),
    }


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"credit_agricole_jobs:{company.name}"
    country = COUNTRY_NAMES.get(context.config.location.country.upper(), context.config.location.country)
    zone = ZoneInfo(context.config.schedule.timezone)
    listed: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        items = [
            item
            for item in parse_list_page(context.client.get(page_url(company.careers_url, page)).text)
            if item["id"] not in seen
        ]
        if page == 1 and not items:
            raise SourceError("aucune offre lisible sur la page des offres (structure changée ?)")
        if not items:
            break
        seen.update(item["id"] for item in items)
        listed += items
        # The list is sorted by update date, newest first: new offers are on the
        # first pages. A page holding only known offers ends the reading.
        if all(context.is_known(source_name, item["id"]) for item in items):
            break

    offers: list[Offer] = []
    # Brands watched on their own with this connector are not read twice.
    others = {
        plain(other.name)
        for other in context.config.companies.watchlist
        if other.ats == "credit_agricole_jobs" and other.name != company.name
    }
    for item in listed:
        if item["entity"] and plain(item["entity"]) in others:
            continue
        if item["country"] and item["country"] != country:
            continue
        details = {"published": None, "description": ""}
        if not context.is_known(source_name, item["id"]) and title_is_candidate(item["title"], context.config):
            details = parse_offer_page(context.client.get(item["url"]).text)
        day = details["published"]
        published = datetime(day.year, day.month, day.day, tzinfo=zone) if day else None
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=item["id"],
                url=item["url"],
                apply_url=item["url"],
                reference=item["id"],
                title=item["title"],
                company=item["entity"] or company.name,
                location=item["city"],
                description=details["description"],
                description_is_partial=not details["description"],
                contract_type_raw=item["contract"],
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={"listing": item},
            )
        )
    return offers
