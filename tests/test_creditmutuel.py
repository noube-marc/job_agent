"""Crédit Mutuel connector, on a fake site."""

from datetime import timedelta

import httpx

from conftest import NOW
from job_agent.collectors import creditmutuel
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

SITE_MAP_URL = "https://recrutement.exemple.fr/fr/sitemap_offres.aspx"


def _site_map() -> str:
    recent, old = NOW.date() - timedelta(days=2), NOW.date() - timedelta(days=40)
    entries = [("101", recent), ("102", recent), ("103", old), ("104", recent)]
    return "<urlset>" + "".join(
        f"<url><loc>https://recrutement.exemple.fr/fr/offre.html?annonce={i}</loc><lastmod>{d.isoformat()}</lastmod></url>"
        for i, d in entries) + "</urlset>"


def _row(label: str, value: str) -> str:
    return (f'<tr><td><span class="ei_root_icon" title="{label}"><span class="invisible">{label}</span></span></td>'
            f"<td></td><td><em>{value}</em></td></tr>")


PAGE = ("<html><h1>Data Scientist (F/H)</h1><div class='ei_card_body'><table>"
        + _row("Type de contrat", "CDI <span> ()</span>") + _row("Localisation", "PARIS (75)")
        + _row("Date de publication", "10/11/2026") + _row("Référence", "A1")
        + "</table></div><div><h2>Votre mission</h2><p>" + "Modèles de risque en Python. " * 20 + "</p></div>"
        + "<div class='rhec_offres'>Ces autres offres peuvent vous intéresser</div></html>")


class Fake:
    def __init__(self, failing: set[str] = frozenset()) -> None:
        self.failing = failing
        self.opened: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path.endswith("sitemap_offres.aspx"):
            return httpx.Response(200, text=_site_map())
        offer_id = request.url.params["annonce"]
        self.opened.append(offer_id)
        if offer_id in self.failing:
            raise httpx.RemoteProtocolError("connexion coupée")
        return httpx.Response(200, text=PAGE)


def _collect(config, fake: Fake, known=frozenset()):
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Crédit Mutuel", sector="banking", careers_url=SITE_MAP_URL, ats="creditmutuel")
    return creditmutuel.collect(company, CollectContext(client, config, NOW, lambda s, i: i in known))


def test_collect(config) -> None:
    fake = Fake()
    offers = _collect(config, fake, known={"104"})
    assert [o.source_offer_id for o in offers] == ["101", "102", "104"]  # 103 too old: not read
    assert fake.opened == ["101", "102"]  # the known offer 104 is reported, not read again
    first = offers[0]
    assert first.title == "Data Scientist (F/H)" and first.contract_type_raw == "CDI" and first.location == "PARIS (75)"
    assert first.published_at.date().isoformat() == "2026-11-10" and first.reference == "A1"
    assert first.description.startswith("Votre mission") and "autres offres" not in first.description


def test_dropped_connections_do_not_stop_the_company(config) -> None:
    fake = Fake(failing={"101"})
    offers = _collect(config, fake)
    assert [o.source_offer_id for o in offers] == ["102", "104"]  # 101 is read again at the next run
