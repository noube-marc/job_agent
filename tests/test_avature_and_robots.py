"""Avature connector (fake site) and RFC 9309 robots.txt rules."""

from datetime import date, timedelta

import httpx

from conftest import NOW
from job_agent.collectors import avature
from job_agent.collectors.base import CollectContext, PoliteClient
from job_agent.collectors.robots import RobotRules
from job_agent.config.models import WatchedCompany

UA = "job-agent/0.1 (+https://github.com/noube-marc/job_agent)"
SITE = "https://jobs.exemple.com/fr_FR/careers/SearchJobs/"

ROBOTS = """
User-agent: *
Allow: /$
Allow: /careers
Disallow: /careers/*qtvc=
Allow: /*/careers
Disallow: /*/careers/*qtvc=
Disallow: /
"""


def test_most_specific_rule_wins() -> None:
    rules = RobotRules(ROBOTS)
    assert rules.can_fetch(UA, "https://jobs.exemple.com/fr_FR/careers/SearchJobs/?jobOffset=20")
    assert rules.can_fetch(UA, "https://jobs.exemple.com/")
    assert not rules.can_fetch(UA, "https://jobs.exemple.com/fr_FR/careers/SearchJobs/?qtvc=abc")
    assert not rules.can_fetch(UA, "https://jobs.exemple.com/autre/page")


def test_own_group_and_tie_and_encoding() -> None:
    rules = RobotRules(
        "User-agent: *\nDisallow: /\n\nUser-agent: job-agent\nDisallow: /prive\nAllow: /prive\n"
        "Disallow: /recherche-d'offres/\nCrawl-delay: 4\n"
    )
    assert rules.can_fetch(UA, "https://x.fr/public")  # own group replaces "*"
    assert rules.can_fetch(UA, "https://x.fr/prive")  # Allow wins a tie
    assert not rules.can_fetch(UA, "https://x.fr/recherche-d%27offres/")
    assert rules.crawl_delay(UA) == 4
    assert not RobotRules(disallow_all=True).can_fetch(UA, "https://x.fr/")
    assert RobotRules("").can_fetch(UA, "https://x.fr/tout")


def _article(job_id: str, title: str, day: date, country: str = "France", contract: str = "CDI") -> str:
    return f"""
<div class="article article--result">
  <h3 class="article__header__text__title"><a class="link" href="https://jobs.exemple.com/fr_FR/careers/JobDetail/x/{job_id}">{title}</a></h3>
  <ul class="list article__header__text__subtitle">
    <li class="list-item list-item-jobCreationDate">{day:%d-%m-%Y}</li>
    <li class="list-item list-item-jobCountry">{country}</li>
    <li class="list-item list-item-employmentType">{contract}</li>
    <li class="list-item list-item-jobEmployerCompany">Filiale Exemple</li>
  </ul>
</div>"""


DETAIL = """
<div class="article__content__view__field"><div class="article__content__view__field__label">Pays</div>
  <div class="article__content__view__field__value">France</div></div>
<div class="article__content__view__field"><div class="article__content__view__field__label">Ville</div>
  <div class="article__content__view__field__value">COURBEVOIE</div></div>
<div class="article__content__view__field"><div class="article__content__view__field__value">Area 92 - Hauts-de-Seine</div></div>
<div class="article__content__view__field"><div class="article__content__view__field__label">Type de contrat</div>
  <div class="article__content__view__field__value">CDI</div></div>
<div class="article__content__view__field"><div class="article__content__view__field__label">Expérience</div>
  <div class="article__content__view__field__value">Minimum 2 ans</div></div>
<div class="article__content__view__field"><div class="article__content__view__field__value">
  <p>Vous construisez les tableaux de bord Power BI et les requêtes SQL de la direction financière.</p>
  <p>""" + "Missions détaillées. " * 15 + """</p></div></div>
<div class="article__content__view__field"><div class="article__content__view__field__value">
  <p>""" + "Présentation du groupe et de ses engagements. " * 10 + """</p></div></div>
"""


class AvatureFake:
    def __init__(self, pages: list[list[str]]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        self.requests.append(str(request.url))
        if "SearchJobs" in request.url.path:
            assert "qtvc" not in str(request.url)
            index = int(request.url.params["jobOffset"]) // avature.PAGE_SIZE
            articles = self.pages[index] if index < len(self.pages) else []
            return httpx.Response(200, text="<html>" + "".join(articles) + "</html>")
        return httpx.Response(200, text=DETAIL)


def test_avature_collect(config) -> None:
    today = NOW.date()
    fake = AvatureFake([
        [_article("1", "Data Analyst H/F", today), _article("2", "Data Engineer", today, country="Royaume-Uni")],
        [_article("3", "Comptable H/F", today - timedelta(days=2)), _article("4", "Data Scientist", today - timedelta(days=3))],
        [_article("5", "Data Analyst", today - timedelta(days=20)), _article("6", "BI Analyst", today - timedelta(days=21))],
        [_article("7", "jamais lue", today - timedelta(days=30))],
    ])
    client = PoliteClient(0, 5, transport=httpx.MockTransport(fake), sleep=lambda s: None)
    company = WatchedCompany(name="Exemple", sector="other", careers_url=SITE, ats="avature")
    context = CollectContext(client, config, NOW, lambda source, offer_id: offer_id == "4")
    offers = avature.collect(company, context)

    assert [o.source_offer_id for o in offers] == ["1", "3", "4", "5", "6"]  # UK offer skipped
    assert not any("jobOffset=60" in r for r in fake.requests)  # stops after a page older than 7 days
    details = [r for r in fake.requests if "JobDetail" in r]
    assert len(details) == 3  # 1, 5 and 6: not the "Comptable", not the known offer 4
    first = offers[0]
    assert first.location == "COURBEVOIE (92)" and first.department_code == "92"
    assert first.contract_type_raw == "CDI" and first.experience_raw == "Minimum 2 ans"
    assert first.description.startswith("Vous construisez") and "Présentation du groupe" not in first.description
    assert first.published_at.date() == today and first.published_at_source == "career_site"
