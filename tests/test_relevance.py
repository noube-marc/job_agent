"""Relevance model study, on synthetic offers (no real label, no model download)."""

from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.ai.labelling import ANSWER_COLUMN, SHEET_NAME, update_sheet
from job_agent.ai.relevance import build_dataset, read_labels, run_study, write_report
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage
import pytest


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    import job_agent.ai.relevance as relevance

    monkeypatch.setattr(relevance, "REPEATS", 2)


class FakeMatcher:
    """Offers about "SQL" are close to the CV."""

    def parts(self, offers):
        return [(0.9 if "SQL" in description else 0.2, 2 if "SQL" in description else 0) for _, description in offers]


def _labelled(config, tmp_path):
    storage = Storage(tmp_path / "t.db")
    offers = []
    for i in range(30):
        relevant = i % 2 == 0
        offers.append(make_offer(
            source_offer_id=str(i), company=f"Société {i}",
            title="Data Analyst junior" if relevant else "Data Engineer",
            description=("Requêtes SQL et Power BI. " if relevant else "Plateforme Kafka et Spark. ") * 30,
        ))
    process_offers(offers, config, storage, SectorIndex.build(config, ROOT / "sectors.yaml"), NOW)
    path = tmp_path / "etiquetage.xlsx"
    update_sheet(storage, path)
    workbook = load_workbook(path)
    sheet = workbook[SHEET_NAME]
    for row in range(2, sheet.max_row + 1):
        relevant = "junior" in sheet.cell(row=row, column=4).value
        sheet.cell(row=row, column=ANSWER_COLUMN, value="oui" if relevant else "non")
    workbook.save(path)
    return storage, path


def test_study_learns_an_obvious_preference(config, tmp_path) -> None:
    storage, path = _labelled(config, tmp_path)
    labels = read_labels(path)
    assert len(labels) == 30 and sum(labels.values()) == 15
    dataset = build_dataset(storage, labels, FakeMatcher())
    study = run_study(dataset)
    assert study["kept"]["Régression logistique"]["auc"] > 0.9
    assert study["kept"]["Ordre au hasard"]["auc"] < 0.8
    storage.close()


def test_report_holds_numbers_only(config, tmp_path) -> None:
    storage, path = _labelled(config, tmp_path)
    study = run_study(build_dataset(storage, read_labels(path), FakeMatcher()))
    report = tmp_path / "relevance_model.md"
    write_report(study, report)
    text = report.read_text(encoding="utf-8")
    assert "ROC AUC" in text and "Poste junior" in text
    assert "Société" not in text and "Power BI" not in text and "http" not in text
    storage.close()
