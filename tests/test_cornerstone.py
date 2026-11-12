"""Cornerstone connector, on a fake career site, and robots.txt with a byte order mark."""

import json

import httpx

from conftest import NOW
from job_agent.collectors import cornerstone
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.collectors.robots import RobotRules
from job_agent.config.models import WatchedCompany

SITE = "https://exemple.csod.com/ux/ats/careersite/3/home?c=exemple&lang=fr-FR"
PAGE = '<script>var csod = {"token":"anonymous-token","cultureID":13,"endpoints":{"cloud":"https://eu.api.exemple.com/"}};</script>'


def _requisition(rid: int, title: str, country: str = "FR", date: str = "10/11/2026") -> dict:
    return {
        "requisitionId": rid,
        "displayJobTitle": title,
        "postingEffectiveDate": date,
        "locations": [{"city": "PARIS", "country": country}],
        "externalDescription": "<p>Analyse des ventes avec <b>SQL</b>. CDI.</p>",
    }


class Fake:
    def __init__(self, requisitions: list[dict]) -> None:
        self.requisitions = requisitions
        self.searches: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=chr(0xFEFF) + "User-agent: *\nCrawl-delay: 10\n")
        if request.url.host == "exemple.csod.com":
            return httpx.Response(200, text=PAGE)
        assert request.headers["Authorization"] == "Bearer anonymous-token"
        body = json.loads(request.content)
        self.searches.append(body)
        start = (body["pageNumber"] - 1) * body["pageSize"]
        page = self.requisitions[start : start + body["pageSize"]]
        return httpx.Response(200, json={"data": {"totalCount": len(self.requisitions), "requisitions": page}})


def test_cornerstone_collect(config, monkeypatch) -> None:
    monkeypatch.setattr(cornerstone, "PAGE_SIZE", 2)
    fake = Fake(
        [
            _requisition(1, "Data Analyst F/H"),
            _requisition(2, "CDD - Opérateur"),
            _requisition(3, "Data Engineer", country="ES"),
            _requisition(4, "Data Scientist"),
        ]
    )
    pauses: list[float] = []
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=pauses.append)
    company = WatchedCompany(name="Exemple", sector="agriculture_agrifood", careers_url=SITE, ats="cornerstone")
    offers = cornerstone.collect(company, CollectContext(client, config, NOW))

    assert [o.source_offer_id for o in offers] == ["1", "2", "4"]  # Spanish offer skipped
    assert len(fake.searches) == 2 and fake.searches[0]["careerSiteId"] == 3 and fake.searches[0]["cultureId"] == 13
    first = offers[0]
    assert first.url == "https://exemple.csod.com/ux/ats/careersite/3/home/requisition/1?c=exemple&lang=fr-FR"
    assert first.description == "Analyse des ventes avec SQL. CDI." and first.location == "Paris"
    assert first.published_at.date().isoformat() == "2026-11-10"
    assert "anonymous-token" not in json.dumps(first.raw_payload)
    assert offers[1].contract_type_raw == "CDD - Opérateur"
    assert any(pause >= 10 for pause in pauses)  # Crawl-delay of the career site respected


def test_byte_order_mark_does_not_hide_the_first_line() -> None:
    rules = RobotRules(chr(0xFEFF) + "User-agent: *\nDisallow: /prive\nCrawl-delay: 10\n")
    assert not rules.can_fetch("job-agent/0.1", "https://x.fr/prive/page")
    assert rules.crawl_delay("job-agent/0.1") == 10
