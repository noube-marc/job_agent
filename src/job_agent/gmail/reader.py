"""Read job alert e-mails from Gmail, behind a double lock.

Lock 1, the Gmail request: always limited to the configured label (by its
id), to e-mails received after `start_date`, and to the allowed senders.
Lock 2, in the code: every message returned is checked again (label,
date and exact sender address) before its content is read.

Without a label or without any allowed sender, no request is made. The
agent only reads: it never modifies, moves or deletes an e-mail.
"""

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from email.utils import parseaddr
from typing import Any

from job_agent.config.models import EmailAlertsSource


class GmailLockError(Exception):
    """The request would not be limited enough: nothing is read."""


@dataclass
class AlertEmail:
    message_id: str
    provider: str
    sender: str
    subject: str
    received_at: datetime
    html: str
    text: str


def allowed_senders(settings: EmailAlertsSource) -> dict[str, str]:
    """Exact sender address (lower case) -> provider name, enabled providers only."""
    senders: dict[str, str] = {}
    for provider, block in settings.providers:
        if block.enabled:
            for address in block.allowed_senders:
                senders[address.strip().lower()] = provider
    return senders


def build_query(start_date: date, senders: list[str]) -> str:
    if not senders:
        raise GmailLockError("aucun expéditeur autorisé (allowed_senders) : aucune lecture.")
    return f"after:{start_date:%Y/%m/%d} from:({' OR '.join(sorted(senders))})"


def _header(message: dict, name: str) -> str:
    for header in (message.get("payload") or {}).get("headers") or []:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _received_at(message: dict) -> datetime:
    return datetime.fromtimestamp(int(message.get("internalDate", "0")) / 1000, tz=timezone.utc)


def _bodies(part: dict, found: dict[str, str]) -> None:
    mime = part.get("mimeType", "")
    data = (part.get("body") or {}).get("data")
    if data and mime in ("text/html", "text/plain") and mime not in found:
        found[mime] = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    for child in part.get("parts") or []:
        _bodies(child, found)


class GmailReader:
    def __init__(self, service: Any, settings: EmailAlertsSource) -> None:
        self._messages = service.users().messages()
        self._labels = service.users().labels()
        self._settings = settings
        label = settings.gmail_label.strip()
        if not label:
            raise GmailLockError("aucun libellé Gmail configuré (gmail_label) : aucune lecture.")
        self._label_id = self._find_label(label)
        self._start = datetime.combine(settings.start_date, time(0, 0), timezone.utc)

    def _find_label(self, name: str) -> str:
        labels = self._labels.list(userId="me").execute().get("labels") or []
        for label in labels:
            if label.get("name", "").lower() == name.lower():
                return label["id"]
        raise GmailLockError(f"libellé Gmail « {name} » introuvable : créez-le dans Gmail.")

    def _list(self, query: str) -> list[str]:
        ids: list[str] = []
        token = None
        while True:
            answer = self._messages.list(
                userId="me", labelIds=[self._label_id], q=query, maxResults=100, pageToken=token
            ).execute()
            ids += [m["id"] for m in answer.get("messages") or []]
            token = answer.get("nextPageToken")
            if not token or len(ids) >= 500:
                return ids

    def _passes_lock(self, message: dict, senders: dict[str, str] | None) -> str | None:
        """Lock 2. Returns the provider, or None if the message must not be read."""
        if self._label_id not in (message.get("labelIds") or []):
            return None
        if _received_at(message) < self._start:
            return None
        if senders is None:
            return "?"
        address = parseaddr(_header(message, "From"))[1].lower()
        return senders.get(address)

    def read_alerts(self, skip: set[str] = frozenset()) -> list[AlertEmail]:
        """Alert e-mails not read yet (`skip`: message ids already processed)."""
        senders = allowed_senders(self._settings)
        query = build_query(self._settings.start_date, list(senders))
        alerts = []
        for message_id in self._list(query):
            if message_id in skip:
                continue
            message = self._messages.get(userId="me", id=message_id, format="full").execute()
            provider = self._passes_lock(message, senders)
            if provider is None:
                continue
            bodies: dict[str, str] = {}
            _bodies(message.get("payload") or {}, bodies)
            alerts.append(AlertEmail(
                message_id=message_id, provider=provider,
                sender=parseaddr(_header(message, "From"))[1].lower(),
                subject=_header(message, "Subject"), received_at=_received_at(message),
                html=bodies.get("text/html", ""), text=bodies.get("text/plain", ""),
            ))
        return alerts

    def sender_summary(self) -> Counter[str]:
        """Senders of the labelled e-mails (headers only), to fill allowed_senders."""
        query = f"after:{self._settings.start_date:%Y/%m/%d}"
        counts: Counter[str] = Counter()
        for message_id in self._list(query):
            message = self._messages.get(
                userId="me", id=message_id, format="metadata", metadataHeaders=["From"]
            ).execute()
            if self._passes_lock(message, None):
                counts[parseaddr(_header(message, "From"))[1].lower()] += 1
        return counts


def build_service():
    from googleapiclient.discovery import build

    from job_agent.gmail.auth import load_credentials

    return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)
