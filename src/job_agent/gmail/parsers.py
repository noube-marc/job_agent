"""Turn job alert e-mails into Offer objects, one reader per site.

Only the content of the e-mail is used: the links it contains are NEVER
opened (that would be collecting from the site in disguise). An offer
read from an e-mail has a partial description (title, company, place).
"""

import re
from collections.abc import Callable

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
                # The alert e-mail gives no publication date: its reception
                # time is the job board date (the offer is at most that old).
                published_at=alert.received_at,
                published_at_source="job_board",
                first_seen_at=alert.received_at,
                raw_payload={"message_id": alert.message_id, "block": block.strip()[:2000]},
            )
        )
    return offers


PARSERS: dict[str, Callable[[AlertEmail], list[Offer]]] = {
    "linkedin": parse_linkedin,
}
