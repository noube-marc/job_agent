"""Read Indeed job alert e-mails (text version of the e-mail).

Each offer is a block of lines: title, "Company - City (dept)", optional
salary and badges ("Candidature simplifiée"), an excerpt of the offer, a
relative date ("Publié à l'instant", "il y a 3 jours") and a link.

The links of the e-mail carry tracking codes: they are NEVER kept, and
never opened. Only the public job key (`jk`) is kept, to build a clean
link https://fr.indeed.com/viewjob?jk=... for the user to open by hand.
When the e-mail gives no job key (first e-mail of a new alert), the link
is an Indeed search (title and city).
"""

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

from job_agent.gmail.hellowork import GENDER, _clean, _visible_lines
from job_agent.gmail.reader import AlertEmail
from job_agent.offer import Offer
from job_agent.processing.extract import detect_contract

EURO = chr(0x20AC)
JOB_KEY = re.compile(r"[?&]jk=([0-9a-f]{16})\b")
URL_LINE = re.compile(r"^https?://\S+$")
# The place follows the LAST " - " (company names may hold one, city
# names hold plain hyphens: "Saint-Denis").
COMPANY_PLACE = re.compile(r"^(?P<company>.+) - (?P<place>.+?)(?: \((?P<code>\d{2,3}|2[AB])\))?$")
BADGES = {"candidature simplifiée", "employeur réactif", "nouveau", "urgent"}
DATE_NOW = re.compile(r"^(publié )?(à l'instant|aujourd'hui)$", re.IGNORECASE)
DATE_AGO = re.compile(r"^(?:publié )?il y a (?:plus de )?(\d+) (jours?|heures?|minutes?)$", re.IGNORECASE)
VIEW_URL = "https://fr.indeed.com/viewjob"
SEARCH_URL = "https://fr.indeed.com/emplois"


def published_from(line: str, received: datetime) -> datetime | None:
    """Publication date from a relative date line, or None if it is not one."""
    text = line.replace(chr(0x2019), "'").strip()
    if DATE_NOW.match(text):
        return received
    match = DATE_AGO.match(text)
    if not match:
        return None
    count, unit = int(match.group(1)), match.group(2).lower()
    if unit.startswith("jour"):
        return received - timedelta(days=count)
    if unit.startswith("heure"):
        return received - timedelta(hours=count)
    return received - timedelta(minutes=count)


def _blocks(alert: AlertEmail) -> list[list[str]]:
    if alert.text.strip():
        lines = [_clean(line) for line in alert.text.splitlines()]
    else:
        lines = _visible_lines(alert.html)[0]
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line:
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    # An offer block ends with its link and holds at least title and company.
    return [b for b in blocks if len(b) >= 3 and URL_LINE.match(b[-1])]


def search_link(title: str, city: str | None) -> str:
    params = {"q": GENDER.sub("", title).strip()}
    if city:
        params["l"] = city
    return f"{SEARCH_URL}?{urlencode(params)}"


def parse_indeed(alert: AlertEmail) -> list[Offer]:
    offers: list[Offer] = []
    seen: set[str] = set()
    for block in _blocks(alert):
        link, lines = block[-1], block[:-1]
        title = lines[0]
        match = COMPANY_PLACE.match(lines[1])
        if match:
            company, place, code = match.group("company"), match.group("place"), match.group("code")
        else:
            company, place, code = lines[1], None, None
        salary = published = None
        excerpt: list[str] = []
        for value in lines[2:]:
            date = published_from(value, alert.received_at)
            if date is not None:
                published = date
            elif EURO in value and salary is None:
                salary = value
            elif value.lower() not in BADGES:
                excerpt.append(value)
        key = JOB_KEY.search(link)
        if key:
            identifier, url = key.group(1), f"{VIEW_URL}?jk={key.group(1)}"
        else:
            raw = "|".join(part.lower() for part in (title, company, place or ""))
            identifier = "in-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
            url = search_link(title, place)
        if identifier in seen:
            continue
        seen.add(identifier)
        place_text = f"{place} ({code})" if place and code else place
        offers.append(
            Offer(
                source="email_alert",
                source_name="email:indeed",
                source_offer_id=identifier,
                url=url,
                title=title,
                company=company,
                location=place_text,
                department_code=code,
                description=" ".join(excerpt),
                description_is_partial=True,
                salary_raw=salary,
                contract_type_raw=title if detect_contract(title) else None,
                # Relative date shown by Indeed ("il y a 3 jours"), to the day.
                published_at=published,
                published_at_source="job_board" if published else None,
                first_seen_at=alert.received_at,
                # Visible text of the block only, never the links.
                raw_payload={"message_id": alert.message_id, "block": lines},
            )
        )
    return offers
