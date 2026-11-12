"""Phenom connector, on a fake site."""

import json

import httpx

from conftest import NOW
from job_agent.collectors import phenom
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

SITE = "https://careers.exemple.com/global/en/search-results?qcountry=France"


def _job(job_id: int, title: str, country: str = "France", posted: str = "2026-11-10T09:00:00.000+0000") -> dict:
    return {"jobId": str(job_id), "title": title, "country": country, "postedDate": posted,
            "location": "Saint-Ouen, France, 93400", "remote": "Hybrid working",
            "recAction": "recruteur@exemple.com"}


def _detail_page(contract: str) -> str:
    data = {"jobDetail": {"data": {"job": {
        "employmentType": contract, "description": "<p>Tableaux de bord <b>Power BI</b> et SQL.</p>",
    }}}}
    return f"<script>phApp.ddo = {json.dumps(data)}; phApp.sessionParams = {{}};</script>"


class PhenomFake:
    def __init__(self, jobs: list[dict], contracts: dict[str, str]) -> None:
        self.jobs, self.contracts = jobs, contracts
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        self.requests.append(f"{request.method} {request.url.path}")
        if request.url.path == "/widgets":
            body = json.loads(request.content)
            assert body["selected_fields"] == {"country": ["France"]} and body["lang"] == "en_global"
            start = body["from"]
            page = self.jobs[start:start + body["size"]]
            return httpx.Response(200, json={"refineSearch": {"totalHits": len(self.jobs), "data": {"jobs": page}}})
        job_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=_detail_page(self.contracts.get(job_id, "Permanent")))


def test_phenom_collect(config, monkeypatch) -> None:
    monkeypatch.setattr(phenom, "PAGE_SIZE", 2)
    fake = PhenomFake(
        [_job(1, "Data Analyst H/F"), _job(2, "Comptable"), _job(3, "Data Engineer", country="Germany"),
         _job(4, "Data Scientist"), _job(5, "BI Analyst")],
        {"5": "Temporary"},
    )
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="insurance", careers_url=SITE, ats="phenom")
    offers = phenom.collect(company, CollectContext(client, config, NOW, lambda s, offer_id: offer_id == "4"))

    assert [o.source_offer_id for o in offers] == ["1", "2", "4", "5"]  # German offer skipped
    assert fake.requests.count("POST /widgets") == 3  # 5 offers, 2 per request
    assert [r for r in fake.requests if r.startswith("GET")] == ["GET /global/en/job/1", "GET /global/en/job/5"]
    first = offers[0]
    assert first.url == "https://careers.exemple.com/global/en/job/1"
    assert first.description == "Tableaux de bord Power BI et SQL." and first.contract_type_raw == "Permanent"
    assert first.work_mode == "hybrid" and first.published_at.isoformat() == "2026-11-10T09:00:00+00:00"
    assert offers[3].contract_type_raw is None  # "Temporary" is not trusted
    assert "recAction" not in first.raw_payload  # recruiters' details are never kept


def test_site_parts() -> None:
    assert phenom.site_parts(SITE) == ("https://careers.exemple.com", "global", "en")


def test_reposted_offer_keeps_the_older_jobposting_date(config) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/widgets":
            return httpx.Response(200, json={"refineSearch": {"totalHits": 1, "data": {"jobs": [
                _job(1, "Data Analyst H/F", posted="2026-11-10T11:21:42.000+0000")]}}})
        posting = '<script type="application/ld+json">{"@type": "JobPosting", "datePosted": "2026-11-02"}</script>'
        return httpx.Response(200, text=_detail_page("Permanent") + posting)

    client = PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="insurance", careers_url=SITE, ats="phenom")
    [offer] = phenom.collect(company, CollectContext(client, config, NOW))
    assert offer.published_at.date().isoformat() == "2026-11-02"
