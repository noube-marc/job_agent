"""Telegram notifications.

Safeguards (CLAUDE.md, section 5):
- an offer group is marked as notified only AFTER a successful send;
- the very first run is silent (`silent_first_run`);
- above `max_per_run` new offers, a single summary message is sent;
- duplicates share a group, so the same job is never notified twice.

The bot token is part of the API URL: it is never printed nor logged.
Errors are reported with their type and HTTP status only.
"""

import html
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from job_agent.config.models import AppConfig
from job_agent.processing.filters import (
    CONTRACT_LABELS,
    FLAG_DATE_ESTIMATED,
    FLAG_EXPERIENCE_UNSPECIFIED,
)
from job_agent.storage import Storage

API_URL = "https://api.telegram.org/bot{token}/{method}"
TOKEN_VARIABLE = "TELEGRAM_BOT_TOKEN"
CHAT_VARIABLE = "TELEGRAM_CHAT_ID"
MAX_SUMMARY_LINES = 15


class NotificationError(Exception):
    """A message could not be sent. The message never contains a secret."""


class TelegramClient:
    def __init__(self, token: str, timeout: float = 20) -> None:
        self._token = token
        self._timeout = timeout

    def call(self, method: str, payload: dict | None = None) -> dict:
        url = API_URL.format(token=self._token, method=method)
        try:
            response = httpx.post(url, json=payload or {}, timeout=self._timeout)
        except httpx.HTTPError as error:
            raise NotificationError(f"Telegram injoignable ({type(error).__name__})") from None
        if response.status_code != 200:
            description = ""
            try:
                description = response.json().get("description", "")
            except ValueError:
                pass
            raise NotificationError(f"Telegram a refusé l'envoi (HTTP {response.status_code}) {description}".strip())
        return response.json().get("result", {})

    def send(self, chat_id: str, text: str) -> None:
        self.call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )


# ---------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------
def _main_link(members: list[sqlite3.Row]) -> str:
    ordered = sorted(members, key=lambda row: (row["source"] != "career_page", row["id"]))
    first = ordered[0]
    return first["apply_url"] or first["url"]


def format_offer(main: sqlite3.Row, members: list[sqlite3.Row], config: AppConfig) -> str:
    fields = set(config.notifications.fields)
    flags = json.loads(main["flags"])
    zone = ZoneInfo(config.schedule.timezone)
    e = html.escape
    lines: list[str] = []

    if "title" in fields:
        lines.append(f"<b>{e(main['title'])}</b>")
    if main["junior"]:
        lines.append("🎯 Poste junior")
    if "company" in fields and (main["company"] or main["company_key"]):
        lines.append(f"🏢 {e(main['company'] or main['company_key'])}")
    details = []
    if "location" in fields and main["location"]:
        details.append(f"📍 {e(main['location'])}")
    if "contract" in fields and main["contract_type"]:
        details.append(e(CONTRACT_LABELS.get(main["contract_type"], main["contract_type"])))
    if details:
        lines.append(" · ".join(details))
    if "publication_date" in fields and main["effective_date"]:
        local = datetime.fromisoformat(main["effective_date"]).astimezone(zone)
        estimated = " (date estimée)" if FLAG_DATE_ESTIMATED in flags else ""
        lines.append(f"🗓 Publiée le {local:%d/%m à %H:%M}{estimated}")

    remarks = []
    if "priority_flag" in fields and main["priority_sector"]:
        remarks.append("⭐ secteur prioritaire")
    if "experience_flag" in fields and FLAG_EXPERIENCE_UNSPECIFIED in flags:
        remarks.append("expérience non précisée")
    remarks += [flag for flag in flags if flag.startswith(("écart technique", "expérience incohérente", "doublon probable"))
                or flag.endswith("(non bloquant : poste junior)")]
    if remarks:
        lines.append("ℹ️ " + e(" · ".join(remarks)))
    if "url" in fields:
        lines.append(f'<a href="{e(_main_link(members), quote=True)}">Voir l\'offre et postuler</a>')
    return "\n".join(lines)


def format_summary(pending: list[sqlite3.Row]) -> str:
    lines = [f"<b>{len(pending)} nouvelles offres</b> (au-delà du plafond par passage)."]
    for row in pending[:MAX_SUMMARY_LINES]:
        lines.append(f"• {html.escape(row['title'])} ({html.escape(row['company'] or row['company_key'])})")
    if len(pending) > MAX_SUMMARY_LINES:
        lines.append(f"… et {len(pending) - MAX_SUMMARY_LINES} autres.")
    lines.append("Toutes les offres sont dans le fichier de suivi.")
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------
@dataclass
class NotifyResult:
    sent: int = 0
    silenced: int = 0
    summary: bool = False
    error: str | None = None


def notify_pending(
    storage: Storage,
    config: AppConfig,
    send: Callable[[str], None],
    silent: bool,
) -> NotifyResult:
    """Notify every kept offer not notified yet. `send` raises NotificationError."""
    pending = storage.pending_notifications()
    result = NotifyResult()
    if not pending:
        return result

    if silent:
        storage.mark_notified([row["group_id"] for row in pending], "silent_first_run")
        result.silenced = len(pending)
        return result

    try:
        if len(pending) > config.notifications.max_per_run:
            send(format_summary(pending))
            storage.mark_notified([row["group_id"] for row in pending], "telegram_summary")
            result.summary = True
            result.sent = len(pending)
            return result
        for row in pending:
            send(format_offer(row, storage.group_members(row["group_id"]), config))
            storage.mark_notified([row["group_id"]], "telegram")
            result.sent += 1
    except NotificationError as error:
        # Remaining offers stay pending and are retried at the next run.
        result.error = str(error)
    return result


def telegram_sender_from_env() -> Callable[[str], None]:
    """Sender using TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env."""
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    chat_id = os.environ.get(CHAT_VARIABLE, "").strip()
    missing = [name for name, value in ((TOKEN_VARIABLE, token), (CHAT_VARIABLE, chat_id)) if not value]
    if missing:
        raise NotificationError(f"Variable(s) absente(s) du fichier .env : {', '.join(missing)}")
    client = TelegramClient(token)
    return lambda text: client.send(chat_id, text)


def telegram_setup() -> int:
    """Help the user find their chat id after creating the bot."""
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not token:
        print(f"{TOKEN_VARIABLE} est absent du fichier .env. Créez d'abord le bot avec @BotFather.")
        return 1
    client = TelegramClient(token)
    try:
        bot = client.call("getMe")
        updates = client.call("getUpdates")
    except NotificationError as error:
        print(f"Échec : {error}. Vérifiez {TOKEN_VARIABLE} dans .env.")
        return 1

    print(f"Bot trouvé : @{bot.get('username', '?')}")
    chats: dict[str, str] = {}
    for update in updates:
        message = update.get("message") or update.get("my_chat_member") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            chats[str(chat["id"])] = chat.get("first_name") or chat.get("title") or chat.get("type", "")
    if not chats:
        print("Aucun message reçu. Envoyez un message (par exemple « bonjour ») à votre bot, puis relancez cette commande.")
        return 1
    for chat_id, name in chats.items():
        print(f"  Conversation « {name} » : TELEGRAM_CHAT_ID={chat_id}")
    print("Copiez la ligne TELEGRAM_CHAT_ID=... dans votre fichier .env.")
    return 0
