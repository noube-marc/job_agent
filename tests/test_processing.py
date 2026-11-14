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


def _with_contracts(config, *accepted):
    return config.model_copy(update={"contracts": config.contracts.model_copy(update={"accepted": list(accepted)})})


def test_professionalisation_accepted_without_work_study(config) -> None:
    settings = _with_contracts(config, "permanent", "fixed_term", "professionalisation")
    graduate = make_offer(
        title="Assistant data & reporting finance débutant H/F",
        contract_type=None,
        contract_type_raw="Contrat de professionnalisation temps plein",
        description=(
            "Contrat de professionnalisation temps plein qualifiant de 12 mois (100% en entreprise). "
            "Ce poste ne s'adresse pas aux personnes recherchant une alternance avec une école."
        ),
    )
    work_study = make_offer(
        title="Data Analyst en alternance H/F",
        contract_type=None,
        contract_type_raw="Alternance",
        description="Contrat d'apprentissage ou de professionnalisation de 12 mois, rythme d'alternance.",
    )
    assert evaluate(graduate, settings, NOW).kept or "intitulé hors cible" in evaluate(graduate, settings, NOW).reasons
    assert not any("contrat" in r for r in evaluate(graduate, settings, NOW).reasons)
    assert not evaluate(work_study, settings, NOW).kept
    # Without professionalisation in the accepted contracts, both are rejected.
    assert any("contrat" in r for r in evaluate(graduate, config, NOW).reasons)


def test_professionalisation_announced_as_fixed_term(config) -> None:
    settings = _with_contracts(config, "permanent", "fixed_term", "professionalisation")
    offer = make_offer(contract_type="fixed_term", description="CDD : contrat de professionnalisation de 12 mois.")
    school = make_offer(title="Alternance - Data Analyst", contract_type="fixed_term")
    assert evaluate(offer, settings, NOW).kept
    assert not evaluate(offer, config, NOW).kept
    assert not evaluate(school, settings, NOW).kept


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
        ("Architecte Data", "poste trop avancé"),
        ("Technicien de maintenance", "intitulé hors cible"),
    ],
)
def test_rejected_titles(config, title: str, reason: str) -> None:
    result = evaluate(make_offer(title=title), config, NOW)
    assert not result.kept
    assert any(reason in r for r in result.reasons)


@pytest.mark.parametrize(
    ("title", "trigger"),
    [
        ("Data Analyst H/F", "intitulé recherché « Data Analyst »"),
        ("Assistant data & reporting finance débutant H/F", "terme de base « data »"),
        ("Analyste de données", "terme de base « donnée »"),
        ("Chargé de reporting", "terme de base « reporting »"),
        ("Chargée d'études statistiques marketing", "intitulé recherché « Chargé d'études statistiques »"),
        ("Responsable pilotage de la performance", "mot-clé « pilotage »"),
        ("Ingénieur Databricks", "outil « Databricks »"),
    ],
)
def test_wider_title_search_tells_what_selected_the_offer(config, title: str, trigger: str) -> None:
    result = evaluate(make_offer(title=title), config, NOW)
    assert result.kept, result.reasons
    assert f"sélection : {trigger}" in result.flags


def test_sector_base_term_counts_only_for_that_sector(config) -> None:
    offer = make_offer(title="Contrôleur de gestion H/F")
    bank = evaluate(offer, config, NOW, "banking")
    assert bank.kept and "sélection : terme de base « contrôleur de gestion »" in bank.flags
    assert not evaluate(offer, config, NOW, "insurance").kept
    assert not evaluate(offer, config, NOW).kept


@pytest.mark.parametrize("title", ["Chef de projet data H/F", "Chargé d'études juridiques"])
def test_project_manager_and_other_studies_are_rejected(config, title: str) -> None:
    assert not evaluate(make_offer(title=title), config, NOW).kept


def test_without_title_match_only_searched_titles_count(config) -> None:
    from job_agent.config.models import TitleMatchConfig

    search = config.search.model_copy(update={"title_match": TitleMatchConfig()})
    narrow = config.model_copy(update={"search": search})
    assert not evaluate(make_offer(title="Chargé de reporting"), narrow, NOW).kept
    assert evaluate(make_offer(title="Data Analyst H/F"), narrow, NOW).kept


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
    return signature("AXA", title, detect_department(location), "permanent", "", None, location)


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
    same = make_offer(
        source="email_alert",
        source_name="email:apec",
        source_offer_id="A",
        title="Data Analyst (F/H)",
        location="Paris",
    )
    other = make_offer(source_offer_id="2", title="Data Engineer H/F")

    counts = process_offers([first, same, other], config, storage, sectors, NOW)
    again = process_offers([first], config, storage, sectors, NOW)

    assert counts["duplicates"] == 1 and counts["kept"] == 2
    assert again["already_seen"] == 1
    assert len(storage.pending_notifications()) == 2
    row = storage.pending_notifications()[0]
    assert row["sector"] == "insurance" and row["priority_sector"] == 1
    storage.close()


# Real false matches seen on the first real run (10/11/2026).
@pytest.mark.parametrize(
    ("first", "second"),
    [
        (
            ("Conseiller commercial F/H", "Clermont-Ferrand", "fixed_term", None),
            ("Conseiller Commercial - LAON F/H", "LAON", "fixed_term", None),
        ),
        (
            ("Gestionnaire indemnisation CDD - Rouen F/H", "rouen", "fixed_term", None),
            ("Gestionnaire Indemnisation CDD - Montauban F/H", "montauban", "fixed_term", None),
        ),
        (
            ("Juriste Conseil - CDI - Rouen F/H", "Rouen", "permanent", None),
            ("Juriste Conseil - CDD 6 mois - Rouen F/H", "Rouen", "fixed_term", None),
        ),
        (
            ("CEO Founder Associate", "Paris, France", None, None),
            ("CTO Founder Associate", "Paris, France", None, None),
        ),
        (
            ("Conseiller commercial", "Lyon", "permanent", "2026-1"),
            ("Conseiller commercial", "Lyon", "permanent", "2026-2"),
        ),
    ],
)
def test_different_offers_are_not_merged(config, first, second) -> None:
    def sig(values):
        title, location, contract, reference = values
        return signature("MAIF", title, detect_department(location), contract, "", reference, location)

    assert similarity(sig(first), sig(second)) < config.source_verification.probable_match_threshold


def test_same_offer_with_city_in_one_title(config) -> None:
    a = signature("MAIF", "Data Analyst - Niort F/H", "79", "permanent", "", None, "Niort")
    b = signature("MAIF", "Data Analyst", "79", "permanent", "", None, "Niort (79)")
    assert similarity(a, b) >= config.source_verification.match_threshold


def test_experience_range_with_unicode_dash() -> None:
    text = "AML experience: you have 1\u20133 years in transaction monitoring."
    assert extract_min_experience(None, text) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Poste en CDI basé à Paris.", "permanent"),
        ("CDD de 6 mois.", "fixed_term"),
        ("CDI ou CDD selon profil.", None),
        ("Rejoignez-nous !", None),
    ],
)
def test_contract_from_description(text, expected) -> None:
    from job_agent.processing.extract import contract_from_description

    assert contract_from_description(text) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Data Analyst Marketing F/H", True),
        ("Business Analyst", True),
        ("Comptable", False),
        ("Stage Data Analyst", False),
        ("Consultant Data Engineer", False),
    ],
)
def test_title_is_candidate(config, title, expected) -> None:
    from job_agent.processing.filters import title_is_candidate

    assert title_is_candidate(title, config) is expected


@pytest.mark.parametrize(
    "title", ["Data Engineer / Architecte Data Junior F/H", "Lead Data Analyst débutant", "Data Engineer Junior"]
)
def test_junior_titles_are_never_too_advanced(config, title) -> None:
    offer = make_offer(
        title=title, description="Stack : Kafka, Scala et Kubernetes. Encadrement d'équipe. 2 ans d'expérience."
    )
    result = evaluate(offer, config, NOW)
    assert result.kept and result.junior
    assert "poste junior" in result.flags
    assert any("non bloquant" in flag for flag in result.flags)


def test_junior_still_follows_experience_rule(config) -> None:
    result = evaluate(make_offer(title="Data Analyst Junior", experience_raw="5 ans minimum"), config, NOW)
    assert not result.kept


def test_junior_offers_come_first_on_the_same_day(config, tmp_path) -> None:
    from datetime import timedelta

    storage = Storage(tmp_path / "t.db")
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    day = NOW - timedelta(hours=10)
    offers = [
        make_offer(
            source_offer_id="1", title="Data Analyst", company="Inconnue", published_at=day + timedelta(hours=3)
        ),
        make_offer(source_offer_id="2", title="Data Engineer Junior", company="Autre", published_at=day),
        make_offer(
            source_offer_id="3", title="Data Scientist", company="Société X", published_at=NOW - timedelta(days=2)
        ),
    ]
    process_offers(offers, config, storage, sectors, NOW)
    titles = [row["title"] for row in storage.pending_notifications()]
    storage.close()
    assert titles == ["Data Engineer Junior", "Data Analyst", "Data Scientist"]


def test_existing_database_gets_the_junior_column(tmp_path) -> None:
    import sqlite3

    from job_agent.storage import SCHEMA

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA.replace("    junior INTEGER NOT NULL DEFAULT 0,\n", ""))  # previous version
    old.close()
    storage = Storage(path)
    columns = {row[1] for row in storage.connection.execute("PRAGMA table_info(offers)")}
    storage.close()
    assert "junior" in columns


def _beginner_controller(config):
    from job_agent.config.models import ConditionalTerms

    rule = ConditionalTerms(terms=["contrôleur de gestion"], max_experience_years=2, allow_excluded_employers=True)
    title_match = config.search.title_match.model_copy(update={"conditional_terms": [rule]})
    search = config.search.model_copy(update={"title_match": title_match})
    return config.model_copy(update={"search": search})


@pytest.mark.parametrize(
    ("title", "description", "kept"),
    [
        ("Contrôleur de gestion junior", "Première expérience appréciée.", True),
        ("Contrôleur de gestion", "Expérience de 2 ans en contrôle de gestion.", True),
        ("Contrôleur de gestion", "Expérience de 3 ans minimum.", False),
        ("Contrôleur de gestion confirmé", "Expérience de 1 an.", False),
        ("Contrôleur de gestion", "Rattaché au directeur financier.", False),  # experience unknown, not for beginners
        ("Contrôleur de gestion", "Poste ouvert aux jeunes diplômés.", True),
    ],
)
def test_conditional_terms_only_for_beginners(config, title: str, description: str, kept: bool) -> None:
    settings = _beginner_controller(config)
    result = evaluate(make_offer(title=title, description=description), settings, NOW, "insurance")
    assert result.kept is kept, result.reasons


def test_conditional_terms_accept_consulting_firms(config) -> None:
    settings = _beginner_controller(config)
    excluded = settings.search.excluded_companies[0]
    offer = make_offer(title="Contrôleur de gestion junior", company=f"Cabinet {excluded}")
    assert evaluate(offer, settings, NOW).kept
    data = make_offer(title="Data Analyst", company=f"Cabinet {excluded}")
    assert not evaluate(data, settings, NOW).kept  # the usual rule still applies elsewhere


def test_bank_controller_keeps_the_usual_rules(config) -> None:
    settings = _beginner_controller(config)
    offer = make_offer(title="Contrôleur de gestion", description="Expérience de 4 ans.")
    assert evaluate(offer, settings, NOW, "banking").kept  # base term of the banking sector
    assert not evaluate(offer, settings, NOW, "insurance").kept
