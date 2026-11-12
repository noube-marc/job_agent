"""AI extraction with a fake Gemini client (no real call)."""

import json
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.ai.enrich import compare_with_rules, enrich_new_offers, fill_gaps
from job_agent.ai.evaluation import evaluate_sheet, write_sheet
from job_agent.ai.extraction import AIExtraction, AIUnavailable, GeminiExtractor
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage

DESCRIPTION = (
    "Vous construisez des tableaux de bord Power BI et des requêtes SQL pour les équipes métier. "
    "Kafka serait un plus. Poste en CDI à Paris, 2 jours de télétravail. 2 ans d'expérience. "
) * 3


class ApiError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"error {code}")
        self.code = code


class FakeModels:
    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        answer = self.answers[model]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(text=json.dumps(answer))


def _extractor(answers: dict[str, object], models=("m1", "m2")) -> tuple[GeminiExtractor, FakeModels]:
    fake = FakeModels(answers)
    return GeminiExtractor("key", list(models), pause_seconds=0, client=SimpleNamespace(models=fake)), fake


GOOD = {
    "contract_type": "permanent",
    "min_experience_years": 2,
    "required_skills": ["SQL", "Power BI"],
    "optional_skills": ["Kafka"],
    "work_mode": "hybrid",
    "seniority": "confirmed",
}


def test_overloaded_model_falls_back_to_the_next() -> None:
    extractor, fake = _extractor({"m1": ApiError(503), "m2": GOOD})
    extraction, model = extractor.extract("Data Analyst", "Exemple", DESCRIPTION)
    assert model == "m2" and extraction.required_skills == ["SQL", "Power BI"]
    assert fake.calls == ["m1", "m2"]


def test_refused_key_stops_immediately() -> None:
    extractor, fake = _extractor({"m1": ApiError(401), "m2": GOOD})
    with pytest.raises(AIUnavailable):
        extractor.extract("Data Analyst", "Exemple", DESCRIPTION)
    assert fake.calls == ["m1"]


def test_invalid_answer_is_not_accepted() -> None:
    extractor, _ = _extractor({"m1": {"contract_type": "CDI magique"}, "m2": ApiError(429)})
    with pytest.raises(AIUnavailable):
        extractor.extract("Data Analyst", "Exemple", DESCRIPTION)


def _stored(config, tmp_path, *offers):
    storage = Storage(tmp_path / "t.db")
    process_offers(list(offers), config, storage, SectorIndex.build(config, ROOT / "sectors.yaml"), NOW)
    return storage


def test_enrichment_is_cached_and_skips_notified_offers(config, tmp_path) -> None:
    storage = _stored(
        config,
        tmp_path,
        make_offer(source_offer_id="1", description=DESCRIPTION),
        make_offer(source_offer_id="2", title="Data Engineer", description=DESCRIPTION),
    )
    notified = storage.pending_notifications()[1]["group_id"]
    storage.mark_notified([notified], "telegram")
    extractor, fake = _extractor({"m1": GOOD})

    first = enrich_new_offers(storage, config, extractor)
    second = enrich_new_offers(storage, config, extractor)

    assert first["analysed"] == 1 and second["analysed"] == 0
    assert len(fake.calls) == 1  # never two calls for the same offer
    storage.close()


def test_ai_warnings(config, tmp_path) -> None:
    """The rules found a value (CDI, 2 years): the AI only adds warnings."""
    storage = _stored(config, tmp_path, make_offer(description="Stack Kafka. Expérience de 2 ans. " * 10))
    row = storage.pending_notifications()[0]
    ai = AIExtraction(
        contract_type="internship", min_experience_years=6, required_skills=["SQL"], seniority="lead_or_manager"
    )
    flags = compare_with_rules(row, ai, config)
    assert "IA : stage selon la description (à vérifier)" in flags
    assert "IA : 6 ans d'expérience minimum (à vérifier)" in flags
    assert "IA : poste avec encadrement (à vérifier)" in flags
    assert "IA : technologies non maîtrisées seulement facultatives" in flags
    storage.close()


def test_annotation_sheet_and_evaluation(config, tmp_path) -> None:
    from job_agent.ai.evaluation import ai_answers_for, sheet_offer_ids

    storage = _stored(
        config,
        tmp_path,
        make_offer(source_offer_id="1", description=DESCRIPTION),
        make_offer(source_offer_id="2", title="Data Engineer", description=DESCRIPTION),
    )
    path = tmp_path / "annotation.xlsx"
    assert write_sheet(storage, path, size=30) == 2  # no AI call needed
    with pytest.raises(FileExistsError):
        write_sheet(storage, path)

    workbook = load_workbook(path)
    sheet = workbook["À annoter"]
    assert workbook["_reponses"].sheet_state == "hidden"
    for row in (2, 3):  # the user's answers
        sheet.cell(row=row, column=6, value="CDI")
        sheet.cell(row=row, column=7, value=2)
        sheet.cell(row=row, column=8, value="hybride")
    workbook.save(path)

    # Only one AI answer available today (quota): the comparison uses that offer.
    extractor, _ = _extractor({"m1": GOOD, "m2": ApiError(429)}, models=("m1",))
    ids = sheet_offer_ids(path)
    first_only = ai_answers_for(storage, ids[:1], extractor)
    results = evaluate_sheet(path, first_only)
    assert results["contract"] == {"annotated": 2, "compared": 1, "rules": 1.0, "ai": 1.0}
    assert results["experience"]["ai"] == 1.0
    assert results["level"]["annotated"] == 0
    storage.close()


class QuotaError(Exception):
    code = 429

    def __str__(self) -> str:
        return (
            "429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', "
            "'quotaValue': '20'}, {'retryDelay': '3600s'}"
        )


def test_daily_quota_pauses_the_model_until_reset(config, tmp_path) -> None:
    storage = _stored(
        config,
        tmp_path,
        make_offer(source_offer_id="1", description=DESCRIPTION),
        make_offer(source_offer_id="2", title="Data Engineer", description=DESCRIPTION),
    )
    extractor, fake = _extractor({"m1": QuotaError(), "m2": GOOD})
    counts = enrich_new_offers(storage, config, extractor)
    assert counts["analysed"] == 2
    assert fake.calls == ["m1", "m2", "m2"]  # m1 is not asked again in the run

    extractor, fake = _extractor({"m1": GOOD, "m2": GOOD})
    storage.connection.execute("DELETE FROM ai_extractions")
    storage.connection.commit()
    enrich_new_offers(storage, config, extractor)
    assert "m1" not in fake.calls  # still out of quota at the next run
    storage.close()


def test_all_models_out_of_quota_skips_ai(config, tmp_path) -> None:
    storage = _stored(config, tmp_path, make_offer(source_offer_id="1", description=DESCRIPTION))
    extractor, _ = _extractor({"m1": QuotaError(), "m2": QuotaError()})
    first = enrich_new_offers(storage, config, extractor)
    assert first["unavailable"] == 1
    extractor, fake = _extractor({"m1": GOOD, "m2": GOOD})
    second = enrich_new_offers(storage, config, extractor)
    assert second["quota"] == 1 and fake.calls == []
    storage.close()


NO_DETAILS = "Vous construisez des tableaux de bord Power BI et des requêtes SQL pour les équipes métier. " * 4


def _enriched(config, tmp_path, answer: dict, **offer):
    storage = _stored(config, tmp_path, make_offer(description=NO_DETAILS, contract_type=None, **offer))
    extractor, _ = _extractor({"m1": answer})
    counts = enrich_new_offers(storage, config, extractor)
    return (
        storage,
        storage.get_offer(storage.pending_notifications()[0]["group_id"]) if storage.pending_notifications() else None,
        counts,
    )


def test_ai_fills_what_the_rules_did_not_find(config, tmp_path) -> None:
    storage, row, counts = _enriched(config, tmp_path, GOOD)
    assert counts["rejected"] == 0
    assert row["contract_type"] == "permanent" and row["min_experience_years"] == 2 and row["work_mode"] == "hybrid"
    flags = json.loads(row["flags"])
    assert "contrat non précisé" not in flags and "expérience non précisée" not in flags
    assert "contrat d'après l'IA : CDI" in flags and "expérience d'après l'IA : 2 an(s) minimum" in flags
    storage.close()


def test_ai_rejects_five_years_when_rules_found_nothing(config, tmp_path) -> None:
    storage, row, counts = _enriched(config, tmp_path, {**GOOD, "min_experience_years": 5})
    assert counts["rejected"] == 1 and row is None  # never notified
    offer = storage.connection.execute("SELECT * FROM offers").fetchone()
    assert not offer["kept"] and "d'après l'IA" in offer["reasons"]
    storage.close()


def test_ai_rejects_hidden_internship(config, tmp_path) -> None:
    storage, row, counts = _enriched(config, tmp_path, {**GOOD, "contract_type": "internship"})
    assert counts["rejected"] == 1 and row is None
    storage.close()


def test_rules_value_is_kept_when_ai_disagrees(config, tmp_path) -> None:
    storage = _stored(config, tmp_path, make_offer(description=DESCRIPTION))  # CDI, 2 years found by the rules
    row = storage.pending_notifications()[0]
    reason, updates, _, _ = fill_gaps(row, AIExtraction(contract_type="internship", min_experience_years=6), config)
    assert reason is None and "contract_type" not in updates and "min_experience_years" not in updates
    storage.close()
