"""Local SQLite memory of the agent.

Tables:
- offers: every collected offer, with its full text, raw source data,
  decision (kept or rejected), reasons and warnings. Offers recognised
  as the same job share a `group_id` (the id of the first one seen).
- notifications: groups already notified (written only after a
  successful send, so a failed send is retried at the next run).
- tracking: offers already written to the tracking file.
- runs / source_runs: history of runs and number of offers per source.
- verification_events: changes of the career site verification status,
  appended to the "Vérifications" sheet of the tracking file.
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
    junior INTEGER NOT NULL DEFAULT 0,
    match_score INTEGER,
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

CREATE TABLE IF NOT EXISTS ai_extractions (
    offer_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_events (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    written INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS letters (
    offer_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
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
        self._migrate()

    def _migrate(self) -> None:
        """Add the columns created after the first version of the database."""
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(offers)")}
        with self.transaction() as db:
            if "junior" not in columns:
                db.execute("ALTER TABLE offers ADD COLUMN junior INTEGER NOT NULL DEFAULT 0")
            if "match_score" not in columns:
                db.execute("ALTER TABLE offers ADD COLUMN match_score INTEGER")
            if "verification" not in columns:
                db.execute("ALTER TABLE offers ADD COLUMN verification TEXT")
            if "last_seen_at" not in columns:
                db.execute("ALTER TABLE offers ADD COLUMN last_seen_at TEXT")

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
        for key in ("published_at", "effective_date", "first_seen_at", "last_seen_at"):
            if isinstance(row.get(key), datetime):
                row[key] = _iso(row[key])
        row["kept"] = int(bool(row["kept"]))
        row["priority_sector"] = int(bool(row.get("priority_sector")))
        row["junior"] = int(bool(row.get("junior")))
        row["group_id"] = group_id if group_id is not None else -1

        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        with self.transaction() as db:
            cursor = db.execute(f"INSERT INTO offers ({columns}) VALUES ({placeholders})", row)
            offer_id = int(cursor.lastrowid)
            if group_id is None:
                db.execute("UPDATE offers SET group_id = id WHERE id = ?", (offer_id,))
        return offer_id

    def touch_offer(self, source_name: str, source_offer_id: str, when: datetime) -> None:
        """The offer was listed again by its source."""
        with self.transaction() as db:
            db.execute(
                "UPDATE offers SET last_seen_at = ? WHERE source_name = ? AND source_offer_id = ?",
                (_iso(when), source_name, source_offer_id),
            )

    def published_dates(self, source_name: str) -> dict[str, str | None]:
        rows = self.connection.execute(
            "SELECT source_offer_id, published_at FROM offers WHERE source_name = ?", (source_name,)
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    # --- career site verification -------------------------------------
    def set_verification(self, group_id: int, status: str, detail: str, when: datetime) -> None:
        """Change the status of a group and log the change (nothing if unchanged)."""
        row = self.get_offer(group_id)
        if row is None or row["verification"] == status:
            return
        with self.transaction() as db:
            db.execute("UPDATE offers SET verification = ? WHERE id = ?", (status, group_id))
            db.execute(
                "INSERT INTO verification_events (group_id, at, old_status, new_status, detail) VALUES (?, ?, ?, ?, ?)",
                (group_id, _iso(when), row["verification"], status, detail),
            )

    def groups_with_verification(self, status: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM offers WHERE id = group_id AND kept = 1 AND verification = ? ORDER BY id", (status,)
        ).fetchall()

    def career_offers_of_company(self, company_key: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM offers WHERE company_key = ? AND source = 'career_page'", (company_key,)
        ).fetchall()

    def tracked_career_offers(self, company_key: str) -> list[sqlite3.Row]:
        """Career site offers of kept groups already in the tracking file."""
        return self.connection.execute(
            """SELECT m.* FROM offers m
               JOIN offers g ON g.id = m.group_id
               JOIN tracking t ON t.offer_id = g.id
               WHERE m.company_key = ? AND m.source = 'career_page' AND g.kept = 1
                 AND COALESCE(g.verification, '') != 'disappeared'""",
            (company_key,),
        ).fetchall()

    def unwritten_verification_events(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT e.*, o.company, o.company_key, o.title FROM verification_events e
               JOIN offers o ON o.id = e.group_id
               WHERE e.written = 0 ORDER BY e.id"""
        ).fetchall()

    def mark_verification_events_written(self, event_ids: list[int]) -> None:
        with self.transaction() as db:
            db.executemany("UPDATE verification_events SET written = 1 WHERE id = ?", [(i,) for i in event_ids])

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
               ORDER BY substr(o.effective_date, 1, 10) DESC, o.junior DESC, o.priority_sector DESC,
                        o.effective_date DESC, o.id"""
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
               ORDER BY substr(o.effective_date, 1, 10) DESC, o.junior DESC, o.priority_sector DESC,
                        o.effective_date DESC, o.id"""
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

    def sources_of_run(self, run_id: int) -> list[str]:
        return [row[0] for row in self.connection.execute(
            "SELECT source_name FROM source_runs WHERE run_id = ? ORDER BY source_name", (run_id,))]

    def last_error(self, source_name: str) -> str | None:
        row = self.connection.execute(
            "SELECT error FROM source_runs WHERE source_name = ? AND error IS NOT NULL ORDER BY run_id DESC LIMIT 1",
            (source_name,),
        ).fetchone()
        return row[0] if row else None

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

    def delete_state(self, key: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM app_state WHERE key = ?", (key,))

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

    AI_FIELDS = ("contract_type", "min_experience_years", "work_mode")

    def update_offer_from_ai(self, offer_id: int, fields: dict, removed_flags: list[str]) -> None:
        """Fill fields the rules left empty, and drop the matching warnings."""
        fields = {key: value for key, value in fields.items() if key in self.AI_FIELDS}
        row = self.get_offer(offer_id)
        flags = [flag for flag in json.loads(row["flags"]) if flag not in removed_flags]
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.transaction() as db:
            db.execute(
                f"UPDATE offers SET {assignments + ', ' if assignments else ''}flags = ? WHERE id = ?",
                (*fields.values(), json.dumps(flags, ensure_ascii=False), offer_id),
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

    # --- AI extraction cache ------------------------------------------
    def offers_needing_ai(self, limit: int, min_description: int = 200) -> list[sqlite3.Row]:
        """Kept offers with a real description, not analysed, not notified yet."""
        return self.connection.execute(
            """SELECT o.* FROM offers o
               LEFT JOIN ai_extractions a ON a.offer_id = o.id
               LEFT JOIN notifications n ON n.group_id = o.group_id
               WHERE o.id = o.group_id AND o.kept = 1 AND a.offer_id IS NULL AND n.group_id IS NULL
                 AND length(o.description) >= ?
               ORDER BY o.effective_date DESC LIMIT ?""",
            (min_description, limit),
        ).fetchall()

    def letter_result(self, offer_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM letters WHERE offer_id = ?", (offer_id,)).fetchone()

    def save_letter(self, offer_id: int, model: str, result: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO letters (offer_id, model, result, created_at) VALUES (?, ?, ?, ?)",
                (offer_id, model, result, _iso(utc_now())),
            )

    def ai_result(self, offer_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM ai_extractions WHERE offer_id = ?", (offer_id,)).fetchone()

    def save_ai_result(self, offer_id: int, model: str, result_json: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO ai_extractions (offer_id, model, result, created_at) VALUES (?, ?, ?, ?)",
                (offer_id, model, result_json, _iso(utc_now())),
            )

    # --- offer / CV match score ---------------------------------------
    def offers_needing_score(self, limit: int = 200) -> list[sqlite3.Row]:
        """Kept offers without a score, not yet written to the tracking file."""
        return self.connection.execute(
            """SELECT o.* FROM offers o
               LEFT JOIN tracking t ON t.offer_id = o.id
               WHERE o.id = o.group_id AND o.kept = 1 AND o.match_score IS NULL AND t.offer_id IS NULL
               ORDER BY o.id LIMIT ?""",
            (limit,),
        ).fetchall()

    def set_match_score(self, offer_id: int, score: int) -> None:
        with self.transaction() as db:
            db.execute("UPDATE offers SET match_score = ? WHERE id = ?", (score, offer_id))
