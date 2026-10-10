from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Never touch the real data/ folder from tests.
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="outreach-test-")
os.environ.pop("TRACKER_URL", None)
os.environ.pop("TRACKER_SECRET", None)

from app import config, db  # noqa: E402
from app.fetch import Page  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_FILE", tmp_path / "google_token.json")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_FILE", tmp_path / "google_client_secret.json")
    monkeypatch.setattr(config, "SECRET_KEY_FILE", tmp_path / "secret_key")
    db.init()
    yield


class FakeFetcher:
    """Serves pages from a dict {url: html} instead of the internet."""

    def __init__(self, pages, redirects=None):
        self.pages = pages
        self.redirects = redirects or {}
        self.requested = []

    def get(self, url, html_only=True):
        self.requested.append(url)
        final = self.redirects.get(url, url)
        html = self.pages.get(final)
        if html is None:
            return None
        return Page(url=final, status=200, content=html.encode("utf-8"), content_type="text/html; charset=utf-8")


@pytest.fixture
def fake_fetcher():
    return FakeFetcher


def add_company(conn, name="Muster Maschinenbau GmbH", city="Würzburg", plz="97070", region="BAYERN", **extra):
    from app import names

    now = db.now()
    cols = {
        "name": name,
        "name_key": names.company_key(name),
        "city": city,
        "plz": plz,
        "region": region,
        "priority": 1 if region == "BAYERN" else 2,
        "lookup_state": "pending",
        "created_at": now,
        "updated_at": now,
    }
    cols.update(extra)
    cur = conn.execute(
        f"INSERT INTO companies({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())
    )
    return cur.lastrowid
