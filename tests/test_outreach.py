from datetime import datetime, timedelta, timezone

import pytest

from app import db, gmail, outreach, tracking
from tests.conftest import add_company


def ago(days=0, seconds=0):
    return (datetime.now(timezone.utc) - timedelta(days=days, seconds=seconds)).strftime(db.TIME_FMT)


def add_sent(conn, cid, to, days=0, kind="initial", thread="t1", token=None):
    conn.execute(
        "INSERT INTO emails(company_id, kind, to_addr, subject, body, sent_at, gmail_id, thread_id, message_id_header, tracking_token) "
        "VALUES (?, ?, ?, 'Subj', 'Body', ?, 'g1', ?, '<m1@x>', ?)",
        (cid, kind, to, ago(days), thread, token),
    )


def row(conn, cid):
    return outreach.company(conn, cid)


def test_stages_follow_the_company_through_the_process():
    with db.session() as conn:
        cid = add_company(conn)
        assert outreach.stage(row(conn, cid)) == "new"
        conn.execute("UPDATE companies SET lookup_state = 'no_website' WHERE id = ?", (cid,))
        assert outreach.stage(row(conn, cid)) == "no_contact"
        conn.execute("UPDATE companies SET email = 'hr@muster.de', lookup_state = 'found' WHERE id = ?", (cid,))
        assert outreach.stage(row(conn, cid)) == "ready"
        add_sent(conn, cid, "hr@muster.de", days=11)
        assert outreach.stage(row(conn, cid)) == "sent"
        assert outreach.is_followup_due(row(conn, cid), 10)
        assert not outreach.is_followup_due(row(conn, cid), 12)
        conn.execute("UPDATE companies SET replied_at = ? WHERE id = ?", (ago(), cid))
        assert outreach.stage(row(conn, cid)) == "replied"
        assert not outreach.is_followup_due(row(conn, cid), 10)
        conn.execute("UPDATE companies SET outcome = 'positive' WHERE id = ?", (cid,))
        assert outreach.stage(row(conn, cid)) == "positive"


def test_choosing_new_address_after_bounce_makes_company_sendable_again():
    with db.session() as conn:
        cid = add_company(conn, email="old@muster.de", lookup_state="found")
        add_sent(conn, cid, "old@muster.de")
        conn.execute("UPDATE companies SET bounced_at = ? WHERE id = ?", (ago(), cid))
        assert outreach.stage(row(conn, cid)) == "bounced"
        conn.execute("UPDATE companies SET email = 'ausbildung@muster.de', bounced_at = NULL WHERE id = ?", (cid,))
        assert outreach.stage(row(conn, cid)) == "ready"


def test_only_one_followup_per_company():
    with db.session() as conn:
        cid = add_company(conn, email="hr@muster.de", lookup_state="found")
        add_sent(conn, cid, "hr@muster.de", days=12)
        add_sent(conn, cid, "hr@muster.de", days=1, kind="followup")
        assert not outreach.is_followup_due(row(conn, cid), 10)


def msg(sender, subject="AW: Anfrage", labels=(), internal=0, **headers):
    hdrs = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    hdrs += [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]
    return {"payload": {"headers": hdrs}, "labelIds": list(labels), "internalDate": str(internal), "snippet": "Danke für Ihre Nachricht"}


def test_classify_messages():
    me = "me@gmail.com"
    assert gmail.classify(msg("Me <me@gmail.com>", labels=["SENT"]), me) == "mine"
    assert gmail.classify(msg("Mail Delivery Subsystem <mailer-daemon@googlemail.com>"), me) == "bounce"
    assert gmail.classify(msg("hr@muster.de", Auto_Submitted="auto-replied"), me) == "auto"
    assert gmail.classify(msg("hr@muster.de", subject="Automatische Antwort: Anfrage"), me) == "auto"
    assert gmail.classify(msg("hr@muster.de", subject="Eingangsbestätigung Ihrer Bewerbung"), me) == "auto"
    assert gmail.classify(msg("Frau Weber <weber@muster.de>"), me) == "reply"


def test_search_query_uses_domains_not_generic_providers():
    q = outreach._search_query(["hr@muster.de", "chef@gmail.com"], "https://www.muster-gruppe.de/", datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert q.startswith("from:(chef@gmail.com OR muster-gruppe.de OR muster.de) after:")


def test_reply_check_ignores_auto_reply_but_records_real_reply(monkeypatch):
    with db.session() as conn:
        cid = add_company(conn, email="hr@muster.de", lookup_state="found")
        add_sent(conn, cid, "hr@muster.de", days=3)
    thread = [
        msg("me@gmail.com", labels=["SENT"], internal=1),
        msg("hr@muster.de", subject="Abwesenheitsnotiz", internal=2),
    ]
    monkeypatch.setattr(gmail, "thread_messages", lambda tid: thread)
    monkeypatch.setattr(gmail, "search_messages", lambda q: [])
    with db.session() as conn:
        assert outreach.check_company_replies(conn, row(conn, cid), "me@gmail.com") == "auto"
        r = row(conn, cid)
        assert r["auto_reply_at"] and not r["replied_at"]
        assert outreach.stage(r) == "sent"

    thread.append(msg("Frau Weber <weber@muster.de>", internal=1_700_000_000_000))
    with db.session() as conn:
        assert outreach.check_company_replies(conn, row(conn, cid), "me@gmail.com") == "reply"
        r = row(conn, cid)
        assert r["reply_from"] == "weber@muster.de" and outreach.stage(r) == "replied"


@pytest.fixture
def fake_gmail(monkeypatch):
    sent = []

    def send(raw, thread_id=None):
        sent.append({"raw": raw, "thread_id": thread_id})
        return {"id": f"g{len(sent)}", "threadId": thread_id or f"t{len(sent)}"}

    monkeypatch.setattr(gmail, "send", send)
    monkeypatch.setattr(gmail, "my_address", lambda: "me@gmail.com")
    monkeypatch.setattr(gmail, "message_id_header", lambda gid: f"<{gid}@mail.gmail.com>")
    monkeypatch.setattr(outreach, "PAUSE_BETWEEN_SENDS", (0, 0))
    return sent


def test_send_first_emails_respects_daily_limit(fake_gmail):
    with db.session() as conn:
        ids = [add_company(conn, name=f"Firma {i} GmbH", email=f"hr@firma{i}.de", lookup_state="found") for i in range(4)]
        db.set_setting(conn, "subject_template", "Anfrage {firma}")
        db.set_setting(conn, "body_template", "Hallo {firma}")
        db.set_setting(conn, "daily_limit", "3")
        add_sent(conn, add_company(conn, name="Old GmbH", email="x@old.de"), "x@old.de")  # 1 already today
    lines = []
    sent = outreach.send_first_emails(ids, lines.append, lambda d, t: None)
    assert sent == 2 and len(fake_gmail) == 2
    assert any("Daily limit" in line for line in lines)
    with db.session() as conn:
        stages = [outreach.stage(row(conn, i)) for i in ids]
        stored = conn.execute("SELECT * FROM emails WHERE company_id = ?", (ids[0],)).fetchone()
    assert stages == ["sent", "sent", "ready", "ready"]
    assert stored["subject"] == "Anfrage Firma 0 GmbH" and stored["message_id_header"] == "<g1@mail.gmail.com>"


def test_send_refuses_without_template(fake_gmail):
    with pytest.raises(ValueError):
        outreach.send_first_emails([1], lambda s: None, lambda d, t: None)


def test_followup_goes_into_same_thread(fake_gmail):
    with db.session() as conn:
        cid = add_company(conn, email="hr@muster.de", lookup_state="found")
        add_sent(conn, cid, "hr@muster.de", days=11, thread="thread-xyz")
        db.set_setting(conn, "followup_body_template", "Kurze Nachfrage an {firma}")
    assert outreach.send_followups([cid], lambda s: None, lambda d, t: None) == 1
    assert fake_gmail[0]["thread_id"] == "thread-xyz"
    assert b"In-Reply-To: <m1@x>" in fake_gmail[0]["raw"]
    assert b"Subject: Re: Subj" in fake_gmail[0]["raw"]
    # Not due any more
    assert outreach.send_followups([cid], lambda s: None, lambda d, t: None) == 0


def test_sync_opens_marks_scanner_hits(monkeypatch):
    monkeypatch.setattr(tracking, "configured", lambda: True)
    with db.session() as conn:
        cid = add_company(conn, email="hr@muster.de")
        add_sent(conn, cid, "hr@muster.de", days=1, token="tok123456789")
        sent_at = db.parse_time(conn.execute("SELECT sent_at FROM emails").fetchone()[0])
    t0 = int(sent_at.timestamp() * 1000)
    events = [
        {"key": "o:1", "token": "tok123456789", "t": t0 + 5_000, "ua": "scanner"},
        {"key": "o:2", "token": "tok123456789", "t": t0 + 3_600_000, "ua": "GoogleImageProxy"},
        {"key": "o:3", "token": "unknown-token", "t": t0, "ua": ""},
    ]
    monkeypatch.setattr(tracking, "fetch_opens", lambda: events)
    assert tracking.sync_opens() == 2
    assert tracking.sync_opens() == 0  # idempotent
    with db.session() as conn:
        r = row(conn, cid)
        assert r["open_count"] == 1 and outreach.stage(r) == "opened"
