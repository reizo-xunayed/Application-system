"""SQLite storage. One connection per call site; WAL so background jobs and pages can overlap."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    street TEXT,
    plz TEXT,
    city TEXT,
    region TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    website TEXT,
    website_source TEXT,
    email TEXT,
    email_source TEXT,
    lookup_state TEXT NOT NULL DEFAULT 'pending',
    lookup_note TEXT,
    looked_up_at TEXT,
    outcome TEXT,
    excluded INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    replied_at TEXT,
    reply_from TEXT,
    reply_snippet TEXT,
    auto_reply_at TEXT,
    bounced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    refnr TEXT NOT NULL UNIQUE,
    title TEXT,
    occupation TEXT,
    occupation_key TEXT,
    city TEXT,
    plz TEXT,
    start_date TEXT,
    published TEXT,
    external_url TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS listings_company ON listings(company_id);

CREATE TABLE IF NOT EXISTS contact_candidates (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    score INTEGER NOT NULL,
    source_url TEXT,
    UNIQUE(company_id, email)
);

CREATE TABLE IF NOT EXISTS drafts (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    gmail_id TEXT,
    thread_id TEXT,
    message_id_header TEXT,
    tracking_token TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS emails_company ON emails(company_id);

CREATE TABLE IF NOT EXISTS opens (
    id INTEGER PRIMARY KEY,
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL UNIQUE,
    opened_at TEXT NOT NULL,
    user_agent TEXT,
    likely_automatic INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS opens_email ON opens(email_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_SETTINGS = {
    "sender_name": "",
    "subject_template": "",
    "body_template": "",
    "followup_body_template": "",
    "daily_limit": "15",
    "followup_days": "10",
    "attach_files": "1",
}

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def now() -> str:
    """Current UTC time in the format stored in the database."""
    return datetime.now(timezone.utc).strftime(TIME_FMT)


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value[:19], TIME_FMT).replace(tzinfo=timezone.utc)


def connect() -> sqlite3.Connection:
    config.ensure_data_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Connection that commits on success and always closes."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with session() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))


def get_settings(conn: sqlite3.Connection) -> dict:
    values = dict(DEFAULT_SETTINGS)
    values.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")})
    return values


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
