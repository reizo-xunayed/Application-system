"""Paths and settings that come from the environment (.env file)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into os.environ (existing values win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_env()

# Everything personal (database, Google login, CV) lives in data/, which git ignores.
DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or ROOT / "data")
DB_PATH = DATA_DIR / "app.db"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
GOOGLE_CLIENT_FILE = DATA_DIR / "google_client_secret.json"
GOOGLE_TOKEN_FILE = DATA_DIR / "google_token.json"
SECRET_KEY_FILE = DATA_DIR / "secret_key"

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT") or 8000)


def tracker_url() -> str:
    return (os.environ.get("TRACKER_URL") or "").strip().rstrip("/")


def tracker_secret() -> str:
    return (os.environ.get("TRACKER_SECRET") or "").strip()


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
