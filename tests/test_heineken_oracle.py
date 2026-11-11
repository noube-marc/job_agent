"""HEINEKEN and Oracle Recruiting Cloud connectors, on fake sites."""

import json

import httpx

from conftest import NOW
from job_agent.collectors import heineken, oracle
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany


def _client(handler) -> PoliteClient:
    return PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)


# --- HEINEKEN -----------------------------------------------------------
LIST_URL = "https://careers.exemple.com/Francais/job-listing?operatings_company%5B0%5D=312"


def _list_page(slugs: list[str]) -> str:
    return "".join(f'<a href="/job/heineken-france/france/{slug}">x</a>' * 2 for slug in slugs)


def _heineken_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nCrawl-delay: 5\nDisallow: /search/\n")
    if request.url.path == "/Francais/job-listing":
        page = request.url.params.get("page", "0,0,0").split(",")[-1]
        pages = {"0": ["cdi-data-analyst-hf-paris", "cdd-chauffeur-livreur-hf"], "1": ["cdi-data-engineer-hf-lille"]}
        return httpx.Response(200, text=_list_page(pages.get(page, [])))
    posting = {"@type": "JobPosting", "title": "CDI - Data Analyst - H/F", "datePosted": "Nov 05 2026",
               "description": "<p>SQL et Power BI.</p>", "jobLocation": {"address": {"addressLocality": "Paris"}}}
    return httpx.Response(200, text=f'<script type="application/ld+json">{json.dumps(posting)}</script>')


def test_heineken_collect(config) -> None:
    company = WatchedCompany(name="Heineken France", sector="consumer_goods", careers_url=LIST_URL, ats="heineken")
    offers = heineken.collect(company, CollectContext(_client(_heineken_handler), config, NOW))
    assert [o.source_offer_id.rsplit("/", 1)[-1] for o in offers] == [
        "cdi-data-analyst-hf-paris", "cdd-chauffeur-livreur-hf", "cdi-data-engineer-hf-lille"]
    first = offers[0]
    assert first.title == "CDI - Data Analyst - H/F" and first.location == "Paris" and first.description == "SQL et Power BI."
    assert first.published_at.date().isoformat() == "2026-11-05"
    assert offers[1].description == ""  # not a target title: page not opened


# --- Oracle -------------------------------------------------------------
ORACLE_PAGE = "https://ora.exemple.com/hcmUI/CandidateExperience/fr/sites/careers/jobs"


def _oracle_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(404)
    if path.endswith("/jobs"):
        return httpx.Response(200, text='<script>var x = "?siteNumber=CX_7";</script>')
    if path.endswith("recruitingCEJobRequisitions"):
        assert "siteNumber=CX_7" in str(request.url) and "expand=requisitionList" in str(request.url)
        jobs = [{"Id": "1", "Title": "Data Analyst (H/F)", "PostedDate": "2026-11-09", "PrimaryLocationCountry": "FR",
                 "PrimaryLocation": "VIROFLAY, 78-Yvelines, France"},
                {"Id": "2", "Title": "Data Engineer", "PostedDate": "2026-11-09", "PrimaryLocationCountry": "SG"},
                {"Id": "3", "Title": "Comptable (H/F)", "PostedDate": "2026-11-08", "PrimaryLocationCountry": "FR"}]
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 3, "requisitionList": jobs}]})
    detail = {"ExternalDescriptionStr": "<p>Mission : tableaux de bord.</p>", "ExternalQualificationsStr": "<p>SQL.</p>",
              "ExternalPostedStartDate": "2026-11-09T11:35:02+00:00", "PrimaryLocation": "VIROFLAY, 78-Yvelines, France",
              "WorkplaceTypeCode": "ORA_HYBRID"}
    return httpx.Response(200, json={"items": [detail]})


def test_oracle_collect(config) -> None:
    company = WatchedCompany(name="Exemple", sector="agriculture_agrifood", careers_url=ORACLE_PAGE, ats="oracle")
    offers = oracle.collect(company, CollectContext(_client(_oracle_handler), config, NOW))
    assert [o.source_offer_id for o in offers] == ["1", "3"]  # Singapore offer skipped
    first = offers[0]
    assert first.url == "https://ora.exemple.com/hcmUI/CandidateExperience/fr/sites/CX_7/job/1"
    assert first.description == "Mission : tableaux de bord.\nSQL." and first.department_code == "78"
    assert first.work_mode == "hybrid" and first.published_at.isoformat() == "2026-11-09T11:35:02+00:00"
    assert offers[1].description == "" and offers[1].published_at.date().isoformat() == "2026-11-08"
