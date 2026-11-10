"""Tests for the append-only Excel tracking file (CLAUDE.md, section 14, point 2)."""

from datetime import timedelta

import pytest
from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage
from job_agent.tracking import excel
from job_agent.tracking.excel import SHEET_REJECTED, SHEET_TRACKING, TrackingFileLocked, append_offers

STATUS_COLUMN = len(excel.AGENT_COLUMNS) + 1
NOTES_COLUMN = len(excel.AGENT_COLUMNS) + len(excel.USER_COLUMNS)


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "test.db")
    yield db
    db.close()


def _write_pending(storage: Storage, path) -> excel.WriteResult:
    result = append_offers(path, storage.pending_tracking(), storage.group_members, keep_rejected=True)
    storage.mark_tracked(result.written_ids)
    return result


def test_append_only_keeps_user_columns(config, storage, tmp_path) -> None:
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    path = tmp_path / "suivi.xlsx"

    process_offers([make_offer(source_offer_id="1")], config, storage, sectors, NOW)
    assert _write_pending(storage, path).kept == 1

    # The user fills in their own columns, then the agent runs again.
    workbook = load_workbook(path)
    sheet = workbook[SHEET_TRACKING]
    sheet.cell(row=2, column=STATUS_COLUMN, value="postulé")
    sheet.cell(row=2, column=NOTES_COLUMN, value="Appel RH prévu")
    original_row = [cell.value for cell in sheet[2]]
    workbook.save(path)

    later = make_offer(source_offer_id="2", title="Data Engineer", published_at=NOW - timedelta(hours=2))
    stage = make_offer(source_offer_id="3", title="Stage Data Analyst", contract_type="internship")
    process_offers([later, stage], config, storage, sectors, NOW)
    result = _write_pending(storage, path)

    sheet = load_workbook(path)[SHEET_TRACKING]
    assert [cell.value for cell in sheet[2]] == original_row
    assert sheet.cell(row=3, column=3).value == "Data Engineer"
    assert sheet.max_row == 3
    assert result.rejected == 1
    assert load_workbook(path)[SHEET_REJECTED].cell(row=2, column=3).value == "Stage Data Analyst"

    # Nothing pending any more: a new run writes nothing.
    assert _write_pending(storage, path).written_ids == []


def test_locked_file_keeps_offers_pending(config, storage, tmp_path, monkeypatch) -> None:
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")
    path = tmp_path / "suivi.xlsx"
    process_offers([make_offer()], config, storage, sectors, NOW)

    excel._create_workbook(path)

    def locked(*args, **kwargs):
        raise PermissionError("file open in Excel")

    monkeypatch.setattr(excel.Workbook, "save", locked)
    with pytest.raises(TrackingFileLocked):
        _write_pending(storage, path)
    assert len(storage.pending_tracking()) == 1
