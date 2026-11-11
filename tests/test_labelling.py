"""Labelling sheet: offers are only ever added, answers are kept."""

from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.ai.labelling import ANSWER_COLUMN, COMMENT_COLUMN, SHEET_NAME, update_sheet
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

    added, total, labelled, prefilled = update_sheet(storage, path)
    assert 2 <= added < 2 + 40  # kept + borderline, and only a share of off-target offers
    assert labelled == prefilled == added - 2  # the "Comptable" offers are obvious "non"

    workbook = load_workbook(path)
    sheet = workbook[SHEET_NAME]
    kept_row = next(r for r in range(2, sheet.max_row + 1) if sheet.cell(row=r, column=4).value == "Data Analyst H/F")
    sheet.cell(row=kept_row, column=ANSWER_COLUMN, value="oui")
    first_row = [cell.value for cell in sheet[kept_row]]
    workbook.save(path)

    process_offers([make_offer(source_offer_id="3", title="Data Scientist")], config, storage, sectors, NOW)
    added_again, total_again, labelled_again, prefilled_again = update_sheet(storage, path)

    sheet = load_workbook(path)[SHEET_NAME]
    assert added_again == 1 and total_again == total + 1 and labelled_again == labelled + 1
    assert prefilled_again == 0
    assert [cell.value for cell in sheet[kept_row]] == first_row
    assert sheet.cell(row=sheet.max_row, column=4).value == "Data Scientist"
    assert update_sheet(storage, path)[0] == 0  # nothing new
    storage.close()


def _answers(path) -> dict[str, tuple]:
    sheet = load_workbook(path)[SHEET_NAME]
    return {
        sheet.cell(row=r, column=4).value: (sheet.cell(row=r, column=ANSWER_COLUMN).value,
                                            sheet.cell(row=r, column=COMMENT_COLUMN).value)
        for r in range(2, sheet.max_row + 1)
    }


def _offers_up_to_id_10(last_title: str) -> list:
    """Nine target offers, then the tested one: database id 10, so it is
    among the one in ten off-target offers written to the sheet."""
    fillers = [make_offer(source_offer_id=f"f{i}", title=f"Data Analyst {i}", company=f"Société {i}") for i in range(4, 10)]
    return [*fillers, make_offer(source_offer_id="10", title=last_title, company="Hôpital Exemple")]


def test_obvious_negatives_are_prefilled(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    process_offers([
        make_offer(source_offer_id="1", title="Data Analyst"),
        make_offer(source_offer_id="2", title="Data Analyst stage", contract_type="internship", company="B"),
        make_offer(source_offer_id="3", title="Data Engineer", description="5 ans d'expérience minimum en SQL.", company="C"),
        *_offers_up_to_id_10("Infirmier"),
    ], config, storage, sectors, NOW)
    path = tmp_path / "etiquetage.xlsx"
    update_sheet(storage, path)
    answers = _answers(path)
    assert answers["Data Analyst"] == (None, None)
    assert answers["Data Analyst stage"][0] == "non" and "CDI" in answers["Data Analyst stage"][1]
    assert answers["Data Engineer"][0] == "non" and "5 ans" in answers["Data Engineer"][1]
    assert answers["Infirmier"][0] == "non" and "non data" in answers["Infirmier"][1]
    storage.close()


def test_off_target_title_with_data_words_is_left_to_the_user() -> None:
    from job_agent.ai.labelling import obvious_no

    row = {"reasons": '["intitulé hors cible"]', "title": "Chargé d'analyse de données", "company": "X"}
    assert obvious_no(row) is None
    assert obvious_no({"reasons": '["intitulé hors cible"]', "title": "Infirmier", "company": "X"}) == "poste non data"


def test_user_answer_is_never_replaced(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    process_offers([make_offer(source_offer_id="1", title="Data Analyst", company="A"),
                    make_offer(source_offer_id="2", title="Data Analyst", company="B"),
                    make_offer(source_offer_id="3", title="Data Analyst", company="C"),
                    *_offers_up_to_id_10("Infirmier")], config, storage, sectors, NOW)
    path = tmp_path / "etiquetage.xlsx"
    update_sheet(storage, path)
    workbook = load_workbook(path)
    sheet = workbook[SHEET_NAME]
    row = next(r for r in range(2, sheet.max_row + 1) if sheet.cell(row=r, column=4).value == "Infirmier")
    assert sheet.cell(row=row, column=ANSWER_COLUMN).value == "non"
    sheet.cell(row=row, column=ANSWER_COLUMN, value="oui")
    sheet.cell(row=row, column=COMMENT_COLUMN, value="ma note")
    workbook.save(path)
    update_sheet(storage, path)
    assert _answers(path)["Infirmier"] == ("oui", "ma note")
    storage.close()


def test_consulting_firms_are_answered_no(config) -> None:
    from job_agent.ai.labelling import CONSULTING, obvious_no

    firms = config.labelling.not_relevant_companies
    kept = '[]'
    assert obvious_no({"reasons": kept, "title": "Data Analyst", "company": "PwC France et Maghreb"}, firms) == CONSULTING
    assert obvious_no({"reasons": kept, "title": "Data Analyst", "company": "EY Consulting"}, firms) == CONSULTING
    assert obvious_no({"reasons": '["employeur exclu (« Nexton »)"]', "title": "Data Analyst", "company": "Nexton"}) == CONSULTING
    assert obvious_no({"reasons": '["intitulé exclu (« consultant »)"]', "title": "Consultant Data", "company": "X"}) == CONSULTING
    for company in ("AXA France", "Conseil régional Exemple", "Lasecurecrute.fr"):
        assert obvious_no({"reasons": kept, "title": "Data Analyst", "company": company}, firms) is None
