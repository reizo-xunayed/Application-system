"""Gmail through "Sign in with Google" (OAuth). The app stores a revocable token, never a password."""
from __future__ import annotations

import json
import os
import re
import secrets
from email.utils import parseaddr
from typing import List, Optional

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SEND_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send?uploadType=multipart"
CHECK_HEADERS = ["From", "Subject", "Auto-Submitted", "X-Autoreply", "X-Autorespond", "Precedence"]
AUTO_SUBJECT = re.compile(
    r"automatische antwort|abwesen|out of office|autoreply|auto-reply|automatic reply|"
    r"eingangsbest(ä|ae)tigung|empfangsbest(ä|ae)tigung",
    re.I,
)


class GmailError(RuntimeError):
    pass


class NotConnected(GmailError):
    pass


def client_file_present() -> bool:
    return config.GOOGLE_CLIENT_FILE.exists()


def _save(creds: Credentials) -> None:
    config.ensure_data_dir()
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(config.GOOGLE_TOKEN_FILE, 0o600)


def credentials() -> Optional[Credentials]:
    if not config.GOOGLE_TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), SCOPES)
    except (ValueError, json.JSONDecodeError):
        return None
    if creds.valid:
        return creds
    if creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        _save(creds)
        return creds
    return None


def connected() -> bool:
    return credentials() is not None


def sign_in(timeout_seconds: int = 300) -> str:
    """Open Google's sign-in page in the browser and wait for the user to finish. Returns the Gmail address."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_file_present():
        raise GmailError(f"Missing {config.GOOGLE_CLIENT_FILE.name} in the data folder (see README, step 3).")
    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        timeout_seconds=timeout_seconds,
        prompt="consent",
        authorization_prompt_message="",
        success_message="Signed in to Gmail. You can close this tab and go back to the app.",
    )
    if creds is None:
        raise GmailError("Sign-in timed out. Try again.")
    _save(creds)
    return my_address()


def disconnect() -> None:
    creds = credentials()
    if creds is not None:
        try:
            requests.post("https://oauth2.googleapis.com/revoke", params={"token": creds.token}, timeout=10)
        except requests.RequestException:
            pass
    if config.GOOGLE_TOKEN_FILE.exists():
        config.GOOGLE_TOKEN_FILE.unlink()


def _session() -> AuthorizedSession:
    creds = credentials()
    if creds is None:
        raise NotConnected("Gmail is not connected. Open Settings and click “Sign in with Google”.")
    return AuthorizedSession(creds)


def _check(resp: requests.Response) -> dict:
    if resp.status_code >= 400:
        raise GmailError(f"Gmail API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def my_address() -> str:
    return _check(_session().get(f"{API}/profile", timeout=20))["emailAddress"].lower()


def send(raw_message: bytes, thread_id: Optional[str] = None) -> dict:
    """Send a complete MIME message. Returns Gmail's {id, threadId}."""
    boundary = "part_" + secrets.token_hex(12)
    metadata = json.dumps({"threadId": thread_id} if thread_id else {})
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
        f"--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n"
    ).encode() + raw_message + f"\r\n--{boundary}--\r\n".encode()
    resp = _session().post(
        SEND_URL, data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"}, timeout=60
    )
    return _check(resp)


def _headers(message: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def message_id_header(gmail_id: str) -> Optional[str]:
    msg = _check(
        _session().get(f"{API}/messages/{gmail_id}", params={"format": "metadata", "metadataHeaders": "Message-ID"}, timeout=20)
    )
    return _headers(msg).get("message-id")


def thread_messages(thread_id: str) -> List[dict]:
    resp = _session().get(
        f"{API}/threads/{thread_id}", params={"format": "metadata", "metadataHeaders": CHECK_HEADERS}, timeout=20
    )
    if resp.status_code == 404:
        return []
    return _check(resp).get("messages", [])


def search_messages(query: str, limit: int = 5) -> List[dict]:
    session = _session()
    found = _check(session.get(f"{API}/messages", params={"q": query, "maxResults": limit}, timeout=20))
    out = []
    for ref in found.get("messages", []):
        out.append(
            _check(
                session.get(
                    f"{API}/messages/{ref['id']}",
                    params={"format": "metadata", "metadataHeaders": CHECK_HEADERS},
                    timeout=20,
                )
            )
        )
    return out


def classify(message: dict, my_addr: str) -> str:
    """'mine', 'bounce', 'auto' (out-of-office / receipt) or 'reply'."""
    headers = _headers(message)
    name, addr = parseaddr(headers.get("from", ""))
    addr = addr.lower()
    if addr == my_addr.lower() or "SENT" in message.get("labelIds", []):
        return "mine"
    if addr.startswith(("mailer-daemon@", "postmaster@")) or "mail delivery" in name.lower():
        return "bounce"
    auto_submitted = headers.get("auto-submitted", "no").lower()
    if (
        auto_submitted != "no"
        or "x-autoreply" in headers
        or "x-autorespond" in headers
        or headers.get("precedence", "").lower() in ("auto_reply", "bulk", "junk")
        or AUTO_SUBJECT.search(headers.get("subject", ""))
    ):
        return "auto"
    return "reply"


def sender_of(message: dict) -> str:
    return parseaddr(_headers(message).get("from", ""))[1].lower()
