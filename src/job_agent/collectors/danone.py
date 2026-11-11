"""Danone career site connector (careers.danone.com).

`careers_url` is the career site, for example "https://careers.danone.com".

The site map (/sitemap.xml, allowed by robots.txt) lists every open offer
in the world, "…/jobs/<title>-<id>-<language>.html". It is read at each
run (the whole list), and an offer page is opened only for NEW offers
whose title, read in the address, matches the search. The page carries
the offer as attributes of one element: title, country, city, date,
position type, remote work and description. Only these fields are kept:
the element also holds a recruiter's e-mail address, never stored.
"""

import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer
from job_agent.processing.filters import title_is_candidate

_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
_JOB = re.compile(r"/jobs/(.+)-(\d+)-[a-z]{2}-[a-z]{2}\.html$")
COUNTRY_NAMES = {"FR": "france", "BE": "belgium", "CH": "switzerland"}
WORK_MODES = {"on-site": "onsite", "hybrid": "hybrid", "remote": "full_remote", "fully remote": "full_remote"}


def parse_site_map(xml: str) -> list[dict]:
    offers = []
    for url in _LOC.findall(xml):
        match = _JOB.search(urlsplit(url).path)
        if match:
            offers.append({"id": match.group(2), "url": url,
                           "words": unquote(match.group(1)).replace("-", " ")})
    return offers


def parse_offer_page(html: str, offer_id: str) -> dict:
    """The offer's attributes (empty dict if the page has none)."""
    element = BeautifulSoup(html, "html.parser").find(attrs={"jobid": offer_id})
    if element is None:
        return {}
    attributes = element.attrs
    posted = attributes.get("posted") or ""
    return {
        "title": attributes.get("title") or "",
        "country": (attributes.get("country") or "").strip(),
        "city": attributes.get("city") or None,
        "published": datetime.fromtimestamp(int(posted) / 1000, tz=timezone.utc) if posted.isdigit() else None,
        "position_type": attributes.get("positiontype") or None,
        "work_mode": WORK_MODES.get((attributes.get("workfromhome") or "").strip().lower()),
        "description": html_to_text(attributes.get("description") or ""),
    }


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    source_name = f"danone:{company.name}"
    parts = urlsplit(company.careers_url)
    listed = parse_site_map(context.client.get(f"{parts.scheme}://{parts.netloc}/sitemap.xml").text)
    if not listed:
        raise SourceError("aucune offre dans le plan du site (structure changée ?)")
    country = COUNTRY_NAMES.get(context.config.location.country.upper(), context.config.location.country.lower())

    offers: list[Offer] = []
    seen: set[str] = set()
    for item in listed:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        details: dict = {}
        if not context.is_known(source_name, item["id"]) and title_is_candidate(item["words"], context.config):
            details = parse_offer_page(context.client.get(item["url"]).text, item["id"])
            if details.get("country") and details["country"].lower() != country:
                continue
        published = details.get("published")
        offers.append(
            Offer(
                source="career_page",
                source_name=source_name,
                source_offer_id=item["id"],
                url=item["url"],
                apply_url=item["url"],
                title=details.get("title") or item["words"],
                company=company.name,
                location=details.get("city"),
                description=details.get("description", ""),
                description_is_partial=not details.get("description"),
                contract_type_raw=details.get("position_type"),
                work_mode=details.get("work_mode"),
                published_at=published,
                published_at_source="career_site" if published else None,
                first_seen_at=context.now,
                raw_payload={"address_words": item["words"], "country": details.get("country"),
                             "position_type": details.get("position_type")},
            )
        )
    return offers
