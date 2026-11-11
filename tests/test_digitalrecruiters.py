"""Digital Recruiters connector, on a fake service."""

import json

import httpx

from conftest import NOW
from job_agent.collectors import digitalrecruiters
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.config.models import WatchedCompany

SITE = "https://recrute.exemple.fr/fr/annonces"


def _item(job_ad_id: int, title: str) -> dict:
    return {"id": f"{job_ad_id}-1", "job_ad_id": job_ad_id, "title": title, "contract": "CDI", "location": "Paris",
            "url": f"{job_ad_id}-slug", "career_domain": "recrutement.enseigne.fr"}


DETAIL = {
    "description": "<div>Vous analysez les ventes avec <b>SQL</b>.</div>", "profile": "<div>Rigueur.</div>",
    "job_experience": "Au moins 2 ans d'expérience", "education_level": "Bac +5",
    "republished_at": "2026-11-10 08:00:00", "location": "75008 Paris", "brand_name": "Enseigne",
    "address": {"zip": "75008", "city": "Paris", "country": "France"}, "apply_email": None,
}


class Fake:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.details: list[str] = []
        self.pages = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.params["domainName"] == "recrute.exemple.fr" and request.url.params["locale"] == "fr_FR"
        if request.method == "POST":
            self.pages += 1
            assert json.loads(request.content) == {"filters": {}}
            limit, page = int(request.url.params["limit"]), int(request.url.params["page"])
            return httpx.Response(200, json={"count": len(self.items),
                                             "items": self.items[(page - 1) * limit:page * limit]})
        self.details.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=DETAIL)


def test_collect(config, monkeypatch) -> None:
    monkeypatch.setattr(digitalrecruiters, "PAGE_SIZE", 2)
    fake = Fake([_item(1, "Data Analyst F/H"), _item(2, "Employé de rayon F/H"), _item(3, "Data Scientist F/H")])
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="retail", careers_url=SITE, ats="digitalrecruiters")
    offers = digitalrecruiters.collect(company, CollectContext(client, config, NOW, lambda s, i: i == "3-1"))

    assert [o.source_offer_id for o in offers] == ["1-1", "2-1", "3-1"]
    assert fake.pages == 2 and fake.details == ["1"]  # only the new target offer is detailed
    first = offers[0]
    assert first.url == "https://recrutement.enseigne.fr/fr/annonce/1-slug"  # the brand's own career site
    assert "SQL" in first.description and "Rigueur" in first.description
    assert first.experience_raw == "Au moins 2 ans d'expérience" and first.location == "75008 Paris"
    assert first.published_at.date().isoformat() == "2026-11-10"
    assert offers[1].description == "" and offers[1].contract_type_raw == "CDI"
