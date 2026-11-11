"""Crédit Agricole group job site connector, on fake pages."""

import httpx

from conftest import NOW
from job_agent.collectors import credit_agricole
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

SITE = "https://groupecreditagricole.jobs/fr/nos-marques/assurance-exemple/nos-offres/"


def _card(reference: str, title: str, country: str = "France", contract: str = "CDI") -> str:
    url = f"https://groupecreditagricole.jobs/fr/nos-offres-emploi/1-2-3-x-reference--{reference}--/"
    return f"""
<article class="card offer detail" data-gtm-jobcity="PARIS - France" data-gtm-jobcontract="{contract}"
  data-gtm-jobcountry="{country}" data-gtm-jobentity="Assurance Exemple" data-gtm-jobtitle="{title}"
  data-gtm-jobpublishdate="Mis à jour le 11/11/2026">
  <h3 class="offer-title"><a href="{url}">{title}</a></h3>
  <input data-reference="{reference}" type="checkbox"/>
  <div class="offer-publication-date"><p>Mis à jour le 11/11/2026</p></div>
</article>"""


OFFER_PAGE = """
<div class="side-menu-content-middle"><p class="publication-date">Modifiée le 11/11/2026</p>
  <p class="publication-date">Publiée le 07/11/2026</p></div>
<section class="block offer-content text-container"><h2>Description du poste</h2>
  <p>Vous analysez les sinistres avec <b>SQL</b> et Power BI.</p></section>
<section class="block accordions"><div class="accordion-item-content">
  <p>Expérience : 2 ans minimum</p></div>
  <div class="accordion-item-content"><p>Présentation de l'entreprise.</p></div></section>
"""


class Fake:
    def __init__(self, pages: list[list[str]]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /wp-admin/\n")
        self.requests.append(request.url.path)
        if "/nos-offres-emploi/" in request.url.path:
            return httpx.Response(200, text=OFFER_PAGE)
        path = request.url.path
        page = int(path.rstrip("/").rsplit("/", 1)[-1]) if "/page/" in path else 1
        cards = self.pages[page - 1] if page <= len(self.pages) else []
        return httpx.Response(200, text="<main>" + "".join(cards) + "</main>")


def test_collect(config) -> None:
    fake = Fake([
        [_card("2026-1", "Data Analyst H/F"), _card("2026-2", "Gestionnaire de sinistres H/F")],
        [_card("2026-3", "Data Engineer H/F", country="Irlande"), _card("2026-4", "Data Scientist H/F")],
    ])
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Assurance Exemple", sector="insurance", careers_url=SITE, ats="credit_agricole_jobs")
    offers = credit_agricole.collect(company, CollectContext(client, config, NOW, lambda s, i: i == "2026-4"))

    assert [o.source_offer_id for o in offers] == ["2026-1", "2026-2", "2026-4"]  # Irish offer skipped
    assert [p for p in fake.requests if "/page/" in p] == [
        "/fr/nos-marques/assurance-exemple/nos-offres/page/2/", "/fr/nos-marques/assurance-exemple/nos-offres/page/3/"]
    assert len([p for p in fake.requests if "/nos-offres-emploi/" in p]) == 1  # only the new Data Analyst
    first = offers[0]
    # The real publication date of the offer page, not the update date of the list.
    assert first.published_at.date().isoformat() == "2026-11-07"
    assert "SQL" in first.description and "2 ans minimum" in first.description
    assert "Présentation de l'entreprise" not in first.description
    assert first.location == "PARIS" and first.contract_type_raw == "CDI" and first.reference == "2026-1"
    assert offers[1].published_at is None  # not a target title: page not opened
