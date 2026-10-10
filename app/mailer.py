"""Turn your template into a finished email for one company."""
from __future__ import annotations

import html
import mimetypes
import re
import sqlite3
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Iterable, Optional

from . import config
from .occupations import OCCUPATIONS

PLACEHOLDERS = ("firma", "beruf", "stadt")
PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}", re.I)
OCCUPATION_ORDER = {o.key: i for i, o in enumerate(OCCUPATIONS)}


def readable_occupation(name: str) -> str:
    """'Fachinformatiker/in - Systemintegration' -> 'Fachinformatiker/in für Systemintegration'."""
    if " - " not in (name or ""):
        return name or ""
    base, branch = name.split(" - ", 1)
    if base.startswith("Fachinformatiker"):
        return f"{base} für {branch}"
    return f"{base} (Fachrichtung {branch})"


def main_listing(conn: sqlite3.Connection, company_id: int) -> Optional[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM listings WHERE company_id = ? ORDER BY published DESC", (company_id,)
    ).fetchall()
    if not rows:
        return None
    return sorted(rows, key=lambda r: OCCUPATION_ORDER.get(r["occupation_key"], 99))[0]


def context_for(conn: sqlite3.Connection, company: sqlite3.Row) -> dict:
    listing = main_listing(conn, company["id"])
    city = (listing["city"] if listing and listing["city"] else company["city"]) or ""
    return {
        "firma": company["name"],
        "beruf": readable_occupation(listing["occupation"]) if listing else "",
        "stadt": city.split(",")[0].strip(),  # "Arnstein, Unterfranken" -> "Arnstein"
    }


def render(template: str, context: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: str(context.get(m.group(1).lower(), m.group(0))), template or "")


def draft_for(conn: sqlite3.Connection, company: sqlite3.Row, settings: dict) -> tuple:
    """(subject, body, customized) for the first email to this company."""
    saved = conn.execute("SELECT subject, body FROM drafts WHERE company_id = ?", (company["id"],)).fetchone()
    if saved:
        return saved["subject"], saved["body"], True
    ctx = context_for(conn, company)
    return render(settings["subject_template"], ctx), render(settings["body_template"], ctx), False


def text_to_html(body: str, pixel_url: Optional[str] = None) -> str:
    paragraphs = re.split(r"\n\s*\n", body.strip())
    parts = ["<p>" + html.escape(p).replace("\n", "<br>\n") + "</p>" for p in paragraphs]
    if pixel_url:
        parts.append(
            f'<img src="{html.escape(pixel_url)}" width="1" height="1" alt="" '
            'style="display:block;width:1px;height:1px;border:0;">'
        )
    return (
        '<!DOCTYPE html><html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;">\n'
        + "\n".join(parts)
        + "\n</body></html>"
    )


def attachment_files() -> list:
    if not config.ATTACHMENTS_DIR.exists():
        return []
    return sorted(p for p in config.ATTACHMENTS_DIR.iterdir() if p.is_file() and not p.name.startswith("."))


def build_message(
    sender_name: str,
    sender_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    pixel_url: Optional[str] = None,
    attachments: Iterable[Path] = (),
    in_reply_to: Optional[str] = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender_addr)) if sender_name else sender_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender_addr.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    msg.add_alternative(text_to_html(body, pixel_url), subtype="html")
    for path in attachments:
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg.as_bytes(policy=SMTP)
