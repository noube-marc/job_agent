"""Turn job alert e-mails into Offer objects, one reader per site.

Only the content of the e-mail is used: the links it contains are NEVER
opened (that would be collecting from the site in disguise). An offer
read from an e-mail has a partial description (title, company, place).
"""

import html
import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_agent.collectors.base import html_to_text
from job_agent.gmail.reader import AlertEmail
from job_agent.offer import Offer
from job_agent.processing.extract import detect_contract

_SEPARATOR = re.compile(r"^\s*-{10,}\s*$", re.MULTILINE)
_LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\u00a0", " ").replace("\u202f", " ")).strip()


# A LinkedIn place line: "Paris, Ile-de-France, France", "France",
# "Lyon et peripherie", "Teletravail"...
_PLACE = re.compile(r"(\bfrance\b|t[ée]l[ée]travail|remote|hybride|p[ée]riph[ée]rie|r[ée]gion)", re.IGNORECASE)


def _title_company_place(lines: list[str]) -> tuple[str, str | None, str | None]:
    """Title, company and place of a block, ignoring the e-mail introduction
    before them and the social lines ("2 relations") after them."""
    for index in range(len(lines) - 1, 1, -1):
        if _PLACE.search(lines[index]):
            return lines[index - 2], lines[index - 1], lines[index]
    return lines[0], (lines[1] if len(lines) > 1 else None), (lines[2] if len(lines) > 2 else None)


def parse_linkedin(alert: AlertEmail) -> list[Offer]:
    text = alert.text or html_to_text(alert.html)
    offers: list[Offer] = []
    seen: set[str] = set()
    for block in _SEPARATOR.split(text):
        match = _LINKEDIN_JOB.search(block)
        if not match or match.group(1) in seen:
            continue
        job_id = match.group(1)
        seen.add(job_id)
        lines = [line for line in map(_clean, block.splitlines()) if line and "http" not in line]
        if not lines:
            continue
        title, company, location = _title_company_place(lines)
        offers.append(
            Offer(
                source="email_alert",
                source_name="email:linkedin",
                source_offer_id=job_id,
                url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                title=title,
                company=company,
                location=location,
                description="",
                description_is_partial=True,
                contract_type_raw=title if detect_contract(title) else None,
                # The alert e-mail gives no publication date. The offer is
                # dated by the e-mail reception and flagged "date estimée";
                # a career-site date replaces it when the same offer is found.
                published_at=None,
                published_at_source=None,
                first_seen_at=alert.received_at,
                raw_payload={"message_id": alert.message_id, "block": block.strip()[:2000]},
            )
        )
    return offers


# Only these search parameters are kept in a "see all offers" link: the
# others are tracking codes or one-click sign-in tokens tied to the account.
_LINKEDIN_SEARCH_PARAMS = {"keywords", "location", "geoId", "distance", "f_TPR", "f_E", "f_JT", "f_WT", "sortBy"}
_LINKEDIN_SEE_ALL = re.compile(r"(https://www\.linkedin\.com/(?:comm/)?jobs/search[^\s>\"']*)")


def linkedin_more_offers_link(alert: AlertEmail) -> str | None:
    """Clean "see all offers" link of a LinkedIn alert, or None.

    The agent never opens it: it is only sent to the user on Telegram.
    """
    text = alert.text or html_to_text(alert.html)
    match = _LINKEDIN_SEE_ALL.search(text) or _LINKEDIN_SEE_ALL.search(alert.html or "")
    if not match:
        return None
    parts = urlsplit(html.unescape(match.group(1)))
    params = [(key, value) for key, value in parse_qsl(parts.query) if key in _LINKEDIN_SEARCH_PARAMS]
    if not params:
        return None
    return urlunsplit(("https", "www.linkedin.com", "/jobs/search/", urlencode(params), ""))


PARSERS: dict[str, Callable[[AlertEmail], list[Offer]]] = {
    "linkedin": parse_linkedin,
}
MORE_OFFERS_LINKS: dict[str, Callable[[AlertEmail], str | None]] = {
    "linkedin": linkedin_more_offers_link,
}
