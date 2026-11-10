"""Local SQLite memory of the agent.

Tables:
- offers: every collected offer, with its full text, raw source data,
  decision (kept or rejected), reasons and warnings. Offers recognised
  as the same job share a `group_id` (the id of the first one seen).
- notifications: groups already notified (written only after a
  successful send, so a failed send is retried at the next run).
- tracking: offers already written to the tracking file.
- runs / source_runs: history of runs and number of offers per source.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_offer_id TEXT NOT NULL,
    company_key TEXT NOT NULL,
    company TEXT,
    sector TEXT,
    priority_sector INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    location TEXT,
    department_code TEXT,
    contract_type TEXT,
    work_mode TEXT,
    reference TEXT,
    url TEXT NOT NULL,
    apply_url TEXT,
    description TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    effective_date TEXT,
    first_seen_at TEXT NOT NULL,
    kept INTEGER NOT NULL,
    reasons TEXT NOT NULL DEFAULT '[]',
    flags TEXT NOT NULL DEFAULT '[]',
    min_experience_years INTEGER,
    offer_json TEXT NOT NULL,
    UNIQUE (source_name, source_offer_id)
);
CREATE INDEX IF NOT EXISTS offers_company ON offers (company_key);
CREATE INDEX IF NOT EXISTS offers_group ON offers (group_id);

CREATE TABLE IF NOT EXISTS notifications (
    group_id INTEGER PRIMARY KEY,
    notified_at TEXT NOT NULL,
    channel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracking (
    offer_id INTEGER PRIMARY KEY,
    written_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    silent INTEGER NOT NULL DEFAULT 0,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
    run_id INTEGER NOT NULL,
    source_name TEXT NOT NULL,
    collected INTEGER NOT NULL,
    error TEXT,
    PRIMARY KEY (run_id, source_name)
);

CREATE TABLE IF NOT EXISTS email_messages (
    message_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    offers INTEGER NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    """UTC ISO text, so that dates sort correctly as strings."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection:
            yield self.connection

    # --- offers -------------------------------------------------------
    def has_offer(self, source_name: str, source_offer_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM offers WHERE source_name = ? AND source_offer_id = ?",
            (source_name, source_offer_id),
        ).fetchone()
        return row is not None

    def offers_of_company(self, company_key: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM offers WHERE company_key = ? AND id = group_id", (company_key,)
        ).fetchall()

    def insert_offer(self, values: dict[str, Any], group_id: int | None = None) -> int:
        """Insert an offer. Without group_id, it starts its own group."""
        row = dict(values)
        for key in ("reasons", "flags"):
            row[key] = json.dumps(row.get(key, []), ensure_ascii=False)
        for key in ("published_at", "effective_date", "first_seen_at"):
            if isinstance(row.get(key), datetime):
                row[key] = _iso(row[key])
        row["kept"] = int(bool(row["kept"]))
        row["priority_sector"] = int(bool(row.get("priority_sector")))
        row["group_id"] = group_id if group_id is not None else -1

        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        with self.transaction() as db:
            cursor = db.execute(f"INSERT INTO offers ({columns}) VALUES ({placeholders})", row)
            offer_id = int(cursor.lastrowid)
            if group_id is None:
                db.execute("UPDATE offers SET group_id = id WHERE id = ?", (offer_id,))
        return offer_id

    def group_members(self, group_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM offers WHERE group_id = ? ORDER BY id", (group_id,)
        ).fetchall()

    def add_flag(self, offer_id: int, flag: str) -> None:
        row = self.connection.execute("SELECT flags FROM offers WHERE id = ?", (offer_id,)).fetchone()
        flags = json.loads(row["flags"])
        if flag not in flags:
            flags.append(flag)
            with self.transaction() as db:
                db.execute(
                    "UPDATE offers SET flags = ? WHERE id = ?",
                    (json.dumps(flags, ensure_ascii=False), offer_id),
                )

    # --- notifications ------------------------------------------------
    def pending_notifications(self) -> list[sqlite3.Row]:
        """Kept offers (one per group) never notified, newest first."""
        return self.connection.execute(
            """SELECT o.* FROM offers o
               LEFT JOIN notifications n ON n.group_id = o.group_id
               WHERE o.id = o.group_id AND o.kept = 1 AND n.group_id IS NULL
               ORDER BY o.effective_date DESC, o.priority_sector DESC, o.id"""
        ).fetchall()

    def mark_notified(self, group_ids: list[int], channel: str) -> None:
        now = _iso(utc_now())
        with self.transaction() as db:
            db.executemany(
                "INSERT OR IGNORE INTO notifications (group_id, notified_at, channel) VALUES (?, ?, ?)",
                [(group_id, now, channel) for group_id in group_ids],
            )

    def is_notified(self, group_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM notifications WHERE group_id = ?", (group_id,)
        ).fetchone()
        return row is not None

    # --- tracking file ------------------------------------------------
    def pending_tracking(self) -> list[sqlite3.Row]:
        """Main offers of each group not yet written to the tracking file."""
        return self.connection.execute(
            """SELECT o.* FROM offers o
               LEFT JOIN tracking t ON t.offer_id = o.id
               WHERE o.id = o.group_id AND t.offer_id IS NULL
               ORDER BY o.effective_date DESC, o.priority_sector DESC, o.id"""
        ).fetchall()

    def mark_tracked(self, offer_ids: list[int]) -> None:
        now = _iso(utc_now())
        with self.transaction() as db:
            db.executemany(
                "INSERT OR IGNORE INTO tracking (offer_id, written_at) VALUES (?, ?)",
                [(offer_id, now) for offer_id in offer_ids],
            )

    # --- runs ---------------------------------------------------------
    def has_completed_run(self) -> bool:
        row = self.connection.execute("SELECT 1 FROM runs WHERE finished_at IS NOT NULL").fetchone()
        return row is not None

    def start_run(self, silent: bool) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO runs (started_at, silent) VALUES (?, ?)", (_iso(utc_now()), int(silent))
            )
        return int(cursor.lastrowid)

    def record_source(self, run_id: int, source_name: str, collected: int, error: str | None) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO source_runs (run_id, source_name, collected, error) VALUES (?, ?, ?, ?)",
                (run_id, source_name, collected, error),
            )

    def finish_run(self, run_id: int, summary: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
                (_iso(utc_now()), json.dumps(summary, ensure_ascii=False), run_id),
            )

    def zero_streak(self, source_name: str) -> int:
        """Number of latest consecutive runs where the source returned nothing."""
        rows = self.connection.execute(
            "SELECT collected FROM source_runs WHERE source_name = ? ORDER BY run_id DESC LIMIT 50",
            (source_name,),
        ).fetchall()
        streak = 0
        for row in rows:
            if row["collected"] != 0:
                break
            streak += 1
        return streak

    # --- e-mail alerts ------------------------------------------------
    def processed_emails(self) -> set[str]:
        return {row[0] for row in self.connection.execute("SELECT message_id FROM email_messages")}

    def mark_email_processed(self, message_id: str, provider: str, offers: int) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO email_messages (message_id, provider, offers, processed_at) VALUES (?, ?, ?, ?)",
                (message_id, provider, offers, _iso(utc_now())),
            )

    # --- small persistent values --------------------------------------
    def get_state(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))

    # --- date corrections ---------------------------------------------
    def get_offer(self, offer_id: int) -> sqlite3.Row:
        return self.connection.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()

    def update_offer_date(
        self, offer_id: int, published_at: datetime, effective_date: datetime, flags: list[str]
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE offers SET published_at = ?, effective_date = ?, flags = ? WHERE id = ?",
                (_iso(published_at), _iso(effective_date), json.dumps(flags, ensure_ascii=False), offer_id),
            )

    def reject_offer(self, offer_id: int, reason: str) -> None:
        row = self.get_offer(offer_id)
        reasons = json.loads(row["reasons"]) + [reason]
        with self.transaction() as db:
            db.execute(
                "UPDATE offers SET kept = 0, reasons = ? WHERE id = ?",
                (json.dumps(reasons, ensure_ascii=False), offer_id),
            )

    # --- messages waiting to be sent (warnings, links) ------------------
    def add_to_outbox(self, key: str, message: str) -> None:
        """Queue a Telegram message; a key is only ever queued once."""
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO outbox (key, message, created_at) VALUES (?, ?, ?)",
                (key, message, _iso(utc_now())),
            )

    def pending_outbox(self) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT id, key, message FROM outbox WHERE sent = 0 ORDER BY id").fetchall()

    def mark_outbox_sent(self, message_id: int) -> None:
        with self.transaction() as db:
            db.execute("UPDATE outbox SET sent = 1 WHERE id = ?", (message_id,))
