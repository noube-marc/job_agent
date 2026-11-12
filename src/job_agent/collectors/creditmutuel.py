"""Crédit Mutuel group career site connector (recrutement.creditmutuel.fr).

`careers_url` is the site map of the offers, for example
"https://recrutement.creditmutuel.fr/fr/sitemap_offres.aspx" (declared in
robots.txt). Each entry is "offre.html?annonce=<id>" with its publication
date (lastmod). The address holds no title, so an offer page is opened
only when it is NEW and published within `freshness.max_age_days`; older
offers are never notified anyway and are not read.

The offer page gives its fields as rows (an icon titled "Type de contrat",
"Localisation", "Date de publication"…, then the value) and the text of
the offer.
"""

import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer

_URL = re.compile(r"<url>(.*?)</url>", re.S)
_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
_LASTMOD = re.compile(r"<lastmod>\s*(\d{4}-\d{2}-\d{2})")
# The site sometimes drops connections: after this many failed offer pages, stop for this run.
MAX_FAILURES = 3


def parse_site_map(xml: str) -> list[dict]:
    offers = []
    for block in _URL.findall(xml):
        loc, lastmod = _LOC.search(block), _LASTMOD.search(block)
        if not loc:
            continue
        url = loc.group(1).replace("&amp;", "&")
        offer_id = (parse_qs(urlsplit(url).query).get("annonce") or [None])[0]
        if offer_id:
            offers.append(
                {"id": offer_id, "url": url, "date": date.fromisoformat(lastmod.group(1)) if lastmod else None}
            )
    return offers


def parse_offer_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for row in soup.select("tr"):
        icon = row.select_one("span.ei_root_icon[title]")
        cells = row.find_all("td")
        if icon and cells:
            value = re.sub(r"\s*\(\s*\)\s*$", "", cells[-1].get_text(" ", strip=True)).strip()
            if value:
                fields[icon["title"].strip()] = value
    title_tag = soup.select_one("h1") or soup.title
    # The offer text: the longest block that holds neither the fields nor the other offers.
    blocks = [
        block
        for block in soup.find_all("div")
        if not block.select("span.ei_root_icon[title]") and "Ces autres offres" not in block.get_text(" ")
    ]
    longest = max(blocks, key=lambda block: len(block.get_text(" ", strip=True)), default=None)
    return {
        "title": title_tag.get_text(" ", strip=True) if title_tag else "",
        "fields": fields,
        "description": html_to_text(longest.decode_contents()) if longest is not None else "",
    }


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"creditmutuel:{company.name}"
    listed = parse_site_map(context.client.get(company.careers_url).text)
    if not listed:
        raise SourceError("aucune offre dans le plan du site (structure changée ?)")
    zone = ZoneInfo(context.config.schedule.timezone)
    window_start = (context.now - timedelta(days=context.config.freshness.max_age_days + 1)).date()

    offers: list[Offer] = []
    seen: set[str] = set()
    failures = 0
    for item in listed:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        day = item["date"]
        if context.is_known(source_name, item["id"]):
            # Still listed: reported (not read again), so the daily recheck sees it online.
            offers.append(
                Offer(
                    source="career_page",
                    source_name=source_name,
                    source_offer_id=item["id"],
                    url=item["url"],
                    title=item["id"],
                    company=company.name,
                    first_seen_at=context.now,
                    published_at=datetime(day.year, day.month, day.day, tzinfo=zone) if day else None,
                    published_at_source="career_site" if day else None,
                )
            )
            continue
        if item["date"] is not None and item["date"] < window_start:
            continue  # too old to be notified: not read
        if failures >= MAX_FAILURES:
            continue  # the site refuses connections: the other new offers wait for the next run
        try:
            details = parse_offer_page(context.client.get(item["url"]).text)
        except SourceError:
            failures += 1  # not stored: read again at the next run
            continue
        if not details["title"]:
            continue
        fields = details["fields"]
        raw_date = fields.get("Date de publication")
        try:
            published = datetime.strptime(raw_date, "%d/%m/%Y").replace(tzinfo=zone) if raw_date else None
        except ValueError:
            published = None
        if published is None and item["date"] is not None:
            published = datetime(item["date"].year, item["date"].month, item["date"].day, tzinfo=zone)
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=item["id"],
                url=item["url"],
                apply_url=item["url"],
                reference=fields.get("Référence"),
                title=details["title"],
                company=company.name,
                location=fields.get("Localisation"),
                description=details["description"],
                description_is_partial=not details["description"],
                contract_type_raw=fields.get("Type de contrat"),
                experience_raw=fields.get("Niveau d expérience") or fields.get("Niveau d'expérience"),
                salary_raw=fields.get("Salaire"),
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={"fields": fields},
            )
        )
    return offers
