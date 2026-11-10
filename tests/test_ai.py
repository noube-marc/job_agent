"""AI extraction with a fake Gemini client (no real call)."""

import json
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.ai.enrich import compare_with_rules, enrich_new_offers
from job_agent.ai.evaluation import evaluate_sheet, write_sheet
from job_agent.ai.extraction import AIExtraction, AIUnavailable, GeminiExtractor
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage

DESCRIPTION = ("Vous construisez des tableaux de bord Power BI et des requêtes SQL pour les équipes métier. "
               "Kafka serait un plus. Poste en CDI à Paris, 2 jours de télétravail. 2 ans d'expérience. ") * 3


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


GOOD = {"contract_type": "permanent", "min_experience_years": 2, "required_skills": ["SQL", "Power BI"],
        "optional_skills": ["Kafka"], "work_mode": "hybrid", "seniority": "confirmed"}


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
    storage = _stored(config, tmp_path, make_offer(source_offer_id="1", description=DESCRIPTION),
                      make_offer(source_offer_id="2", title="Data Engineer", description=DESCRIPTION))
    notified = storage.pending_notifications()[1]["group_id"]
    storage.mark_notified([notified], "telegram")
    extractor, fake = _extractor({"m1": GOOD})

    first = enrich_new_offers(storage, config, extractor)
    second = enrich_new_offers(storage, config, extractor)

    assert first["analysed"] == 1 and second["analysed"] == 0
    assert len(fake.calls) == 1  # never two calls for the same offer
    storage.close()


def test_ai_warnings(config, tmp_path) -> None:
    storage = _stored(config, tmp_path, make_offer(description="Stack Kafka. Expérience de 2 ans. " * 10, contract_type=None))
    row = storage.pending_notifications()[0]
    ai = AIExtraction(contract_type="internship", min_experience_years=6, required_skills=["SQL"],
                      seniority="lead_or_manager")
    flags = compare_with_rules(row, ai, config)
    assert "IA : stage selon la description (à vérifier)" in flags
    assert "IA : 6 ans d'expérience minimum (à vérifier)" in flags
    assert "IA : poste avec encadrement (à vérifier)" in flags
    assert "IA : technologies non maîtrisées seulement facultatives" in flags
    storage.close()


def test_annotation_sheet_and_evaluation(config, tmp_path) -> None:
    storage = _stored(config, tmp_path, make_offer(source_offer_id="1", description=DESCRIPTION),
                      make_offer(source_offer_id="2", title="Data Engineer", description=DESCRIPTION))
    extractor, _ = _extractor({"m1": GOOD})
    path = tmp_path / "annotation.xlsx"
    assert write_sheet(storage, extractor, path, size=30) == 2
    with pytest.raises(FileExistsError):
        write_sheet(storage, extractor, path)

    workbook = load_workbook(path)
    sheet = workbook["À annoter"]
    assert workbook["_reponses"].sheet_state == "hidden"
    for row in (2, 3):  # the user's answers
        sheet.cell(row=row, column=6, value="CDI")
        sheet.cell(row=row, column=7, value=2)
        sheet.cell(row=row, column=8, value="hybride")
    workbook.save(path)

    results = evaluate_sheet(path)
    assert results["contract"] == {"annotated": 2, "rules": 1.0, "ai": 1.0}
    assert results["experience"]["ai"] == 1.0
    assert results["level"]["annotated"] == 0
    storage.close()
