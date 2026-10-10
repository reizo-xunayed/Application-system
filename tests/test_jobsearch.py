from app import db, jobsearch, names
from app.occupations import OCCUPATIONS_BY_KEY, match_listing


def listing(refnr, firma, beruf, title="Ausbildung 2027", region="BAYERN", ort="München", plz="80331", **extra):
    entry = {
        "referenznummer": refnr,
        "firma": firma,
        "hauptberuf": beruf,
        "alleBerufe": [beruf],
        "stellenangebotsTitel": title,
        "stellenlokationen": [{"adresse": {"strasse": "Hauptstr.", "hausnummer": "1", "plz": plz, "ort": ort, "region": region}}],
        "eintrittszeitraum": {"von": "2027-09-01"},
        "datumErsteVeroeffentlichung": "2026-09-01",
    }
    entry.update(extra)
    return entry


def test_mechatronik_excludes_car_repair():
    mech = OCCUPATIONS_BY_KEY["mechatronik"]
    assert match_listing(mech, "Mechatroniker/in", [], "Ausbildung Mechatroniker (m/w/d)") == "Mechatroniker/in"
    assert match_listing(mech, "Kraftfahrzeugmechatroniker/in - Personenkraftwagentechnik", [], "Kfz-Mechatroniker") is None
    assert match_listing(mech, "Mechatroniker/in", [], "Mechatroniker im Autohaus Huber") is None
    assert match_listing(mech, "Mechatroniker/in - Kältetechnik", [], "Kältetechnik") is None


def test_other_occupations_match_by_prefix():
    assert match_listing(OCCUPATIONS_BY_KEY["it"], "Fachinformatiker/in - Systemintegration", [], "")
    assert match_listing(OCCUPATIONS_BY_KEY["bau"], "Bautechnische/r Konstrukteur/in - Architektur", [], "")
    assert match_listing(OCCUPATIONS_BY_KEY["bau"], "Bauzeichner/in", [], "")
    assert not match_listing(OCCUPATIONS_BY_KEY["it"], "Kaufmann/-frau - Büromanagement", [], "")
    # Secondary occupation of a listing also counts
    assert match_listing(OCCUPATIONS_BY_KEY["it"], "Elektroniker/in", ["Fachinformatiker/in - Anwendungsentwicklung"], "")


def test_company_key_merges_legal_forms():
    assert names.company_key("Deutsche Bahn AG") == names.company_key("Deutsche Bahn")
    assert names.company_key("Müller GmbH & Co. KG") == "mueller"


def test_import_merges_listings_per_company_and_sets_priority():
    it = OCCUPATIONS_BY_KEY["it"]
    entries = [
        listing("1", "Muster IT GmbH", "Fachinformatiker/in - Systemintegration"),
        listing("2", "Muster IT GmbH", "Fachinformatiker/in - Anwendungsentwicklung"),
        listing("3", "Rhein Software AG", "Fachinformatiker/in - Systemintegration", region="NORDRHEIN_WESTFALEN", ort="Köln"),
        listing("4", "Büro GmbH", "Kaufmann/-frau - Büromanagement"),
        listing("5", "", "Fachinformatiker/in - Systemintegration"),  # anonymous listing
    ]
    with db.session() as conn:
        counts = jobsearch.import_listings(conn, entries, it)
        again = jobsearch.import_listings(conn, entries, it)
        rows = conn.execute("SELECT name, priority, city FROM companies ORDER BY priority, name").fetchall()
        n_listings = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    assert counts == {"seen": 5, "matched": 4, "new_companies": 2, "new_listings": 3}
    assert again["new_companies"] == 0 and again["new_listings"] == 0
    assert [(r["name"], r["priority"], r["city"]) for r in rows] == [("Muster IT GmbH", 1, "München"), ("Rhein Software AG", 2, "Köln")]
    assert n_listings == 3


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        return FakeResponse(self.pages[params["page"] - 1])


def test_iter_listings_follows_pages(monkeypatch):
    monkeypatch.setattr(jobsearch, "PAUSE", 0)
    pages = [{"maxErgebnisse": 3, "ergebnisliste": [{"n": 1}, {"n": 2}]}, {"maxErgebnisse": 3, "ergebnisliste": [{"n": 3}]}]
    session = FakeSession(pages)
    from app.occupations import PLACES_BY_KEY

    got = list(jobsearch.iter_listings("Fachinformatiker", PLACES_BY_KEY["muenchen"], 30, session))
    assert [g["n"] for g in got] == [1, 2, 3]
    assert session.calls[0]["umkreis"] == 30 and session.calls[0]["angebotsart"] == 4
    # Whole-state search sends no radius
    session = FakeSession(pages)
    list(jobsearch.iter_listings("Fachinformatiker", PLACES_BY_KEY["bayern"], 30, session))
    assert "umkreis" not in session.calls[0] and session.calls[0]["wo"] == "Bayern"
