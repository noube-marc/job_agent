"""Connectors read recorded (synthetic) responses: no network call."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from conftest import NOW
from job_agent.collectors import CONNECTORS, ashby, successfactors, talentsoft, workday
from job_agent.collectors.base import CollectContext, PoliteClient, SourceError, html_to_text
from job_agent.config.models import WatchedCompany

FIXTURES = Path(__file__).parent / "fixtures"


def _client(routes: dict[str, httpx.Response]) -> PoliteClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return routes[str(request.url)]

    return PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def _context(config, routes: dict[str, httpx.Response]) -> CollectContext:
    return CollectContext(_client(routes), config, NOW)


def test_registry() -> None:
    assert set(CONNECTORS) == {"talentsoft_rss", "ashby", "workday", "successfactors_search"}


def test_talentsoft_feed(config) -> None:
    company = WatchedCompany(name="Exemple", sector="insurance", careers_url="https://recrutement.exemple.fr", ats="talentsoft_rss")
    feed = (FIXTURES / "talentsoft_feed.xml").read_text(encoding="utf-8")
    url = "https://recrutement.exemple.fr/handlers/offerRss.ashx?LCID=1036"
    offers = talentsoft.collect(company, _context(config, {url: httpx.Response(200, text=feed)}))

    assert len(offers) == 2
    first = offers[0]
    assert first.title == "Data Analyst F/H"
    assert first.reference == "2026-101" and first.source_offer_id == "101"
    assert first.contract_type_raw == "Contrat à durée indéterminée"
    assert first.location == "Niort (79)"
    assert first.published_at == datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc)
    assert "Power BI" in first.description and "<b>" not in first.description
    assert first.source_name == "talentsoft:Exemple" and not first.description_is_partial


def test_ashby_keeps_listed_offers_in_france(config) -> None:
    company = WatchedCompany(name="Exemple", sector="banking", careers_url="https://jobs.ashbyhq.com/exemple", ats="ashby")
    board = (FIXTURES / "ashby_board.json").read_text(encoding="utf-8")
    url = "https://api.ashbyhq.com/posting-api/job-board/exemple"
    offers = ashby.collect(company, _context(config, {url: httpx.Response(200, text=board)}))

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


# --- Workday ---------------------------------------------------------------
WD_SITE = "https://exemple.wd3.myworkdayjobs.com/fr-FR/Carrieres"
WD_API = "https://exemple.wd3.myworkdayjobs.com/wday/cxs/exemple/Carrieres"


def _posting(path: str, title: str, posted: str = "Offre publiée hier") -> dict:
    return {"title": title, "externalPath": path, "locationsText": "Paris", "postedOn": posted,
            "bulletFields": ["REQ-" + path[-3:]]}


class WorkdayFake:
    """Answers like a Workday site; records the requests made."""

    def __init__(self, pages: list[list[dict]], facet_name: str | None = "locationCountry",
                 reject_unknown_facet: bool = False) -> None:
        self.pages = pages
        self.total = sum(len(p) for p in pages)
        self.facet_name = facet_name
        self.reject_unknown_facet = reject_unknown_facet
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        self.requests.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            body = json.loads(request.content)
            facets = body["appliedFacets"]
            if facets and self.facet_name not in facets:
                if self.reject_unknown_facet:
                    return httpx.Response(400, json={"error": "invalid facet"})
                return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})
            listed = [] if not facets and self.facet_name else self.pages
            index = body["offset"] // 20
            page = listed[index] if index < len(listed) else []
            facet_list = [{"facetParameter": self.facet_name,
                           "values": [{"descriptor": "France", "id": "FR-ID"}]}] if self.facet_name else []
            return httpx.Response(200, json={"total": self.total, "jobPostings": page, "facets": facet_list})
        path = request.url.path.split("/Carrieres", 1)[1]
        return httpx.Response(200, json={"jobPostingInfo": {
            "title": "Data Analyst H/F", "jobDescription": "<p>CDI. SQL et Power BI.</p>",
            "startDate": "2026-11-09", "postedOn": "Offre publiée hier", "location": "Paris",
            "timeType": "Temps complet", "jobReqId": "REQ-001", "remoteType": "Hybride",
            "externalUrl": f"https://exemple.wd3.myworkdayjobs.com/Carrieres{path}"}})


def _workday_context(config, fake: WorkdayFake, known: set[str] = frozenset()) -> CollectContext:
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    return CollectContext(client, config, NOW, lambda source, offer_id: offer_id in known)


def test_workday_fetches_details_only_for_new_target_offers(config) -> None:
    fake = WorkdayFake([[
        _posting("/job/Paris/Data-Analyst_REQ-001", "Data Analyst H/F"),
        _posting("/job/Paris/Comptable_REQ-002", "Comptable H/F"),
        _posting("/job/Paris/Data-Engineer_REQ-003", "Data Engineer H/F"),
    ]])
    company = WatchedCompany(name="Exemple", sector="other", careers_url=WD_SITE, ats="workday")
    context = _workday_context(config, fake, known={"/job/Paris/Data-Engineer_REQ-003"})

    offers = workday.collect(company, context)

    details = [r for r in fake.requests if r.startswith("GET")]
    assert details == ["GET /wday/cxs/exemple/Carrieres/job/Paris/Data-Analyst_REQ-001"]
    assert len(offers) == 3
    analyst, accountant, _ = offers
    assert analyst.description == "CDI. SQL et Power BI." and not analyst.description_is_partial
    assert analyst.work_mode == "hybrid" and analyst.reference == "REQ-001"
    assert analyst.published_at.date().isoformat() == "2026-11-09"
    assert accountant.description_is_partial and accountant.reference == "REQ-002"


def test_workday_finds_differently_named_country_filter(config) -> None:
    fake = WorkdayFake([[_posting("/job/Lyon/Data-Analyst_REQ-010", "Comptable")]], facet_name="Location_Country")
    company = WatchedCompany(name="Exemple", sector="other", careers_url=WD_SITE, ats="workday")
    offers = workday.collect(company, _workday_context(config, fake))
    assert len(offers) == 1
    assert len([r for r in fake.requests if r.startswith("POST")]) == 3


def test_workday_stops_on_a_page_of_old_offers(config) -> None:
    recent = [_posting(f"/job/x/Comptable_R-{i:03d}", "Comptable") for i in range(20)]
    old = [_posting(f"/job/x/Comptable_R-{i:03d}", "Comptable", "Offre publiée il y a 30 jours ou plus")
           for i in range(20, 40)]
    never = [_posting(f"/job/x/Comptable_R-{i:03d}", "Comptable") for i in range(40, 60)]
    fake = WorkdayFake([recent, old, never])
    company = WatchedCompany(name="Exemple", sector="other", careers_url=WD_SITE, ats="workday")
    offers = workday.collect(company, _workday_context(config, fake))
    assert len(offers) == 40  # the third page is never requested


@pytest.mark.parametrize(
    ("text", "days"),
    [("Offre publiée aujourd'hui", 0), ("Posted Yesterday", 1), ("Offre publiée il y a 3 jours", 3),
     ("Posted 30+ Days Ago", 30), (None, None)],
)
def test_posted_days_ago(text, days) -> None:
    assert workday.posted_days_ago(text) == days


def test_workday_without_country_filter_reads_everything(config) -> None:
    fake = WorkdayFake([[_posting("/job/Lille/Comptable_REQ-020", "Comptable")]],
                       facet_name=None, reject_unknown_facet=True)
    company = WatchedCompany(name="Exemple", sector="other", careers_url=WD_SITE, ats="workday")
    offers = workday.collect(company, _workday_context(config, fake))
    assert [o.source_offer_id for o in offers] == ["/job/Lille/Comptable_REQ-020"]


# --- SuccessFactors (HTML search page) -------------------------------------
SF_SITE = "https://emplois.exemple.fr"


def _sf_row(offer_id: str, title: str, location: str = "PARIS, FR, 75008") -> str:
    return (f'<tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone">'
            f'<a class="jobTitle-link" href="/job/PARIS-{offer_id}/{offer_id}/">{title}</a></span></td>'
            f'<td class="colLocation"><span class="jobLocation"> {location} </span></td></tr>')


def _sf_page(rows: list[str]) -> str:
    return f"<html><body><table>{''.join(rows)}</table></body></html>"


SF_OFFER = """<html><body>
<span data-careersite-propertyid="title">Data Analyst H/F</span>
<span data-careersite-propertyid="adcode">4242</span>
<span data-careersite-propertyid="customfield2">CDI DUREE INDETERMINEE</span>
<span data-careersite-propertyid="location">PARIS, FR, 75008</span>
<meta itemprop="datePosted" content="Mon Nov 09 02:00:00 UTC 2026"/>
<span itemprop="description"><p>Analyse des ventes avec <b>SQL</b>.</p><ul><li>Power BI</li></ul></span>
</body></html>"""


class SuccessFactorsFake:
    def __init__(self, pages: list[list[str]]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        self.requests.append(str(request.url))
        if request.url.path == "/search/":
            assert request.url.params["sortColumn"] == "referencedate"
            start = int(request.url.params["startrow"])
            index = start // 2  # two offers per fake page
            rows = self.pages[index] if index < len(self.pages) else []
            return httpx.Response(200, text=_sf_page(rows))
        return httpx.Response(200, text=SF_OFFER)


def _sf_context(config, fake, known=frozenset()) -> CollectContext:
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    return CollectContext(client, config, NOW, lambda source, offer_id: offer_id in known)


def test_successfactors_parses_offer_page() -> None:
    details = successfactors.parse_offer_page(SF_OFFER)
    assert details["description"] == "Analyse des ventes avec SQL.\n- Power BI"
    assert details["contract"] == "CDI DUREE INDETERMINEE" and details["reference"] == "4242"
    assert details["published_at"] == datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)


def test_successfactors_collect(config) -> None:
    fake = SuccessFactorsFake([
        [_sf_row("1000001", "Data Analyst H/F"), _sf_row("1000002", "Comptable H/F")],
        [_sf_row("1000003", "Data Engineer H/F", "LONDON, GB, SW1"), _sf_row("1000004", "Data Scientist")],
        [_sf_row("1000005", "Data Analyst Senior")],
    ])
    company = WatchedCompany(name="Exemple", sector="other", careers_url=SF_SITE, ats="successfactors_search")
    offers = successfactors.collect(company, _sf_context(config, fake))

    ids = [o.source_offer_id for o in offers]
    assert ids == ["1000001", "1000002", "1000004", "1000005"]  # London offer skipped
    detail_requests = [r for r in fake.requests if "/job/" in r]
    assert len(detail_requests) == 3  # Comptable is not opened
    analyst = offers[0]
    assert analyst.contract_type_raw == "CDI DUREE INDETERMINEE" and not analyst.description_is_partial
    assert offers[1].description_is_partial


def test_successfactors_stops_on_known_page(config) -> None:
    fake = SuccessFactorsFake([
        [_sf_row("2000001", "Comptable"), _sf_row("2000002", "Comptable")],
        [_sf_row("2000003", "Comptable"), _sf_row("2000004", "Comptable")],
    ])
    company = WatchedCompany(name="Exemple", sector="other", careers_url=SF_SITE, ats="successfactors_search")
    offers = successfactors.collect(company, _sf_context(config, fake, known={"2000001", "2000002"}))
    assert len(offers) == 2 and len(fake.requests) == 1


def test_successfactors_search_url_keeps_site_filters() -> None:
    url = successfactors.search_url("https://jobs.exemple.com/search/?q=&locationsearch=France", 40)
    assert url.startswith("https://jobs.exemple.com/search/?")
    assert "locationsearch=France" in url and "startrow=40" in url and "sortDirection=desc" in url
