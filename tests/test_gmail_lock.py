"""Gmail double lock (CLAUDE.md, section 14, point 9): no read outside the
label, the start date and the allowed senders."""

import base64
from datetime import date, datetime, timezone

import pytest

from job_agent.config.models import EmailAlertsSource
from job_agent.gmail.reader import GmailLockError, GmailReader, build_query

LABEL_ID = "Label_42"


def _ms(day: datetime) -> str:
    return str(int(day.timestamp() * 1000))


def _message(message_id: str, sender: str, labels: list[str], received: datetime) -> dict:
    html = base64.urlsafe_b64encode(b"<p>Offre</p>").decode().rstrip("=")
    return {
        "id": message_id, "labelIds": labels, "internalDate": _ms(received),
        "payload": {"mimeType": "multipart/alternative",
                    "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "Alerte"}],
                    "parts": [{"mimeType": "text/html", "body": {"data": html}}]},
    }


MESSAGES = {
    "ok": _message("ok", "LinkedIn <jobalerts-noreply@linkedin.com>", [LABEL_ID], datetime(2026, 11, 10, tzinfo=timezone.utc)),
    "other-sender": _message("other-sender", "RH <rh@entreprise.fr>", [LABEL_ID], datetime(2026, 11, 10, tzinfo=timezone.utc)),
    "no-label": _message("no-label", "jobalerts-noreply@linkedin.com", ["INBOX"], datetime(2026, 11, 10, tzinfo=timezone.utc)),
    "too-old": _message("too-old", "jobalerts-noreply@linkedin.com", [LABEL_ID], datetime(2026, 10, 1, tzinfo=timezone.utc)),
}


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeGmail:
    """Mimics service.users().messages()/labels(); records every call.

    It returns every message whatever the query, to check that the code
    itself (lock 2) refuses what Gmail should not have returned.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def users(self):
        return self

    def labels(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        if "labelIds" not in kwargs:  # labels().list()
            return Call({"labels": [{"id": "INBOX", "name": "INBOX"}, {"id": LABEL_ID, "name": "Alertes-emploi"}]})
        return Call({"messages": [{"id": key} for key in MESSAGES]})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return Call(MESSAGES[kwargs["id"]])


def _settings(**overrides) -> EmailAlertsSource:
    values = {
        "enabled": True, "gmail_label": "Alertes-emploi", "start_date": date(2026, 11, 9),
        "providers": {"linkedin": {"enabled": True, "allowed_senders": ["jobalerts-noreply@linkedin.com"]}},
    }
    values.update(overrides)
    return EmailAlertsSource(**values)


def test_only_allowed_messages_are_returned() -> None:
    gmail = FakeGmail()
    alerts = GmailReader(gmail, _settings()).read_alerts()
    assert [a.message_id for a in alerts] == ["ok"]
    assert alerts[0].provider == "linkedin" and alerts[0].html == "<p>Offre</p>"


def test_every_list_request_is_limited() -> None:
    gmail = FakeGmail()
    GmailReader(gmail, _settings()).read_alerts()
    message_lists = [kw for name, kw in gmail.calls if name == "list" and "labelIds" in kw]
    assert message_lists
    for kwargs in message_lists:
        assert kwargs["labelIds"] == [LABEL_ID]
        assert "after:2026/11/09" in kwargs["q"]
        assert "from:(jobalerts-noreply@linkedin.com)" in kwargs["q"]


def test_skip_already_processed_messages() -> None:
    gmail = FakeGmail()
    assert GmailReader(gmail, _settings()).read_alerts(skip={"ok"}) == []


def test_no_request_without_allowed_senders() -> None:
    with pytest.raises(GmailLockError):
        build_query(date(2026, 11, 9), [])
    gmail = FakeGmail()
    reader = GmailReader(gmail, _settings(providers={"linkedin": {"enabled": True, "allowed_senders": []}}))
    with pytest.raises(GmailLockError):
        reader.read_alerts()
    assert not [kw for name, kw in gmail.calls if name == "list" and "labelIds" in kw]


def test_no_request_without_label() -> None:
    gmail = FakeGmail()
    with pytest.raises(GmailLockError):
        GmailReader(gmail, _settings(gmail_label="Inconnu"))
    assert [name for name, _ in gmail.calls] == ["list"]  # only the label list was read


def test_disabled_provider_is_not_read() -> None:
    gmail = FakeGmail()
    settings = _settings(providers={"linkedin": {"enabled": False, "allowed_senders": ["jobalerts-noreply@linkedin.com"]}})
    with pytest.raises(GmailLockError):
        GmailReader(gmail, settings).read_alerts()


def test_sender_summary_reads_headers_only() -> None:
    gmail = FakeGmail()
    counts = GmailReader(gmail, _settings()).sender_summary()
    assert counts == {"jobalerts-noreply@linkedin.com": 1, "rh@entreprise.fr": 1}
    gets = [kw for name, kw in gmail.calls if name == "get"]
    assert gets and all(kw["format"] == "metadata" for kw in gets)
