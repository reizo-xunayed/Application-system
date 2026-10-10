import email
from email import policy

from app import db, mailer
from tests.conftest import add_company


def test_readable_occupation():
    assert mailer.readable_occupation("Fachinformatiker/in - Systemintegration") == "Fachinformatiker/in für Systemintegration"
    assert mailer.readable_occupation("Bautechnische/r Konstrukteur/in - Architektur") == "Bautechnische/r Konstrukteur/in (Fachrichtung Architektur)"
    assert mailer.readable_occupation("Mechatroniker/in") == "Mechatroniker/in"


def test_render_fills_blanks_and_keeps_unknown_braces():
    out = mailer.render("Hallo {Firma}, {beruf} in {stadt}. {name} bleibt.", {"firma": "A", "beruf": "B", "stadt": "C"})
    assert out == "Hallo A, B in C. {name} bleibt."


def test_draft_uses_listing_and_custom_override():
    with db.session() as conn:
        cid = add_company(conn, name="Muster IT GmbH", city="München")
        conn.execute(
            "INSERT INTO listings(company_id, refnr, title, occupation, occupation_key, city, published, created_at) "
            "VALUES (?, 'r1', 't', 'Fachinformatiker/in - Anwendungsentwicklung', 'it', 'Garching', '2026-09-01', ?)",
            (cid, db.now()),
        )
        settings = dict(db.get_settings(conn), subject_template="Ausbildung als {beruf}", body_template="An {firma} in {stadt}")
        company = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
        assert mailer.draft_for(conn, company, settings) == (
            "Ausbildung als Fachinformatiker/in für Anwendungsentwicklung", "An Muster IT GmbH in Garching", False)
        conn.execute("INSERT INTO drafts(company_id, subject, body) VALUES (?, 'S', 'B')", (cid,))
        assert mailer.draft_for(conn, company, settings) == ("S", "B", True)


def test_build_message_with_pixel_attachment_and_reply(tmp_path):
    cv = tmp_path / "Lebenslauf.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    raw = mailer.build_message(
        "Abdul Test", "me@gmail.com", "ausbildung@muster.de", "Initiativanfrage – Ausbildung",
        "Sehr geehrte Damen und Herren,\n\nich bin <motiviert> & lerne Deutsch.\nViele Grüße",
        pixel_url="https://t.example.workers.dev/p/TOKEN123abc.gif",
        attachments=[cv],
        in_reply_to="<abc@mail.gmail.com>",
    )
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert msg["To"] == "ausbildung@muster.de"
    assert msg["Subject"] == "Initiativanfrage – Ausbildung"
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert "Abdul Test" in msg["From"]
    plain = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "ich bin <motiviert> & lerne Deutsch." in plain
    assert "&lt;motiviert&gt; &amp;" in html and "TOKEN123abc.gif" in html
    assert "TOKEN123abc" not in plain
    files = [p.get_filename() for p in msg.iter_attachments()]
    assert files == ["Lebenslauf.pdf"]


def test_attachment_files_ignores_hidden(tmp_path, monkeypatch):
    from app import config

    folder = tmp_path / "attachments"
    folder.mkdir(exist_ok=True)
    (folder / ".DS_Store").write_text("x")
    (folder / "Zeugnis.pdf").write_text("x")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", folder)
    assert [p.name for p in mailer.attachment_files()] == ["Zeugnis.pdf"]
