"""SuccessFactors connector through the site map, on a fake site."""

import httpx

from conftest import NOW
from job_agent.collectors import successfactors_sitemap as sitemap
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

SITE = "https://jobs.exemple.com"
SITE_MAP = """<?xml version="1.0"?><urlset>
<url><loc>https://jobs.exemple.com/Marque/job/Paris-Data-Analyst-HF-75008/1001/</loc><lastmod>2026-11-07</lastmod></url>
<url><loc>https://jobs.exemple.com/job/Traverse-City-Data-Engineer/1002/</loc></url>
<url><loc>https://jobs.exemple.com/job/Dole-Comptable-HF-39100/1003/</loc></url>
<url><loc>https://jobs.exemple.com/job/Lyon-Data-Scientist-HF-69001/1004/</loc></url>
</urlset>"""


def _field(label: str, value: str, lang: str, prop: str = "") -> str:
    attribute = f' itemprop="{prop}"' if prop else ""
    return (f'<div class="col-xs-12">{label}: <span class="rtltextaligneligible" lang="{lang}"{attribute}>'
            f"{value}</span></div>")


FRENCH = (
    '<meta property="og:title" content="Data Analyst H/F">'
    + _field("Date de début de publication", "07/11/2026", "fr-FR")
    + _field("Localisation", "Paris", "fr-FR")
    + _field("Type de contrat", "CDI", "fr-FR")
    + _field("Description de poste", "<p>Tableaux de bord <b>Power BI</b>.</p>", "fr-FR", "description")
)
AMERICAN = (
    '<meta property="og:title" content="Data Engineer">'
    + _field("Posting Start Date", "11/7/26", "en-US")
    + _field("Country", "United States", "en-US")
    + _field("Job Location", "Traverse City", "en-US")
)


class Fake:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /services/\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITE_MAP)
        self.opened.append(request.url.path)
        return httpx.Response(200, text=AMERICAN if "Traverse" in request.url.path else FRENCH)


def test_collect(config) -> None:
    fake = Fake()
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="consumer_goods", careers_url=SITE, ats="successfactors_sitemap")
    offers = sitemap.collect(company, CollectContext(client, config, NOW, lambda s, i: i == "1004"))

    assert [o.source_offer_id for o in offers] == ["1001", "1003", "1004"]  # the American offer is skipped
    assert len(fake.opened) == 2  # Data Analyst and Data Engineer; not the "Comptable", not the known 1004
    first = offers[0]
    assert first.title == "Data Analyst H/F" and first.location == "Paris" and first.contract_type_raw == "CDI"
    assert first.description == "Tableaux de bord Power BI." and first.published_at.date().isoformat() == "2026-11-07"
    assert offers[1].title == "Dole Comptable HF" and offers[1].description == ""  # title read in the address


def test_dates_follow_the_posting_language() -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Paris")
    assert sitemap.published("3/25/26", "en-US", zone).date().isoformat() == "2026-03-25"
    assert sitemap.published("01/03/2026", "fr-FR", zone).date().isoformat() == "2026-03-01"
