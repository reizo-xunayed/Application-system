"""Company stages, sending first emails and follow-ups, and checking Gmail for replies."""
from __future__ import annotations

import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

from . import db, gmail, mailer, tracking
from .contacts import GENERIC_MAIL_DOMAINS, registrable_domain

Log = Callable[[str], None]
Progress = Callable[[int, int], None]
PAUSE_BETWEEN_SENDS = (8, 20)  # seconds; looks less like a bulk mailer

COMPANY_QUERY = """
SELECT c.*,
  (SELECT MIN(e.sent_at) FROM emails e
     WHERE e.company_id = c.id AND e.kind = 'initial' AND e.to_addr = c.email) AS sent_at,
  (SELECT COUNT(*) FROM emails e
     WHERE e.company_id = c.id AND e.kind = 'followup' AND e.to_addr = c.email) AS followups,
  (SELECT MIN(o.opened_at) FROM opens o JOIN emails e ON e.id = o.email_id
     WHERE e.company_id = c.id AND e.to_addr = c.email AND o.likely_automatic = 0) AS first_open,
  (SELECT COUNT(*) FROM opens o JOIN emails e ON e.id = o.email_id
     WHERE e.company_id = c.id AND e.to_addr = c.email AND o.likely_automatic = 0) AS open_count,
  (SELECT GROUP_CONCAT(DISTINCT l.occupation_key) FROM listings l WHERE l.company_id = c.id) AS occupation_keys,
  (SELECT COUNT(*) FROM listings l WHERE l.company_id = c.id) AS listing_count
FROM companies c
"""

STAGES: Dict[str, str] = {
    "new": "Not searched yet",
    "no_contact": "No contact found",
    "ready": "Ready to send",
    "sent": "Sent",
    "opened": "Opened",
    "replied": "Replied",
    "bounced": "Bounced",
    "positive": "Positive",
    "rejected": "Rejected",
    "skipped": "Skipped",
}


def stage(row: sqlite3.Row) -> str:
    if row["excluded"]:
        return "skipped"
    if row["outcome"]:
        return row["outcome"]
    if row["replied_at"]:
        return "replied"
    if row["bounced_at"]:
        return "bounced"
    if row["sent_at"]:
        return "opened" if row["first_open"] else "sent"
    if row["email"]:
        return "ready"
    if row["lookup_state"] == "pending":
        return "new"
    return "no_contact"


def is_followup_due(row: sqlite3.Row, days: int, now: Optional[datetime] = None) -> bool:
    if stage(row) not in ("sent", "opened") or row["followups"]:
        return False
    sent = db.parse_time(row["sent_at"])
    return sent is not None and sent <= (now or datetime.now(timezone.utc)) - timedelta(days=days)


def companies(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> List[sqlite3.Row]:
    return conn.execute(f"{COMPANY_QUERY} {where}", params).fetchall()


def company(conn: sqlite3.Connection, company_id: int) -> Optional[sqlite3.Row]:
    rows = companies(conn, "WHERE c.id = ?", (company_id,))
    return rows[0] if rows else None


def sent_today(conn: sqlite3.Connection) -> int:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    since = local_midnight.astimezone(timezone.utc).strftime(db.TIME_FMT)
    return conn.execute("SELECT COUNT(*) FROM emails WHERE sent_at >= ?", (since,)).fetchone()[0]


def _send(settings: dict, my_addr: str, to_addr: str, subject: str, body: str, *, attachments=(),
          thread_id: Optional[str] = None, in_reply_to: Optional[str] = None) -> dict:
    token = tracking.new_token() if tracking.configured() else None
    raw = mailer.build_message(
        settings["sender_name"],
        my_addr,
        to_addr,
        subject,
        body,
        pixel_url=tracking.pixel_url(token) if token else None,
        attachments=attachments,
        in_reply_to=in_reply_to,
    )
    result = gmail.send(raw, thread_id=thread_id)
    try:
        message_id = gmail.message_id_header(result["id"])
    except gmail.GmailError:
        message_id = None
    return {"gmail_id": result.get("id"), "thread_id": result.get("threadId"), "message_id": message_id, "token": token}


def _record(conn: sqlite3.Connection, company_id: int, kind: str, to_addr: str, subject: str, body: str, sent: dict) -> None:
    conn.execute(
        "INSERT INTO emails(company_id, kind, to_addr, subject, body, sent_at, gmail_id, thread_id, "
        "message_id_header, tracking_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, kind, to_addr, subject, body, db.now(), sent["gmail_id"], sent["thread_id"], sent["message_id"], sent["token"]),
    )


def _budget(conn: sqlite3.Connection, settings: dict) -> int:
    return max(0, int(settings.get("daily_limit") or 15) - sent_today(conn))


def send_first_emails(company_ids: List[int], log: Log, progress: Progress) -> int:
    with db.session() as conn:
        settings = db.get_settings(conn)
        budget = _budget(conn, settings)
    if not settings["subject_template"].strip() or not settings["body_template"].strip():
        raise ValueError("Write your email template in Settings first.")
    my_addr = gmail.my_address()
    attachments = mailer.attachment_files() if settings.get("attach_files") == "1" else []
    if attachments:
        log("Attaching: " + ", ".join(p.name for p in attachments))
    sent = 0
    progress(0, len(company_ids))
    for i, company_id in enumerate(company_ids):
        if sent >= budget:
            log(f"Daily limit reached ({settings['daily_limit']}). The rest stay in “Ready to send” for tomorrow.")
            break
        with db.session() as conn:
            row = company(conn, company_id)
            if row is None or stage(row) != "ready":
                log(f"Skipped {row['name'] if row else company_id}: not ready to send any more.")
                continue
            subject, body, _ = mailer.draft_for(conn, row, settings)
        if sent:
            time.sleep(random.uniform(*PAUSE_BETWEEN_SENDS))
        result = _send(settings, my_addr, row["email"], subject, body, attachments=attachments)
        with db.session() as conn:
            _record(conn, company_id, "initial", row["email"], subject, body, result)
            conn.execute("DELETE FROM drafts WHERE company_id = ?", (company_id,))
        sent += 1
        log(f"✉️ Sent to {row['name']} <{row['email']}>")
        progress(i + 1, len(company_ids))
    return sent


def followup_subject(subject: str) -> str:
    return subject if subject.lower().startswith(("re:", "aw:")) else f"Re: {subject}"


def send_followups(company_ids: List[int], log: Log, progress: Progress) -> int:
    with db.session() as conn:
        settings = db.get_settings(conn)
        budget = _budget(conn, settings)
    if not settings["followup_body_template"].strip():
        raise ValueError("Write your follow-up text in Settings first.")
    my_addr = gmail.my_address()
    days = int(settings.get("followup_days") or 10)
    sent = 0
    progress(0, len(company_ids))
    for i, company_id in enumerate(company_ids):
        if sent >= budget:
            log(f"Daily limit reached ({settings['daily_limit']}). Send the rest tomorrow.")
            break
        with db.session() as conn:
            row = company(conn, company_id)
            if row is None or not is_followup_due(row, days):
                log(f"Skipped {row['name'] if row else company_id}: follow-up not due.")
                continue
            first = conn.execute(
                "SELECT * FROM emails WHERE company_id = ? AND kind = 'initial' AND to_addr = ? ORDER BY sent_at LIMIT 1",
                (company_id, row["email"]),
            ).fetchone()
            body = mailer.render(settings["followup_body_template"], mailer.context_for(conn, row))
        if sent:
            time.sleep(random.uniform(*PAUSE_BETWEEN_SENDS))
        subject = followup_subject(first["subject"])
        result = _send(
            settings, my_addr, first["to_addr"], subject, body,
            thread_id=first["thread_id"], in_reply_to=first["message_id_header"],
        )
        with db.session() as conn:
            _record(conn, company_id, "followup", first["to_addr"], subject, body, result)
        sent += 1
        log(f"↩️ Follow-up sent to {row['name']} <{first['to_addr']}>")
        progress(i + 1, len(company_ids))
    return sent


# ---------------------------------------------------------------- replies


def _search_query(to_addrs: List[str], website: Optional[str], after: datetime) -> Optional[str]:
    senders = set()
    for addr in to_addrs:
        domain = addr.split("@", 1)[-1]
        senders.add(addr if domain in GENERIC_MAIL_DOMAINS else domain)
    if website:
        senders.add(registrable_domain(urlsplit(website).hostname))
    senders.discard("")
    if not senders:
        return None
    return f"from:({' OR '.join(sorted(senders))}) after:{int(after.timestamp())}"


def check_company_replies(conn: sqlite3.Connection, row: sqlite3.Row, my_addr: str) -> Optional[str]:
    """Look in Gmail for answers to this company's emails. Returns 'reply', 'bounce', 'auto' or None."""
    sent = conn.execute("SELECT * FROM emails WHERE company_id = ? ORDER BY sent_at", (row["id"],)).fetchall()
    if not sent:
        return None
    messages = []
    for thread_id in {e["thread_id"] for e in sent if e["thread_id"]}:
        messages.extend(gmail.thread_messages(thread_id))
    query = _search_query([e["to_addr"] for e in sent], row["website"], db.parse_time(sent[0]["sent_at"]))
    if query and not any(gmail.classify(m, my_addr) == "reply" for m in messages):
        messages.extend(gmail.search_messages(query))

    result = None
    for message in sorted(messages, key=lambda m: int(m.get("internalDate", 0))):
        kind = gmail.classify(message, my_addr)
        when = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, tz=timezone.utc).strftime(db.TIME_FMT)
        if kind == "reply":
            conn.execute(
                "UPDATE companies SET replied_at = ?, reply_from = ?, reply_snippet = ?, updated_at = ? WHERE id = ?",
                (when, gmail.sender_of(message), (message.get("snippet") or "")[:300], db.now(), row["id"]),
            )
            return "reply"
        if kind == "bounce" and not row["bounced_at"]:
            conn.execute("UPDATE companies SET bounced_at = ?, updated_at = ? WHERE id = ?", (when, db.now(), row["id"]))
            result = "bounce"
        elif kind == "auto" and not row["auto_reply_at"]:
            conn.execute("UPDATE companies SET auto_reply_at = ?, updated_at = ? WHERE id = ?", (when, db.now(), row["id"]))
            result = result or "auto"
    return result


def sync(log: Log) -> dict:
    """Check Gmail for replies and the tracker for opens."""
    counts = {"replies": 0, "bounces": 0, "auto": 0, "opens": 0}
    if gmail.connected():
        my_addr = gmail.my_address()
        with db.session() as conn:
            waiting = [
                r for r in companies(conn, "WHERE c.replied_at IS NULL AND c.bounced_at IS NULL AND c.excluded = 0")
                if r["sent_at"]
            ]
        for row in waiting:
            with db.session() as conn:
                result = check_company_replies(conn, row, my_addr)
            if result == "reply":
                counts["replies"] += 1
                log(f"💬 Reply from {row['name']}")
            elif result == "bounce":
                counts["bounces"] += 1
                log(f"⚠️ Email to {row['name']} bounced (address does not exist)")
            elif result == "auto":
                counts["auto"] += 1
                log(f"🤖 Automatic answer from {row['name']} (not counted as a reply)")
    else:
        log("Gmail not connected: skipped reply check.")
    if tracking.configured():
        try:
            counts["opens"] = tracking.sync_opens()
        except tracking.TrackerError as exc:
            log(f"⚠️ Open tracking: {exc}")
    else:
        log("Open tracking not set up: skipped.")
    with db.session() as conn:
        db.set_setting(conn, "last_sync", db.now())
    return counts
