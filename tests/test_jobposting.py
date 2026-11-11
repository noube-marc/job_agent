"""Generic site map + JobPosting connector, on a fake site."""

import json

import httpx

from conftest import NOW
from job_agent.collectors import jobposting
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

INDEX = """<sitemapindex><sitemap><loc>https://emplois.exemple.fr/offers-sitemap.xml</loc></sitemap></sitemapindex>"""
OFFERS = """<urlset>
<url><loc>https://emplois.exemple.fr/jobs/data-analyst-h-f-paris-1001</loc></url>
<url><loc>https://emplois.exemple.fr/jobs/equipier-polyvalent-h-f-lyon-1002</loc></url>
<url><loc>https://emplois.exemple.fr/offre/tq3g3vmorv</loc></url>
<url><loc>https://emplois.exemple.fr/jobs/data-engineer-h-f-bruxelles-1004</loc></url>
</urlset>"""


def _page(title: str, country: str = "FR", kind: object = "PERMANENT") -> str:
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
               "datePosted": "2026-11-05T08:36:23.676Z", "employmentType": kind,
               "description": "<p>Modèles de prévision en <b>Python</b>.</p>",
               "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "postalCode": "60000",
                                                             "addressLocality": "Beauvais", "addressCountry": country}}}
    return f'<script type="application/ld+json">{json.dumps(posting)}</script>'


class Fake:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap_index.xml":
            return httpx.Response(200, text=INDEX)
        if path == "/offers-sitemap.xml":
            return httpx.Response(200, text=OFFERS)
        self.opened.append(path)
        if path.endswith("1004"):
            return httpx.Response(200, text=_page("Data Engineer H/F", country="BE"))
        return httpx.Response(200, text=_page("Data Scientist (H/F)", kind=["FULL_TIME"]))


def test_collect(config) -> None:
    fake = Fake()
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="retail",
                             careers_url="https://emplois.exemple.fr/sitemap_index.xml", ats="jobposting")
    offers = jobposting.collect(company, CollectContext(client, config, NOW))

    assert [o.source_offer_id for o in offers] == ["1001", "1002", "/offre/tq3g3vmorv"]  # Belgian offer skipped
    # Target titles read in the address, and the address without words; not the "équipier".
    assert fake.opened == ["/jobs/data-analyst-h-f-paris-1001", "/offre/tq3g3vmorv", "/jobs/data-engineer-h-f-bruxelles-1004"]
    first = offers[0]
    assert first.title == "Data Scientist (H/F)" and first.location == "60000 Beauvais"
    assert first.description == "Modèles de prévision en Python." and first.contract_type_raw is None  # FULL_TIME says nothing
    assert first.published_at.isoformat() == "2026-11-05T08:36:23.676000+00:00"
    assert offers[1].title == "equipier polyvalent h f lyon" and offers[1].description == ""


def test_contract_and_missing_data() -> None:
    assert jobposting._contract({"employmentType": "PERMANENT"}) == "PERMANENT"
    assert jobposting._contract({"employmentType": ["INTERN"]}) == "stage"
    assert jobposting.job_posting("<html>pas de données</html>") == {}
