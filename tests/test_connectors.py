"""Connectors read recorded (synthetic) responses: no network call."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from conftest import NOW
from job_agent.collectors import CONNECTORS, ashby, talentsoft
from job_agent.collectors.base import PoliteClient, SourceError, html_to_text
from job_agent.config.models import WatchedCompany

FIXTURES = Path(__file__).parent / "fixtures"


def _client(routes: dict[str, httpx.Response]) -> PoliteClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return routes[str(request.url)]

    return PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_registry() -> None:
    assert set(CONNECTORS) == {"talentsoft_rss", "ashby"}


def test_talentsoft_feed() -> None:
    company = WatchedCompany(name="Exemple", sector="insurance", careers_url="https://recrutement.exemple.fr", ats="talentsoft_rss")
    feed = (FIXTURES / "talentsoft_feed.xml").read_text(encoding="utf-8")
    url = "https://recrutement.exemple.fr/handlers/offerRss.ashx?LCID=1036"
    offers = talentsoft.collect(company, _client({url: httpx.Response(200, text=feed)}))

    assert len(offers) == 2
    first = offers[0]
    assert first.title == "Data Analyst F/H"
    assert first.reference == "2026-101" and first.source_offer_id == "101"
    assert first.contract_type_raw == "Contrat à durée indéterminée"
    assert first.location == "Niort (79)"
    assert first.published_at == datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc)
    assert "Power BI" in first.description and "<b>" not in first.description
    assert first.source_name == "talentsoft:Exemple" and not first.description_is_partial


def test_ashby_keeps_listed_offers_in_france() -> None:
    company = WatchedCompany(name="Exemple", sector="banking", careers_url="https://jobs.ashbyhq.com/exemple", ats="ashby")
    board = (FIXTURES / "ashby_board.json").read_text(encoding="utf-8")
    url = "https://api.ashbyhq.com/posting-api/job-board/exemple"
    offers = ashby.collect(company, _client({url: httpx.Response(200, text=board)}))

    assert [o.source_offer_id for o in offers] == ["aaaa-1111", "cccc-3333"]
    analyst, intern = offers
    assert analyst.work_mode == "hybrid" and analyst.contract_type is None
    assert analyst.apply_url.endswith("/application")
    assert intern.contract_type == "internship" and intern.work_mode == "full_remote"
    assert intern.description == "Stage data\n- SQL"


def test_broken_feed_is_a_source_error() -> None:
    with pytest.raises(SourceError):
        talentsoft.parse_feed("<html>maintenance</html", WatchedCompany(name="X", sector="other"), NOW)


def test_html_to_text() -> None:
    assert html_to_text("<p>Bonjour&nbsp;!</p><ul><li>SQL</li><li>Python</li></ul>") == "Bonjour !\n- SQL\n- Python"
