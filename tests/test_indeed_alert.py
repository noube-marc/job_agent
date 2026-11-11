"""Indeed alert e-mails (anonymised fixture, no real Gmail)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_agent.gmail.indeed import parse_indeed, published_from
from job_agent.gmail.parsers import PARSERS
from job_agent.gmail.reader import AlertEmail
from job_agent.processing.filters import evaluate
from job_agent.processing.pipeline import complete_offer

FIXTURE = Path(__file__).parent / "fixtures" / "indeed_alert.txt"
RECEIVED = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def _alert(message_id: str = "i1") -> AlertEmail:
    return AlertEmail(message_id, "indeed", "donotreply@jobalert.indeed.com", "Alerte",
                      RECEIVED, "", FIXTURE.read_text(encoding="utf-8"))


def test_indeed_alert_blocks() -> None:
    offers = parse_indeed(_alert())
    assert [(o.title, o.company, o.location, o.department_code) for o in offers] == [
        ("Data Analyst (F/H)", "Banque Exemple", "Saint-Denis (93)", "93"),
        ("Data Engineer H/F", "Conseil - Exemple", "Lyon (69)", "69"),
        ("Data Scientist F/H", "Assurance Exemple", "Niort (79)", "79"),
        ("Data Analyst H/F", "Distribution Exemple", "Paris (75)", "75"),
    ]
    first = offers[0]
    assert first.source_name == "email:indeed" and first.description_is_partial
    assert first.salary_raw == "De 40 000 € à 45 000 € par an"
    assert first.description.startswith("Vous analysez")  # badges are not kept
    assert "Candidature simplifiée" not in first.description


def test_indeed_links_keep_only_the_public_job_key() -> None:
    offers = parse_indeed(_alert())
    assert offers[0].source_offer_id == "0123456789abcdef"
    assert offers[0].url == "https://fr.indeed.com/viewjob?jk=0123456789abcdef"
    # Sponsored offer without job key: an Indeed search, to open by hand.
    assert offers[2].url == "https://fr.indeed.com/emplois?q=Data+Scientist&l=Niort"
    for offer in offers:
        kept = offer.url + str(offer.raw_payload)
        assert "FAKE" not in kept and "engage.indeed" not in kept and "clk" not in kept


def test_indeed_relative_dates() -> None:
    offers = parse_indeed(_alert())
    assert offers[0].published_at == RECEIVED and offers[0].published_at_source == "job_board"
    assert offers[1].published_at == RECEIVED - timedelta(days=3)
    assert published_from("il y a 1 jour", RECEIVED) == RECEIVED - timedelta(days=1)
    assert published_from("Il y a plus de 30 jours", RECEIVED) == RECEIVED - timedelta(days=30)
    assert published_from("Data Analyst", RECEIVED) is None


def test_indeed_freshness_uses_the_indeed_date(config) -> None:
    offers = parse_indeed(_alert())
    recent = evaluate(complete_offer(offers[0]), config, RECEIVED)
    old = evaluate(complete_offer(offers[3]), config, RECEIVED)
    assert recent.kept and "date estimée" not in recent.flags
    assert not old.kept and any("trop ancienne" in reason for reason in old.reasons)


def test_indeed_identifier_is_stable_between_emails() -> None:
    first = [o.source_offer_id for o in parse_indeed(_alert("i1"))]
    assert first == [o.source_offer_id for o in parse_indeed(_alert("i2"))]


def test_indeed_reader_is_registered() -> None:
    assert PARSERS["indeed"] is parse_indeed
