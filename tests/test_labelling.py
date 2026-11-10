"""Labelling sheet: offers are only ever added, answers are kept."""

from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.ai.labelling import ANSWER_COLUMN, SHEET_NAME, update_sheet
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage


def test_sheet_is_extended_without_touching_answers(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    off_target = [make_offer(source_offer_id=f"c{i}", title="Comptable", company=f"Société {i}") for i in range(40)]
    process_offers([make_offer(source_offer_id="1"),
                    make_offer(source_offer_id="2", title="Lead Data Engineer"), *off_target],
                   config, storage, sectors, NOW)
    path = tmp_path / "etiquetage.xlsx"

    added, total, labelled = update_sheet(storage, path)
    assert 2 <= added < 2 + 40  # kept + borderline, and only a share of off-target offers
    assert labelled == 0

    workbook = load_workbook(path)
    sheet = workbook[SHEET_NAME]
    sheet.cell(row=2, column=ANSWER_COLUMN, value="oui")
    first_row = [cell.value for cell in sheet[2]]
    workbook.save(path)

    process_offers([make_offer(source_offer_id="3", title="Data Scientist")], config, storage, sectors, NOW)
    added_again, total_again, labelled_again = update_sheet(storage, path)

    sheet = load_workbook(path)[SHEET_NAME]
    assert added_again == 1 and total_again == total + 1 and labelled_again == 1
    assert [cell.value for cell in sheet[2]] == first_row
    assert sheet.cell(row=sheet.max_row, column=4).value == "Data Scientist"
    assert update_sheet(storage, path)[0] == 0  # nothing new
    storage.close()
