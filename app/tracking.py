"""Client for the open-tracking Worker in tracker/ (Cloudflare)."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import List, Optional

import requests

from . import config, db

# Opens this soon after sending are almost always mail-security scanners, not people.
AUTOMATIC_WITHIN_SECONDS = 60


class TrackerError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.tracker_url() and config.tracker_secret())


def new_token() -> str:
    return secrets.token_urlsafe(16)


def pixel_url(token: str) -> str:
    return f"{config.tracker_url()}/p/{token}.gif"


def fetch_opens() -> List[dict]:
    opens, cursor = [], None
    headers = {"Authorization": f"Bearer {config.tracker_secret()}"}
    while True:
        params = {"cursor": cursor} if cursor else {}
        try:
            resp = requests.get(f"{config.tracker_url()}/api/opens", params=params, headers=headers, timeout=20)
        except requests.RequestException as exc:
            raise TrackerError(f"Tracker not reachable: {exc}") from exc
        if resp.status_code == 401:
            raise TrackerError("Tracker refused the secret. TRACKER_SECRET in .env must match API_SECRET in Cloudflare.")
        if resp.status_code != 200:
            raise TrackerError(f"Tracker answered HTTP {resp.status_code}")
        data = resp.json()
        opens.extend(data.get("opens", []))
        cursor = data.get("cursor")
        if not cursor:
            return opens


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def sync_opens() -> int:
    """Copy new open events from the tracker into the database. Returns how many were new."""
    if not configured():
        return 0
    events = fetch_opens()
    new = 0
    with db.session() as conn:
        emails = {
            r["tracking_token"]: r
            for r in conn.execute("SELECT id, sent_at, tracking_token FROM emails WHERE tracking_token IS NOT NULL")
        }
        for event in events:
            email = emails.get(event.get("token"))
            if email is None:
                continue
            opened = _utc(int(event["t"]))
            sent = db.parse_time(email["sent_at"])
            automatic = sent is not None and (opened - sent).total_seconds() < AUTOMATIC_WITHIN_SECONDS
            cur = conn.execute(
                "INSERT OR IGNORE INTO opens(email_id, event_key, opened_at, user_agent, likely_automatic) "
                "VALUES (?, ?, ?, ?, ?)",
                (email["id"], event["key"], opened.strftime(db.TIME_FMT), (event.get("ua") or "")[:200], int(automatic)),
            )
            new += cur.rowcount
    return new


def check() -> Optional[str]:
    """None if the tracker works, otherwise a human-readable problem."""
    if not configured():
        return "Not set up (TRACKER_URL / TRACKER_SECRET missing in .env)."
    try:
        fetch_opens()
    except TrackerError as exc:
        return str(exc)
    return None
