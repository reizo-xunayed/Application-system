"""Ausbildung outreach: find companies, find their HR contact, send your email, track the answers."""
from __future__ import annotations

import secrets

from flask import Flask

from . import config, db


def _secret_key() -> bytes:
    config.ensure_data_dir()
    if not config.SECRET_KEY_FILE.exists():
        config.SECRET_KEY_FILE.write_bytes(secrets.token_bytes(32))
        config.SECRET_KEY_FILE.chmod(0o600)
    return config.SECRET_KEY_FILE.read_bytes()


def create_app() -> Flask:
    db.init()
    app = Flask(__name__)
    app.secret_key = _secret_key()
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

    from .web import bp

    app.register_blueprint(bp)
    return app
