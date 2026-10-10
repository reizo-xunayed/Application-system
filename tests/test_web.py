import re

import pytest

from app import create_app, db
from tests.conftest import add_company


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def csrf_of(client, path="/settings"):
    html = client.get(path, base_url="http://localhost:8000").get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_every_page_renders(client):
    with db.session() as conn:
        cid = add_company(conn, email="hr@muster.de", lookup_state="found")
        conn.execute(
            "INSERT INTO listings(company_id, refnr, title, occupation, occupation_key, city, created_at) "
            "VALUES (?, '10000-1', 'Ausbildung Mechatroniker', 'Mechatroniker/in', 'mechatronik', 'Würzburg', ?)",
            (cid, db.now()),
        )
    for path in ["/", "/search", "/companies", f"/companies/{cid}", "/send", "/followups", "/settings",
                 "/companies?stage=ready&occupation=mechatronik&q=muster"]:
        resp = client.get(path, base_url="http://localhost:8000")
        assert resp.status_code == 200, path
    assert "Muster Maschinenbau GmbH" in client.get("/send", base_url="http://localhost:8000").get_data(as_text=True)


def test_foreign_host_is_refused(client):
    assert client.get("/", base_url="http://evil.example").status_code == 403


def test_post_without_csrf_is_refused(client):
    resp = client.post("/settings", data={"subject_template": "x"}, base_url="http://localhost:8000")
    assert resp.status_code == 400


def test_settings_save(client):
    token = csrf_of(client)
    resp = client.post(
        "/settings",
        data={"csrf": token, "sender_name": "A B", "subject_template": "Ausbildung als {beruf}",
              "body_template": "Text", "followup_body_template": "Nachfrage", "daily_limit": "12", "followup_days": "10"},
        base_url="http://localhost:8000",
    )
    assert resp.status_code == 302
    with db.session() as conn:
        s = db.get_settings(conn)
    assert s["subject_template"] == "Ausbildung als {beruf}" and s["daily_limit"] == "12" and s["attach_files"] == "0"


def test_add_company_by_hand_and_choose_email(client):
    token = csrf_of(client)
    resp = client.post("/companies/add", data={"csrf": token, "name": "Handmade GmbH", "website": "handmade.de"},
                       base_url="http://localhost:8000")
    assert resp.status_code == 302
    cid = int(resp.headers["Location"].rstrip("/").split("/")[-1])
    with db.session() as conn:
        c = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
    assert c["website"] == "https://handmade.de/" and c["lookup_state"] == "pending"
    client.post(f"/companies/{cid}/email", data={"csrf": token, "email": "ausbildung@handmade.de"}, base_url="http://localhost:8000")
    with db.session() as conn:
        c = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
    assert c["email"] == "ausbildung@handmade.de" and c["email_source"] == "manual"
