"""Hellowork alert e-mails (anonymised fixture, no real Gmail)."""

from datetime import UTC, datetime
from pathlib import Path

from conftest import NOW
from job_agent.gmail.hellowork import parse_hellowork
from job_agent.gmail.parsers import PARSERS
from job_agent.gmail.reader import AlertEmail
from job_agent.processing.filters import evaluate
from job_agent.processing.pipeline import complete_offer

FIXTURE = Path(__file__).parent / "fixtures" / "hellowork_alert.html"
RECEIVED = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)


def _alert(message_id: str = "h1") -> AlertEmail:
    return AlertEmail(
        message_id,
        "hellowork",
        "alerte@emails.hellowork.com",
        "Alerte",
        RECEIVED,
        FIXTURE.read_text(encoding="utf-8"),
        "",
    )


def test_hellowork_alert_cards() -> None:
    offers = parse_hellowork(_alert())
    assert [(o.title, o.company, o.location, o.department_code, o.contract_type_raw, o.salary_raw) for o in offers] == [
        ("Data Analyst H/F", "Assurance Exemple", "Lyon - 69", "69", "CDI", "40 000 - 45 000 € / an"),
        ("Stage - Data Analyst (F/H)", "Banque Exemple", "Île-de-France", None, "Stage", None),
        ("Data Engineer H/F", "Distribution Exemple", "Paris 8e - 75", "75", "CDD", None),
    ]
    first = offers[0]
    assert first.source == "email_alert" and first.source_name == "email:hellowork"
    assert first.description_is_partial
    assert first.published_at is None and first.first_seen_at == RECEIVED


def test_hellowork_tracking_links_are_never_kept() -> None:
    for offer in parse_hellowork(_alert()):
        kept = offer.url + str(offer.raw_payload) + str(offer.apply_url)
        assert "emails.hellowork.com" not in kept and "clic" not in kept
        assert "dGVzdEBleGFtcGxlLmNvbQ" not in kept  # encoded address of the recipient
        assert "Prénom" not in kept  # greeting of the e-mail
    assert parse_hellowork(_alert())[0].url == (
        "https://www.hellowork.com/fr-fr/emploi/recherche.html?k=Data+Analyst&l=Lyon"
    )


def test_hellowork_identifier_is_stable_between_emails() -> None:
    first = [o.source_offer_id for o in parse_hellowork(_alert("h1"))]
    second = [o.source_offer_id for o in parse_hellowork(_alert("h2"))]
    assert first == second and len(set(first)) == 3


def test_hellowork_internship_is_excluded(config) -> None:
    results = [evaluate(complete_offer(offer), config, NOW) for offer in parse_hellowork(_alert())]
    assert results[0].kept and "date estimée" in results[0].flags
    assert not results[1].kept  # "Stage" card


def test_hellowork_reader_is_registered() -> None:
    assert PARSERS["hellowork"] is parse_hellowork
