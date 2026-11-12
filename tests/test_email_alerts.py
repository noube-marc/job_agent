"""E-mail alert readers and their integration in a run (no real Gmail)."""

from datetime import UTC, datetime
from pathlib import Path

from conftest import NOW, ROOT
from job_agent import runner
from job_agent.gmail.parsers import parse_linkedin
from job_agent.gmail.reader import AlertEmail
from job_agent.processing.filters import evaluate
from job_agent.processing.pipeline import complete_offer
from job_agent.storage import Storage

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_alert.txt"
RECEIVED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _alert(message_id: str = "m1") -> AlertEmail:
    return AlertEmail(
        message_id,
        "linkedin",
        "jobalerts-noreply@linkedin.com",
        "Alerte",
        RECEIVED,
        "",
        FIXTURE.read_text(encoding="utf-8"),
    )


def test_linkedin_alert() -> None:
    offers = parse_linkedin(_alert())
    assert [(o.source_offer_id, o.title, o.company, o.location) for o in offers] == [
        ("1000000001", "Data Analyst H/F", "Banque Exemple", "Paris, Île-de-France, France"),
        (
            "1000000002",
            "CDD - Business Analyst - Performance (F/H/X)",
            "Média Exemple",
            "Issy-les-Moulineaux, Île-de-France, France",
        ),
        ("1000000003", "Data Engineer (H/F)", "Conseil Exemple", "Toulouse et périphérie"),
    ]
    first = offers[0]
    assert first.url == "https://www.linkedin.com/jobs/view/1000000001/"  # tracking removed, never opened
    assert first.description_is_partial and first.source == "email_alert"
    assert first.published_at is None and first.first_seen_at == RECEIVED  # no real date in the e-mail
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
    sources = config.sources.model_copy(
        update={
            "email_alerts": alerts,
            "career_pages": config.sources.career_pages.model_copy(update={"enabled": False}),
        }
    )
    return config.model_copy(update={"sources": sources})


def test_run_reads_each_email_once(config, tmp_path, monkeypatch) -> None:
    import job_agent.gmail.reader as reader_module

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sectors.yaml").write_text((ROOT / "sectors.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(reader_module, "GmailReader", FakeReader)
    monkeypatch.setattr(reader_module, "build_service", lambda: object())
    monkeypatch.setattr(runner, "telegram_sender_from_env", lambda: lambda text: None)
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


def test_alert_offers_are_flagged_estimated_date(config) -> None:
    result = evaluate(complete_offer(parse_linkedin(_alert())[0]), config, NOW)
    assert result.kept and "date estimée" in result.flags


def test_more_offers_link_keeps_only_search_parameters() -> None:
    from job_agent.gmail.parsers import linkedin_more_offers_link

    link = linkedin_more_offers_link(_alert())
    assert link == "https://www.linkedin.com/jobs/search/?keywords=Data&geoId=105015875&f_TPR=a1793710661-"
    assert "SECRET" not in link and "trk" not in link


def _store(config, storage, offers, now=NOW):
    from job_agent.processing.pipeline import process_offers
    from job_agent.processing.sectors import SectorIndex

    return process_offers(offers, config, storage, SectorIndex.build(config, ROOT / "sectors.yaml"), now)


def _career_copy(days_ago: int):
    from datetime import timedelta

    from conftest import make_offer

    return make_offer(
        source_offer_id="cs-1",
        title="Data Analyst H/F",
        company="Banque Exemple",
        location="Paris",
        published_at=NOW - timedelta(days=days_ago),
        contract_type=None,
    )


def test_career_site_date_replaces_alert_date(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _store(config, storage, [parse_linkedin(_alert())[0]])
    counts = _store(config, storage, [_career_copy(2)])
    main = storage.pending_notifications()[0]
    assert counts["date_corrections"] == 1
    assert "date du site carrière" in main["flags"] and "date estimée" not in main["flags"]
    storage.close()


def test_old_offer_found_on_career_site_is_rejected_before_notification(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _store(config, storage, [parse_linkedin(_alert())[0]])
    _store(config, storage, [_career_copy(12)])
    assert all(row["title"] != "Data Analyst H/F" for row in storage.pending_notifications())
    assert storage.pending_outbox() == []
    storage.close()


def test_old_offer_already_notified_triggers_a_warning(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    _store(config, storage, [parse_linkedin(_alert())[0]])
    storage.mark_notified([row["group_id"] for row in storage.pending_notifications()], "telegram")
    _store(config, storage, [_career_copy(12)])
    warnings = storage.pending_outbox()
    assert len(warnings) == 1 and "12 jours" in warnings[0]["message"]
    storage.close()


def test_run_sends_more_offers_link_once(config, tmp_path, monkeypatch) -> None:
    import job_agent.gmail.reader as reader_module

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sectors.yaml").write_text((ROOT / "sectors.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    sent: list[str] = []
    monkeypatch.setattr(reader_module, "GmailReader", FakeReader)
    monkeypatch.setattr(reader_module, "build_service", lambda: object())
    monkeypatch.setattr(runner, "telegram_sender_from_env", lambda: sent.append)
    monkeypatch.setattr(runner, "internet_available", lambda: True)
    config = _enable_alerts(config)

    FakeReader.alerts = []
    runner.run(config, force=True)  # first run: silent
    FakeReader.alerts = [_alert("m2")]
    runner.run(config, force=True)
    runner.run(config, force=True)

    links = [m for m in sent if "Voir toutes les offres" in m]
    assert len(links) == 1 and "SECRET" not in links[0]
