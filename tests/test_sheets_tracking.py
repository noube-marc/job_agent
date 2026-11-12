"""Google Sheets tracking, with a fake Sheets service (no real Google call)."""

from openpyxl import Workbook

from conftest import NOW, ROOT, make_offer
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage
from job_agent.tracking import sheets
from job_agent.tracking.excel import SHEET_DESCRIPTIONS, SHEET_REJECTED, SHEET_TRACKING, STATUS_CHOICES


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeSheets:
    """Mimics service.spreadsheets() and .values(); keeps every appended row."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.requests: list[dict] = []
        self.rows: dict[str, list[list]] = {}

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def create(self, body):
        self.created.append(body)
        sheets_ = [{"properties": {"title": s["properties"]["title"], "sheetId": i}} for i, s in enumerate(body["sheets"])]
        return Call({"spreadsheetId": "sheet-123", "sheets": sheets_})

    def batchUpdate(self, spreadsheetId, body):  # noqa: N802 (Google API name)
        self.requests += body["requests"]
        return Call({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):  # noqa: A002
        assert insertDataOption == "INSERT_ROWS"  # rows are only ever added
        name = range.split("'")[1]
        self.rows.setdefault(name, []).extend(body["values"])
        return Call({})


def _storage(config, tmp_path) -> Storage:
    storage = Storage(tmp_path / "t.db")
    process_offers(
        [make_offer(source_offer_id="1", title="Data Analyst H/F", description="Texte. " * 10000),
         make_offer(source_offer_id="2", title="Comptable H/F")],
        config, storage, SectorIndex.build(config, ROOT / "sectors.yaml"), NOW,
    )
    return storage


def test_spreadsheet_is_created_once_in_french(config, tmp_path) -> None:
    storage = _storage(config, tmp_path)
    fake = FakeSheets()
    first = sheets.spreadsheet_id(fake, storage, "")
    second = sheets.spreadsheet_id(fake, storage, "")
    assert first == second == "sheet-123" and len(fake.created) == 1  # remembered in the database
    assert fake.created[0]["properties"]["locale"] == "fr_FR"
    validation = [r for r in fake.requests if "setDataValidation" in r][0]["setDataValidation"]
    assert [v["userEnteredValue"] for v in validation["rule"]["condition"]["values"]] == STATUS_CHOICES
    assert fake.rows[SHEET_TRACKING][0][:2] == ["Identifiant", "Entreprise"]  # header row
    assert sheets.spreadsheet_id(fake, storage, "configured-id") == "configured-id"
    storage.close()


def test_offers_are_appended_without_touching_user_columns(config, tmp_path) -> None:
    storage = _storage(config, tmp_path)
    fake = FakeSheets()
    result = sheets.append_offers(fake, "sheet-123", storage.pending_tracking(), storage.group_members, True)
    assert result.kept == 1 and result.rejected == 1
    [row] = fake.rows[SHEET_TRACKING]
    assert row[2] == "Data Analyst H/F" and len(row) == len(sheets.AGENT_COLUMNS)  # user columns left empty
    assert all(isinstance(value, (str, int, float)) for value in row)  # dates written as text
    [description] = fake.rows[SHEET_DESCRIPTIONS]
    assert len(description[3]) < 50000 and description[3].endswith("[texte coupé : voir la base de l'agent]")
    assert fake.rows[SHEET_REJECTED][0][2] == "Comptable H/F"
    storage.close()


def test_excel_file_is_copied_with_the_user_columns(tmp_path) -> None:
    path = tmp_path / "suivi.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TRACKING
    sheet.append(["Identifiant", "Entreprise", "Poste"])
    sheet.append([12, "AXA", "Data Analyst", *[None] * 15, "postulé", None, None, None, None, "relancer lundi"])
    workbook.save(path)
    fake = FakeSheets()
    copied = sheets.import_excel(fake, "sheet-123", path)
    assert copied == {SHEET_TRACKING: 1}
    assert "postulé" in fake.rows[SHEET_TRACKING][0] and "relancer lundi" in fake.rows[SHEET_TRACKING][0]
