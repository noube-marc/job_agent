"""E-mail alert readers and their integration in a run (no real Gmail)."""

from datetime import datetime, timezone
from pathlib import Path

from conftest import NOW, ROOT
from job_agent import runner
from job_agent.gmail.parsers import parse_linkedin
from job_agent.gmail.reader import AlertEmail
from job_agent.processing.filters import evaluate
from job_agent.processing.pipeline import complete_offer
from job_agent.storage import Storage

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_alert.txt"
RECEIVED = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


def _alert(message_id: str = "m1") -> AlertEmail:
    return AlertEmail(message_id, "linkedin", "jobalerts-noreply@linkedin.com", "Alerte",
                      RECEIVED, "", FIXTURE.read_text(encoding="utf-8"))


def test_linkedin_alert() -> None:
    offers = parse_linkedin(_alert())
    assert [(o.source_offer_id, o.title, o.company, o.location) for o in offers] == [
        ("1000000001", "Data Analyst H/F", "Banque Exemple", "Paris, Île-de-France, France"),
        ("1000000002", "CDD - Business Analyst - Performance (F/H/X)", "Média Exemple",
         "Issy-les-Moulineaux, Île-de-France, France"),
        ("1000000003", "Data Engineer (H/F)", "Conseil Exemple", "Toulouse et périphérie"),
    ]
    first = offers[0]
    assert first.url == "https://www.linkedin.com/jobs/view/1000000001/"  # tracking removed, never opened
    assert first.description_is_partial and first.source == "email_alert"
    assert first.published_at == RECEIVED and first.published_at_source == "job_board"
    assert offers[1].contract_type_raw.startswith("CDD")


def test_business_analyst_from_alert_is_kept_for_checking(config) -> None:
    analyst = complete_offer(parse_linkedin(_alert())[1])
    result = evaluate(analyst, config, NOW)
    assert result.kept
    assert "Business Analyst : compétences techniques à vérifier" in result.flags


class FakeReader:
    alerts: list[AlertEmail] = []
    skipped: set[str] = set()

    def __init__(self, service, settings) -> None:
        pass

    def read_alerts(self, skip=frozenset()):
        FakeReader.skipped = set(skip)
        return [a for a in FakeReader.alerts if a.message_id not in skip]


def _enable_alerts(config):
    alerts = config.sources.email_alerts.model_copy(update={"enabled": True})
    sources = config.sources.model_copy(update={"email_alerts": alerts, "career_pages": config.sources.career_pages.model_copy(update={"enabled": False})})
    return config.model_copy(update={"sources": sources})


def test_run_reads_each_email_once(config, tmp_path, monkeypatch) -> None:
    import job_agent.gmail.reader as reader_module

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sectors.yaml").write_text((ROOT / "sectors.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(reader_module, "GmailReader", FakeReader)
    monkeypatch.setattr(reader_module, "build_service", lambda: object())
    monkeypatch.setattr(runner, "telegram_sender_from_env", lambda: (lambda text: None))
    monkeypatch.setattr(runner, "internet_available", lambda: True, raising=False)
    FakeReader.alerts = [_alert("m1")]
    config = _enable_alerts(config)

    runner.run(config, force=True)
    runner.run(config, force=True)

    storage = Storage(tmp_path / "private" / "job_agent.db")
    assert storage.processed_emails() == {"m1"}
    assert FakeReader.skipped == {"m1"}  # second run asked Gmail to skip it
    count = storage.connection.execute("SELECT COUNT(*) FROM offers WHERE source = 'email_alert'").fetchone()[0]
    storage.close()
    assert count == 3


def test_expired_gmail_access_warns_once_a_day(config, tmp_path, monkeypatch) -> None:
    import job_agent.gmail.reader as reader_module
    from job_agent.gmail.auth import GmailAuthError

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sectors.yaml").write_text((ROOT / "sectors.yaml").read_text(encoding="utf-8"), encoding="utf-8")

    def expired():
        raise GmailAuthError("autorisation Gmail refusée ou expirée (RefreshError)")

    sent: list[str] = []
    monkeypatch.setattr(reader_module, "build_service", expired)
    monkeypatch.setattr(runner, "telegram_sender_from_env", lambda: sent.append)
    monkeypatch.setattr(runner, "internet_available", lambda: True, raising=False)
    config = _enable_alerts(config)

    runner.run(config, force=True)
    runner.run(config, force=True)
    assert len(sent) == 1 and "gmail-setup" in sent[0]
