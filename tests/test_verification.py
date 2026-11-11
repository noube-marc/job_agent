"""Career site verification of offers seen on job sites (no network)."""

import json
from datetime import timedelta

import pytest
from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.processing.verification import (
    DISAPPEARED, MISSING, PENDING, UNVERIFIABLE, VERIFIED, Listing, check_pending, recheck_tracked,
)
from job_agent.storage import Storage
from job_agent.tracking.excel import SHEET_TRACKING, SHEET_VERIFICATIONS, append_offers

LATER = NOW + timedelta(hours=25)


@pytest.fixture
def vconfig(config):
    settings = config.source_verification.model_copy(update={"enabled": True})
    return config.model_copy(update={"source_verification": settings})


def _alert(**overrides):
    values = dict(source="email_alert", source_name="email:linkedin", source_offer_id="a1",
                  url="https://www.linkedin.com/jobs/view/1/", company="Alan", title="Data Analyst H/F",
                  location="Paris", description="", contract_type=None, published_at=None,
                  published_at_source=None)
    values.update(overrides)
    return make_offer(**values)


def _career(**overrides):
    values = dict(source="career_page", source_name="ashby:Alan", source_offer_id="c1", company="Alan",
                  url="https://jobs.ashbyhq.com/alan/1", title="Data Analyst H/F", location="Paris",
                  description="")
    values.update(overrides)
    return make_offer(**values)


def _process(config, storage, offers, now=NOW):
    return process_offers(offers, config, storage, SectorIndex.build(config, ROOT / "sectors.yaml"), now)


def _main(storage):
    return storage.connection.execute("SELECT * FROM offers WHERE id = group_id ORDER BY id").fetchone()


def _alan_ok():
    return {"Alan": Listing("ashby", ok=True, covered_since=NOW - timedelta(days=60))}


def test_initial_status(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [
        _alert(),  # Alan: watched, with a connector
        _alert(source_offer_id="a2", company="AXA France", title="Data Engineer"),  # watched, no connector
        _alert(source_offer_id="a3", company="Société Inconnue", title="Data Scientist"),  # not watched
    ])
    statuses = [row["verification"] for row in storage.connection.execute("SELECT * FROM offers ORDER BY id")]
    assert statuses == [PENDING, UNVERIFIABLE, UNVERIFIABLE]
    storage.close()


def test_found_on_career_site_is_verified(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert()])
    _process(vconfig, storage, [_career()])
    main = _main(storage)
    assert main["verification"] == VERIFIED
    assert len(storage.group_members(main["id"])) == 2  # one offer, both links kept
    [event] = storage.unwritten_verification_events()
    assert (event["old_status"], event["new_status"]) == (PENDING, VERIFIED)
    storage.close()


def test_still_pending_during_grace_period(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert()])
    check_pending(storage, vconfig, _alan_ok(), NOW + timedelta(hours=10))
    assert _main(storage)["verification"] == PENDING and _main(storage)["kept"]
    storage.close()


def test_missing_after_grace_period_is_rejected_and_reported(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert()])
    storage.mark_notified([_main(storage)["id"]], "telegram")
    counts = check_pending(storage, vconfig, _alan_ok(), LATER)
    main = _main(storage)
    assert counts[MISSING] == 1 and main["verification"] == MISSING and not main["kept"]
    assert "absente du site carrière" in main["reasons"]
    [message] = storage.pending_outbox()
    assert "introuvable sur le site carrière" in message["message"]
    storage.close()


def test_career_site_error_means_unverifiable_not_missing(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert()])
    check_pending(storage, vconfig, {"Alan": Listing("ashby", ok=False)}, LATER)
    main = _main(storage)
    assert main["verification"] == UNVERIFIABLE and main["kept"]
    storage.close()


def test_short_feed_cannot_conclude(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert(company="MAIF")])
    short_feed = {"MAIF": Listing("talentsoft_rss", ok=True, covered_since=NOW - timedelta(days=2))}
    check_pending(storage, vconfig, short_feed, LATER)
    assert _main(storage)["verification"] == UNVERIFIABLE and _main(storage)["kept"]

    _process(vconfig, storage, [_alert(company="MAIF", source_offer_id="a2", title="Data Engineer")])
    long_feed = {"MAIF": Listing("talentsoft_rss", ok=True, covered_since=NOW - timedelta(days=10))}
    check_pending(storage, vconfig, long_feed, LATER)
    rows = storage.connection.execute("SELECT verification FROM offers ORDER BY id").fetchall()
    assert rows[1]["verification"] == MISSING
    storage.close()


def test_close_title_on_career_site_is_a_probable_match(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert()])
    # Same job, but another place and contract: not merged by the deduplication.
    _process(vconfig, storage, [_career(title="Data Analyst", location="Lyon", contract_type="fixed_term")])
    check_pending(storage, vconfig, _alan_ok(), LATER)
    main = storage.get_offer(1)
    assert main["verification"] == VERIFIED and main["kept"]
    detail = storage.unwritten_verification_events()[-1]["detail"]
    assert detail.startswith("correspondance probable")
    storage.close()


def test_recheck_marks_disappeared_offers(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [
        _career(source_offer_id="c1", published_at=NOW - timedelta(days=2)),
        _career(source_offer_id="c2", title="Data Engineer", published_at=NOW - timedelta(days=1)),
        _career(source_offer_id="c3", title="Data Scientist", published_at=NOW - timedelta(days=40)),
    ])
    storage.mark_tracked([1, 2, 3])
    storage.mark_notified([1, 2, 3], "telegram")
    # This run, the site lists c2 only, back to 10 days ago: c1 is gone, c3 is too old to judge.
    listing = {"Alan": Listing("ashby", ok=True, covered_since=NOW - timedelta(days=10), seen_ids=frozenset({"c2"}))}
    gone = recheck_tracked(storage, vconfig, listing, NOW, NOW)
    assert [row["id"] for row in gone] == [1]
    assert storage.get_offer(1)["verification"] == DISAPPEARED
    assert storage.get_offer(3)["verification"] is None
    assert "ne sont plus en ligne" in storage.pending_outbox()[0]["message"]
    # Once per interval only.
    assert recheck_tracked(storage, vconfig, listing, NOW, NOW + timedelta(hours=1)) == []
    storage.close()


def test_status_column_and_verification_sheet(vconfig, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _process(vconfig, storage, [_alert()])
    path = tmp_path / "suivi.xlsx"
    append_offers(path, storage.pending_tracking(), storage.group_members, keep_rejected=True)
    storage.mark_tracked([1])
    _process(vconfig, storage, [_career()])
    events = storage.unwritten_verification_events()
    result = append_offers(path, storage.pending_tracking(), storage.group_members, keep_rejected=True, events=events)

    workbook = load_workbook(path)
    assert workbook[SHEET_TRACKING].cell(row=2, column=15).value == "vérification en attente"  # never rewritten
    log = workbook[SHEET_VERIFICATIONS]
    assert [cell.value for cell in log[2]][4:6] == ["vérification en attente", "vérifiée à la source"]
    assert result.events_written == [events[0]["id"]]
    storage.close()


def test_pending_offers_wait_when_configured(vconfig, tmp_path) -> None:
    from job_agent.notify.telegram import notify_pending

    settings = vconfig.source_verification.model_copy(update={"notify_while_pending": False})
    waiting = vconfig.model_copy(update={"source_verification": settings})
    storage = Storage(tmp_path / "t.db")
    _process(waiting, storage, [_alert(), _career(source_offer_id="c9", title="Data Engineer")])
    sent: list[str] = []
    notify_pending(storage, waiting, sent.append, silent=False)
    assert len(sent) == 1 and "Data Engineer" in sent[0]
    storage.close()


def test_disabled_verification_changes_nothing(config, tmp_path) -> None:
    settings = config.source_verification.model_copy(update={"enabled": False})
    config = config.model_copy(update={"source_verification": settings})
    storage = Storage(tmp_path / "t.db")
    _process(config, storage, [_alert()])
    assert _main(storage)["verification"] is None
    flags = json.loads(_main(storage)["flags"])
    assert "date estimée" in flags
    storage.close()
