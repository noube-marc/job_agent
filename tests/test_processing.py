"""Tests for extraction, filters and deduplication."""

from datetime import timedelta

import pytest

from conftest import NOW, ROOT, make_offer
from job_agent.processing.dedup import signature, similarity
from job_agent.processing.extract import detect_department, extract_min_experience
from job_agent.processing.filters import FLAG_EXPERIENCE_UNSPECIFIED, evaluate
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage


# --- 3. minimum experience (examples of CLAUDE.md, section 6) -----------
@pytest.mark.parametrize(
    ("text", "expected"),
    [("2 ans", 2), ("3 à 5 ans", 3), ("5 ans minimum", 5), ("5 ans et plus", 5), (None, None)],
)
def test_minimum_experience(text: str | None, expected: int | None) -> None:
    assert extract_min_experience(text) == expected


def test_experience_rule_keeps_and_rejects(config) -> None:
    assert evaluate(make_offer(experience_raw="3 à 5 ans"), config, NOW).kept
    assert not evaluate(make_offer(experience_raw="5 ans minimum"), config, NOW).kept
    unspecified = evaluate(make_offer(description="Analyse de données en SQL."), config, NOW)
    assert unspecified.kept
    assert FLAG_EXPERIENCE_UNSPECIFIED in unspecified.flags


def test_experience_ignores_unrelated_numbers() -> None:
    text = "Groupe créé il y a 30 ans. Bac+5, expérience de 2 ans minimum en SQL."
    assert extract_min_experience(None, text) == 2


# --- 4. contracts --------------------------------------------------------
def test_internship_announced_as_fixed_term_is_rejected(config) -> None:
    offer = make_offer(title="Stage Data Analyst H/F", contract_type="fixed_term")
    result = evaluate(offer, config, NOW)
    assert not result.kept
    assert any("contrat incohérent" in reason for reason in result.reasons)


def test_internship_and_interim_contracts_are_rejected(config) -> None:
    assert not evaluate(make_offer(contract_type="internship"), config, NOW).kept
    interim = make_offer(contract_type=None, contract_type_raw="Intérim")
    assert not evaluate(interim, config, NOW).kept


# --- 7. freshness --------------------------------------------------------
def test_freshness_limit(config) -> None:
    old = make_offer(published_at=NOW - timedelta(days=8))
    recent = make_offer(published_at=NOW - timedelta(days=6))
    assert not evaluate(old, config, NOW).kept
    assert evaluate(recent, config, NOW).kept


def test_unknown_date_uses_first_seen_and_is_flagged(config) -> None:
    result = evaluate(make_offer(published_at=None, published_at_source=None), config, NOW)
    assert result.kept
    assert "date estimée" in result.flags


# --- titles and seniority ------------------------------------------------
@pytest.mark.parametrize(
    "title",
    ["Data Analyste (F/H)", "Chargée d'études statistiques", "Statisticienne", "Data Engineer Azure"],
)
def test_target_titles_are_kept(config, title: str) -> None:
    assert evaluate(make_offer(title=title), config, NOW).kept


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Comptable H/F", "intitulé hors cible"),
        ("Consultante Data Analyst", "intitulé exclu"),
        ("Lead Data Engineer", "poste trop avancé"),
        ("Architecte Data", "intitulé hors cible"),
    ],
)
def test_rejected_titles(config, title: str, reason: str) -> None:
    result = evaluate(make_offer(title=title), config, NOW)
    assert not result.kept
    assert any(reason in r for r in result.reasons)


def test_business_analyst_needs_technical_skills(config) -> None:
    weak = make_offer(title="Business Analyst", description="Recueil du besoin métier. 2 ans d'expérience.")
    strong = make_offer(title="Business Analyst", description="Requêtes SQL et rapports Power BI. 2 ans d'expérience.")
    assert not evaluate(weak, config, NOW).kept
    assert evaluate(strong, config, NOW).kept


def test_blocking_technologies(config) -> None:
    three = make_offer(description="Stack : Kafka, Scala et Kubernetes. 2 ans d'expérience.")
    one = make_offer(description="Stack : Kafka. 2 ans d'expérience.")
    optional = make_offer(description="Kafka, Scala et Kubernetes seraient un plus. 2 ans d'expérience.")
    assert not evaluate(three, config, NOW).kept
    result = evaluate(one, config, NOW)
    assert result.kept and any("écart technique" in f for f in result.flags)
    assert evaluate(optional, config, NOW).kept


# --- 5. deduplication ----------------------------------------------------
def _sig(title: str, location: str) -> object:
    return signature("AXA", title, detect_department(location), "permanent", "", None)


def test_same_offer_from_two_sources(config) -> None:
    score = similarity(_sig("Data Analyst H/F", "Paris 8e"), _sig("Data Analyst (F/H)", "Paris"))
    assert score >= config.source_verification.match_threshold


def test_different_jobs_of_same_company(config) -> None:
    score = similarity(_sig("Data Analyst", "Paris"), _sig("Data Engineer", "Paris"))
    assert score < config.source_verification.probable_match_threshold


def test_pipeline_groups_duplicates(config, tmp_path) -> None:
    storage = Storage(tmp_path / "test.db")
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    first = make_offer(source_offer_id="1", title="Data Analyst H/F", location="Paris 8e")
    same = make_offer(source="email_alert", source_name="email:apec", source_offer_id="A",
                      title="Data Analyst (F/H)", location="Paris")
    other = make_offer(source_offer_id="2", title="Data Engineer H/F")

    counts = process_offers([first, same, other], config, storage, sectors, NOW)
    again = process_offers([first], config, storage, sectors, NOW)

    assert counts["duplicates"] == 1 and counts["kept"] == 2
    assert again["already_seen"] == 1
    assert len(storage.pending_notifications()) == 2
    row = storage.pending_notifications()[0]
    assert row["sector"] == "insurance" and row["priority_sector"] == 1
    storage.close()
