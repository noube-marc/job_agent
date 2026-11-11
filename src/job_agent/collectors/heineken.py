"""HEINEKEN career site connector (careers.theheinekencompany.com).

`careers_url` is the job list of one operating company, for example
"https://careers.theheinekencompany.com/Francais/job-listing?operatings_company%5B0%5D=312"
(HEINEKEN France). The search pages (/search/) are forbidden by robots.txt;
this list is allowed. Its pages (10 offers each, "&page=0,0,N") are all
read at each run. An offer page is opened only for NEW offers whose
title, read in the address, matches the search: it carries the standard
JobPosting data (title, date such as "Nov 05 2026", description).
robots.txt asks for 5 s between two requests, which the client respects.
"""

import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.collectors.jobposting import earliest, job_posting, visible_date
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

MAX_PAGES = 30


def page_url(careers_url: str, page: int) -> str:
    return careers_url if page == 0 else f"{careers_url}&page=0%2C0%2C{page}"


def parse_list_page(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    offers: dict[str, dict] = {}
    for link in soup.find_all("a", href=True):
        path = urlsplit(link["href"]).path
        if not path.startswith("/job/"):
            continue
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        offers.setdefault(path, {"id": path, "url": urljoin(base_url, path), "words": slug.replace("-", " ")})
    return list(offers.values())


def parse_date(value: str | None, zone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    except ValueError:
        pass
    for pattern in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=zone)
        except ValueError:
            continue
    return None


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"heineken:{company.name}"
    zone = ZoneInfo(context.config.schedule.timezone)
    listed: list[dict] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        url = page_url(company.careers_url, page)
        items = [item for item in parse_list_page(context.client.get(url).text, url) if item["id"] not in seen]
        if page == 0 and not items:
            raise SourceError("aucune offre lisible sur la liste des offres (structure changée ?)")
        if not items:
            break
        seen.update(item["id"] for item in items)
        listed += items

    offers: list[Offer] = []
    for item in listed:
        posting: dict = {}
        page = ""
        if not context.is_known(source_name, item["id"]) and title_is_candidate(item["words"], context.config):
            page = context.client.get(item["url"]).text
            posting = job_posting(page)
        published = earliest(parse_date(posting.get("datePosted"), zone), visible_date(page, zone))
        description = html_to_text(posting.get("description") or "")
        location = posting.get("jobLocation") or {}
        if isinstance(location, list):
            location = location[0] if location else {}
        address = location.get("address") if isinstance(location, dict) else None
        place = address.get("addressLocality") if isinstance(address, dict) else None
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=item["id"],
                url=item["url"],
                apply_url=item["url"],
                title=(posting.get("title") or item["words"]).strip(),
                company=company.name,
                location=place,
                description=description,
                description_is_partial=not description,
                contract_type_raw=posting.get("title") or item["words"],  # contract written in the title
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={"address_words": item["words"], "datePosted": posting.get("datePosted")},
            )
        )
    return offers
