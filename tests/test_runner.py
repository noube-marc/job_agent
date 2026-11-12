"""End-to-end run with fake connectors (no network)."""

import shutil

from openpyxl import load_workbook

from conftest import ROOT, make_offer
from job_agent import runner
from job_agent.collectors.base import SourceError
from job_agent.storage import Storage, utc_now


def test_full_run_is_silent_first_then_notifies(config, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    shutil.copyfile(ROOT / "sectors.yaml", tmp_path / "sectors.yaml")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    sent: list[str] = []
    monkeypatch.setattr(runner, "telegram_sender_from_env", lambda: sent.append)
    monkeypatch.setattr(runner, "internet_available", lambda: True)

    batches = {"first": [make_offer(source_offer_id="1", published_at=utc_now())]}

    def good(company, context):
        return batches["first"]

    def broken(company, context):
        raise SourceError("site injoignable (exemple.fr, ConnectError)")

    # Only fake connectors: the real ones would call real sites.
    monkeypatch.setattr(runner, "CONNECTORS", {"fake": good, "broken": broken})
    watchlist = config.companies.watchlist
    watchlist[0] = watchlist[0].model_copy(update={"ats": "broken", "careers_url": "https://exemple.fr"})
    watchlist[1] = watchlist[1].model_copy(update={"ats": "fake", "careers_url": "https://exemple.org"})

    assert runner.run(config, force=True) == 0
    assert sent == []  # first run is silent

    batches["first"] = [make_offer(source_offer_id="2", title="Data Engineer", published_at=utc_now())]
    runner.run(config, force=True)
    assert len(sent) == 1 and "Data Engineer" in sent[0]

    sheet = load_workbook(tmp_path / "private" / "job_tracking.xlsx")["Suivi"]
    assert sheet.max_row == 3  # header + two offers
    storage = Storage(tmp_path / "private" / "job_agent.db")
    errors = storage.connection.execute("SELECT COUNT(*) FROM source_runs WHERE error IS NOT NULL").fetchone()[0]
    storage.close()
    assert errors == 2  # the broken source failed twice, without stopping the run
    log_text = (tmp_path / "private" / "logs" / "job_agent.log").read_text(encoding="utf-8")
    assert "test-token" not in log_text


def test_no_internet_postpones_the_run(config, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "internet_available", lambda: False)
    called = []
    monkeypatch.setattr(runner, "_collect_and_process", lambda *args: called.append(args))

    assert runner.run(config, force=True) == 0
    assert called == []
    assert not (tmp_path / "private" / "job_agent.db").exists()  # nothing recorded


def test_silent_career_site_is_reported_once_then_back(tmp_path) -> None:
    from job_agent.runner import SILENT_SOURCE_RUNS, _watch_silent_sources
    from job_agent.storage import Storage

    storage = Storage(tmp_path / "t.db")
    for _ in range(SILENT_SOURCE_RUNS + 2):
        run_id = storage.start_run(False)
        storage.record_source(run_id, "workday:Exemple", 0, "réponse HTTP 500 (exemple.com)")
        storage.record_source(run_id, "email:linkedin", 0, None)  # no new e-mail is normal
        _watch_silent_sources(storage, run_id)
    messages = [row["message"] for row in storage.pending_outbox()]
    assert len(messages) == 1 and "Exemple" in messages[0] and "HTTP 500" in messages[0]

    run_id = storage.start_run(False)
    storage.record_source(run_id, "workday:Exemple", 12, None)
    _watch_silent_sources(storage, run_id)
    messages = [row["message"] for row in storage.pending_outbox()]
    assert len(messages) == 2 and "de nouveau" in messages[1]
    storage.close()
