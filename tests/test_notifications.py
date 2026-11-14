"""Notification safeguards (CLAUDE.md, section 14, points 1 and 6)."""

import pytest

from conftest import NOW, ROOT, make_offer
from job_agent.notify.telegram import NotificationError, notify_pending
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage


class FakeSender:
    def __init__(self, fail_after: int | None = None) -> None:
        self.messages: list[str] = []
        self.fail_after = fail_after

    def __call__(self, text: str) -> None:
        if self.fail_after is not None and len(self.messages) >= self.fail_after:
            raise NotificationError("Telegram injoignable (ConnectError)")
        self.messages.append(text)


@pytest.fixture
def env(config, tmp_path):
    storage = Storage(tmp_path / "test.db")
    sectors = SectorIndex.build(config, ROOT / "sectors.yaml")

    def collect(*offers):
        process_offers(list(offers), config, storage, sectors, NOW)

    yield config, storage, collect
    storage.close()


def _offers(count: int) -> list:
    titles = ["Data Analyst", "Data Engineer", "Analytics Engineer", "BI Analyst", "Statisticien"]
    companies = ["AXA", "MAIF", "Danone", "Carrefour", "Qonto", "Alan", "Bel"]
    return [
        make_offer(source_offer_id=str(i), title=titles[i % 5], company=f"{companies[i % 7]} {i}") for i in range(count)
    ]


def test_same_offer_is_not_notified_twice_across_runs(env) -> None:
    config, storage, collect = env
    sender = FakeSender()
    collect(make_offer())
    notify_pending(storage, config, sender, silent=False)
    collect(make_offer())  # second run, same offer
    notify_pending(storage, config, sender, silent=False)
    assert len(sender.messages) == 1


def test_same_offer_from_two_sources_is_notified_once(env) -> None:
    config, storage, collect = env
    sender = FakeSender()
    collect(make_offer(title="Data Analyst H/F", location="Paris 8e"))
    notify_pending(storage, config, sender, silent=False)
    collect(
        make_offer(
            source="email_alert",
            source_name="email:apec",
            source_offer_id="X",
            title="Data Analyst (F/H)",
            location="Paris",
        )
    )
    notify_pending(storage, config, sender, silent=False)
    assert len(sender.messages) == 1


def test_first_run_is_silent(env) -> None:
    config, storage, collect = env
    sender = FakeSender()
    collect(*_offers(3))
    result = notify_pending(storage, config, sender, silent=True)
    assert sender.messages == [] and result.silenced == 3
    assert storage.pending_notifications() == []


def test_cap_sends_a_single_summary(env) -> None:
    config, storage, collect = env
    sender = FakeSender()
    collect(*_offers(config.notifications.max_per_run + 1))
    result = notify_pending(storage, config, sender, silent=False)
    assert result.summary and len(sender.messages) == 1
    assert f"{config.notifications.max_per_run + 1} nouvelles offres" in sender.messages[0]


def test_failed_send_keeps_offers_pending(env) -> None:
    config, storage, collect = env
    collect(*_offers(3))
    result = notify_pending(storage, config, FakeSender(fail_after=1), silent=False)
    assert result.sent == 1 and result.error
    assert len(storage.pending_notifications()) == 2
    retry = FakeSender()
    notify_pending(storage, config, retry, silent=False)
    assert len(retry.messages) == 2


def test_message_content(env) -> None:
    config, storage, collect = env
    sender = FakeSender()
    collect(make_offer(title="Data Analyst <Power BI>", description="SQL et Power BI."))
    notify_pending(storage, config, sender, silent=False)
    message = sender.messages[0]
    assert "<b>Data Analyst &lt;Power BI&gt;</b>" in message
    assert "secteur prioritaire" in message and "expérience non précisée" in message
    assert 'href="https://example.org/offres/1"' in message


def test_message_tells_where_the_date_comes_from(config, tmp_path) -> None:
    from datetime import timedelta

    from job_agent.notify.telegram import format_offer

    storage = Storage(tmp_path / "t.db")
    process_offers(
        [
            make_offer(source_offer_id="c1", title="Data Scientist (H/F)", company="Isagri"),
            make_offer(
                source="email_alert",
                source_name="email:linkedin",
                source_offer_id="a1",
                title="Data Analyst",
                company="Société Inconnue",
                published_at=None,
                published_at_source=None,
            ),
            make_offer(
                source="email_alert",
                source_name="email:indeed",
                source_offer_id="i1",
                title="Data Engineer",
                company="Autre Société",
                published_at=NOW - timedelta(days=1),
                published_at_source="job_board",
            ),
        ],
        config,
        storage,
        SectorIndex.build(config, ROOT / "sectors.yaml"),
        NOW,
    )
    messages = {
        row["title"]: format_offer(row, storage.group_members(row["group_id"]), config)
        for row in storage.pending_notifications()
    }
    assert "(date du site carrière)" in messages["Data Scientist (H/F)"]
    assert "lue directement sur le site carrière" in messages["Data Scientist (H/F)"]
    assert "(date estimée : réception de l'alerte)" in messages["Data Analyst"]
    assert "(date du site d'emploi)" in messages["Data Engineer"]
    storage.close()


def test_only_offers_above_the_score_are_notified(env) -> None:
    config, storage, collect = env
    settings = config.model_copy(
        update={"notifications": config.notifications.model_copy(update={"notify_above_score": 50})}
    )
    collect(*_offers(4))
    ids = [row["id"] for row in storage.pending_notifications()]
    for offer_id, score in zip(ids, [80, 50, 30]):  # the fourth offer has no score
        storage.set_match_score(offer_id, score)
    sender = FakeSender()
    result = notify_pending(storage, settings, sender, silent=False)
    assert result.sent == 2 and result.below_score == 2  # 80 and the unscored offer; 50 and 30 are not
    assert storage.pending_notifications() == []  # low scores are never notified later
    notify_pending(storage, settings, sender, silent=False)
    assert len(sender.messages) == 2
