"""Read Hellowork job alert e-mails.

Each card of the e-mail holds: title, company, an optional badge ("Super
recruteur"), place ("Lyon - 69" or a region), contract, an optional
salary, then the "Voir l'offre" button.

The e-mail only holds tracking links that contain the address of the
recipient: they are NEVER kept, and never opened. The link given to the
user is a Hellowork search (title and city), to open by hand.
"""

import hashlib
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from job_agent.gmail.reader import AlertEmail
from job_agent.offer import Offer
from job_agent.processing.extract import detect_contract

RIGHT_QUOTE = chr(0x2019)
EURO = chr(0x20AC)
BUTTON = "voir l" + RIGHT_QUOTE + "offre"
BADGES = {"super recruteur"}
PLACE = re.compile(r"^(?P<city>.+?) - (?P<code>\d{2,3}|2[AB])$")
SEARCH_URL = "https://www.hellowork.com/fr-fr/emploi/recherche.html"
GENDER = re.compile(r"\s*[(\[]?\b[HF]\s*/\s*[HF](?:\s*/\s*X)?\b[)\]]?\s*$", re.IGNORECASE)
SPACES = re.compile(r"\s+")


def _clean(text: str) -> str:
    return SPACES.sub(" ", text.replace(chr(0xA0), " ").replace(chr(0x202F), " ")).strip()


def _visible_lines(markup: str) -> tuple[list[str], set[str]]:
    """Visible lines of an HTML e-mail, and the texts of its links."""
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["style", "head", "script"]):
        tag.decompose()
    link_texts = {_clean(a.get_text(" ")) for a in soup.find_all("a", href=True)}
    lines = [_clean(line) for line in soup.get_text(chr(10)).splitlines()]
    return [line for line in lines if line], {text for text in link_texts if text}


def offer_id(title: str, company: str | None, place: str | None) -> str:
    """Stable identifier: the e-mail gives no offer number."""
    key = "|".join(_clean(part or "").lower() for part in (title, company, place))
    return "hw-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def search_link(title: str, city: str | None) -> str:
    params = {"k": GENDER.sub("", title).strip()}
    if city:
        params["l"] = city
    return f"{SEARCH_URL}?{urlencode(params)}"


def _cards(lines: list[str], link_texts: set[str]) -> list[list[str]]:
    cards: list[list[str]] = []
    start = 0
    for index, line in enumerate(lines):
        if line.replace("'", RIGHT_QUOTE).lower() != BUTTON:
            continue
        card, start = lines[start:index], index + 1
        # A card begins at its title, which is a link: this skips the
        # greeting and the alert name before the first card.
        first = next((i for i, value in enumerate(card) if value in link_texts), None)
        if first is None:
            continue
        card = [value for value in card[first:] if value.lower() not in BADGES]
        if len(card) >= 2:
            cards.append(card)
    return cards


def parse_hellowork(alert: AlertEmail) -> list[Offer]:
    offers: list[Offer] = []
    seen: set[str] = set()
    for card in _cards(*_visible_lines(alert.html)):
        title, company = card[0], card[1]
        place = city = code = contract = salary = None
        for value in card[2:]:
            match = PLACE.match(value)
            if EURO in value:
                salary = value
            elif match and place is None:
                place, city, code = value, match.group("city"), match.group("code")
            elif contract is None and detect_contract(value):
                contract = value
            elif place is None:
                place = city = value  # a region, e.g. "Île-de-France"
        identifier = offer_id(title, company, place)
        if identifier in seen:
            continue
        seen.add(identifier)
        offers.append(
            Offer(
                source="email_alert",
                source_name="email:hellowork",
                source_offer_id=identifier,
                url=search_link(title, city),
                title=title,
                company=company,
                location=place,
                department_code=code,
                description="",
                description_is_partial=True,
                salary_raw=salary,
                contract_type_raw=contract or (title if detect_contract(title) else None),
                # No publication date in the e-mail: the offer is dated by the
                # reception of the e-mail and flagged "date estimée".
                published_at=None,
                published_at_source=None,
                first_seen_at=alert.received_at,
                # Visible text of the card only, never the links.
                raw_payload={"message_id": alert.message_id, "card": card},
            )
        )
    return offers
