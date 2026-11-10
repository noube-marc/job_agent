"""Talentsoft (Cegid) connector, through the public RSS feed of offers.

`careers_url` is the Talentsoft site of the company, for example
"https://recrutement.maif.fr". The feed lists the 20 most recent offers
with their full description, which is enough for a run every 2 hours.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

from job_agent.collectors.base import CollectContext, SourceError, html_to_text
from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer

FEED_PATH = "/handlers/offerRss.ashx?LCID=1036"
_REFERENCE_PREFIX = re.compile(r"^\s*(\d{4}-\d+)\s*-\s*")
_LABELLED = re.compile(r"<b>\s*{label}\s*:\s*</b>\s*(.*?)\s*<br", re.IGNORECASE | re.DOTALL)


def feed_url(careers_url: str) -> str:
    parts = urlsplit(careers_url)
    return f"{parts.scheme}://{parts.netloc}{FEED_PATH}"


def _labelled(description_html: str, label: str) -> str | None:
    match = re.search(_LABELLED.pattern.format(label=label), description_html, _LABELLED.flags)
    return html_to_text(match.group(1)) if match else None


def parse_feed(xml_text: str, company: WatchedCompany, now: datetime) -> list[Offer]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise SourceError(f"flux RSS illisible ({error})") from None

    offers = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        raw_title = (item.findtext("title") or "").strip()
        description_html = item.findtext("description") or ""
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        query = parse_qs(urlsplit(link).query)

        reference_match = _REFERENCE_PREFIX.match(raw_title)
        reference = (query.get("offerReference") or [None])[0] or (
            reference_match.group(1) if reference_match else None
        )
        offer_id = (query.get("idOffre") or [reference or link])[0]
        title = _REFERENCE_PREFIX.sub("", raw_title).strip()
        contract = _labelled(description_html, "Type de contrat") or (categories[1] if len(categories) > 1 else None)
        location = _labelled(description_html, "Lieu") or (categories[2] if len(categories) > 2 else None)
        published = item.findtext("pubDate")
        try:
            published_at = parsedate_to_datetime(published) if published else None
        except (TypeError, ValueError):
            published_at = None
        if published_at is not None and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        offers.append(
            Offer(
                source="career_page",
                source_name=f"talentsoft:{company.name}",
                source_offer_id=str(offer_id),
                url=link,
                apply_url=link,
                reference=reference,
                title=title,
                company=company.name,
                location=location,
                description=html_to_text(description_html),
                contract_type_raw=contract,
                published_at=published_at,
                published_at_source="career_site" if published_at else None,
                first_seen_at=now,
                raw_payload={
                    "title": raw_title, "link": link, "categories": categories,
                    "pubDate": published, "description": description_html,
                },
            )
        )
    return offers


def collect(company: WatchedCompany, context: CollectContext) -> list[Offer]:
    response = context.client.get(feed_url(company.careers_url))
    return parse_feed(response.text, company, context.now)
