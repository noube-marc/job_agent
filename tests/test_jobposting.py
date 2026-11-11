"""Generic site map + JobPosting connector, on fake sites."""

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
<url><loc>https://emplois.exemple.fr/emploi/paris/data-scientist-f-h/3441/46789675648</loc></url>
<url><loc>https://emplois.exemple.fr/jobs/data-engineer-h-f-bruxelles-1004</loc></url>
<url><loc>https://emplois.exemple.fr/categorie/offres-data/3441/32379072/1</loc></url>
<url><loc>https://emplois.exemple.fr/en/data-careers</loc></url>
</urlset>"""
OPAQUE = """<urlset><url><loc>https://emplois.exemple.fr/offre/tq3g3vmorv</loc></url></urlset>"""


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
        if path == "/opaque.xml":
            return httpx.Response(200, text=OPAQUE)
        self.opened.append(path)
        if path.endswith("1004"):
            return httpx.Response(200, text=_page("Data Engineer H/F", country="BE"))
        if path.endswith("46789675648"):
            return httpx.Response(200, text="<html>offre retirée</html>")  # no JobPosting
        return httpx.Response(200, text=_page("Data Scientist (H/F)", kind=["FULL_TIME"]))


def _collect(config, careers_url: str, fake: Fake):
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="retail", careers_url=careers_url, ats="jobposting")
    return jobposting.collect(company, CollectContext(client, config, NOW))


def test_collect(config) -> None:
    fake = Fake()
    offers = _collect(config, "https://emplois.exemple.fr/sitemap_index.xml", fake)

    # Category and presentation pages are not offers; the Belgian offer and the page
    # without JobPosting are skipped.
    assert [o.source_offer_id for o in offers] == ["1001", "1002"]
    assert fake.opened == ["/jobs/data-analyst-h-f-paris-1001", "/emploi/paris/data-scientist-f-h/3441/46789675648",
                           "/jobs/data-engineer-h-f-bruxelles-1004"]
    first = offers[0]
    assert first.title == "Data Scientist (H/F)" and first.location == "60000 Beauvais"
    assert first.description == "Modèles de prévision en Python." and first.contract_type_raw is None
    assert first.published_at.isoformat() == "2026-11-05T08:36:23.676000+00:00"
    assert offers[1].title == "equipier polyvalent h f lyon" and offers[1].description == ""


def test_addresses_without_words_are_opened(config) -> None:
    fake = Fake()
    offers = _collect(config, "https://emplois.exemple.fr/opaque.xml", fake)
    assert [o.source_offer_id for o in offers] == ["/offre/tq3g3vmorv"] and fake.opened == ["/offre/tq3g3vmorv"]


def test_address_parts() -> None:
    parts = jobposting._address_parts
    assert parts("https://x.fr/emploi/paris/data-analyst-f-h/3441/46789675648") == ("46789675648", "data analyst f h")
    assert parts("https://x.fr/offres/148324-boucher-cdi-vichy") == ("148324", "boucher cdi vichy")
    assert parts("https://x.fr/jobs/equipier-lyon-749288") == ("749288", "equipier lyon")
    assert parts("https://x.fr/categorie/offres/3441/32379072/1")[0] is None


def test_contract_and_missing_data() -> None:
    assert jobposting._contract({"employmentType": "PERMANENT"}) == "PERMANENT"
    assert jobposting._contract({"employmentType": ["INTERN"]}) == "stage"
    assert jobposting.job_posting("<html>pas de données</html>") == {}


def test_date_without_time_zone_is_local() -> None:
    from zoneinfo import ZoneInfo

    date = jobposting._date("2026-11-10", ZoneInfo("Europe/Paris"))
    assert date.isoformat() == "2026-11-10T00:00:00+01:00"


def test_older_date_written_on_the_page_wins() -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Paris")
    page = '<li><svg></svg><span>Publié le 16/10/2026</span></li>' + _page("Data Scientist (H/F)")
    posted = jobposting._date(jobposting.job_posting(page)["datePosted"], zone)  # 05/11: a republication
    published = jobposting.earliest(posted, jobposting.visible_date(page, zone))
    assert published.date().isoformat() == "2026-10-16"
    assert jobposting.visible_date("<p>Date de publication : 03/11/2026</p>", zone).day == 3
    assert jobposting.visible_date("<p>aucune date</p>", zone) is None
