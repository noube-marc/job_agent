"""Danone career site connector, on a fake site."""

import httpx

from conftest import NOW
from job_agent.collectors import danone
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

SITE_MAP = """<urlset>
<url><loc>https://careers.exemple.com/en-global/jobs/data-analyst-h-f-101-fr-fr.html</loc></url>
<url><loc>https://careers.exemple.com/en-global/jobs/data-scientist-h-f-102-fr-fr.html</loc></url>
<url><loc>https://careers.exemple.com/en-global/jobs/conducteur-machine-103-fr-fr.html</loc></url>
<url><loc>https://careers.exemple.com/en-global/jobs.html</loc></url>
</urlset>"""


def _page(job_id: str, title: str, country: str) -> str:
    return (
        f'<article jobid="{job_id}" title="{title}" country="{country}" city="Paris" posted="1793191140000" '
        'positiontype="Permanent (Full Time)" workfromhome="Hybrid" recruiteremail="recruteur@exemple.com" '
        'description="&lt;p&gt;Analyse des ventes avec &lt;b&gt;SQL&lt;/b&gt;.&lt;/p&gt;"></article>'
    )


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(404)
    if path == "/sitemap.xml":
        return httpx.Response(200, text=SITE_MAP)
    if "-101-" in path:
        return httpx.Response(200, text=_page("101", "Data Analyst H/F", "France"))
    return httpx.Response(200, text=_page("102", "Data Scientist H/F", "Morocco"))


def test_collect(config) -> None:
    client = PoliteClient(0, 5, transport=httpx.MockTransport(_handler), sleep=lambda s: None)
    company = WatchedCompany(
        name="Danone", sector="agriculture_agrifood", careers_url="https://careers.exemple.com", ats="danone"
    )
    offers = danone.collect(company, CollectContext(client, config, NOW))

    assert [o.source_offer_id for o in offers] == ["101", "103"]  # the Moroccan offer is skipped
    first = offers[0]
    assert first.title == "Data Analyst H/F" and first.location == "Paris" and first.work_mode == "hybrid"
    assert first.description == "Analyse des ventes avec SQL." and first.contract_type_raw == "Permanent (Full Time)"
    assert first.published_at.year == 2026
    assert "recruteur@exemple.com" not in str(first.raw_payload)
    assert offers[1].title == "conducteur machine" and offers[1].description == ""


def test_offer_of_another_country_is_opened_once(config) -> None:
    opened: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in ("/robots.txt", "/sitemap.xml"):
            opened.append(request.url.path)
        return _handler(request)

    skipped: set[tuple[str, str]] = set()
    company = WatchedCompany(
        name="Danone", sector="agriculture_agrifood", careers_url="https://careers.exemple.com", ats="danone"
    )
    for _ in range(2):
        client = PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)
        context = CollectContext(
            client,
            config,
            NOW,
            lambda s, i: i == "101",
            lambda s, i: (s, i) in skipped,
            lambda s, i: skipped.add((s, i)),
        )
        danone.collect(company, context)
    assert opened == ["/en-global/jobs/data-scientist-h-f-102-fr-fr.html"]  # the Moroccan offer, once
