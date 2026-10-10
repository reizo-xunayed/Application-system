from app import contacts
from app.contacts import Candidate, clean_email, crawl_site, decode_cfemail, decode_typo3, emails_in_page, score_email
from app.fetch import Page


def page(html, url="https://www.muster.de/"):
    return Page(url=url, status=200, content=html.encode(), content_type="text/html")


def cf_encode(email, key=0x42):
    return f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in email)


def typo3_encode(text, offset=1):
    return contacts._typo3_shift(text, offset)


def test_cloudflare_email_protection():
    assert decode_cfemail(cf_encode("ausbildung@muster.de")) == "ausbildung@muster.de"


def test_typo3_spam_protection_any_offset():
    for offset in (1, 2, 5, -3):
        token = typo3_encode("mailto:karriere@muster-gmbh.de", offset)
        assert decode_typo3(token) == "karriere@muster-gmbh.de"


def test_clean_email_rejects_noise():
    assert clean_email("mailto:Info@Muster.de?subject=Hallo") == "info@muster.de"
    assert clean_email("logo@2x.png") is None
    assert clean_email("max.mustermann@muster.de") is None
    assert clean_email("someone@example.com") is None


def test_emails_in_page_finds_every_format():
    html = f"""
    <html><body>
      <a href="mailto:bewerbung@muster.de">Bewerbung</a>
      <span class="__cf_email__" data-cfemail="{cf_encode('personal@muster.de')}">[email protected]</span>
      <a href="/cdn-cgi/l/email-protection#{cf_encode('jobs@muster.de')}">mail</a>
      <a href="javascript:linkTo_UnCryptMailto(%27{typo3_encode('mailto:ausbildung@muster.de')}%27);">Ausbildung</a>
      <a href="#" data-mailto-token="{typo3_encode('mailto:azubi@muster.de', 3)}" data-mailto-vector="3">azubi</a>
      <p>Schreiben Sie an info (at) muster [dot] de oder hr [at] muster.de</p>
      <p>Spaced: kontakt @ muster.de</p>
      <img src="logo@2x.png">
      <script type="application/ld+json">{{"email": "zentrale@muster.de"}}</script>
    </body></html>"""
    found = emails_in_page(page(html))
    assert found == {
        "bewerbung@muster.de", "personal@muster.de", "jobs@muster.de", "ausbildung@muster.de",
        "azubi@muster.de", "info@muster.de", "hr@muster.de", "kontakt@muster.de", "zentrale@muster.de",
    }


def test_ranking_prefers_ausbildung_then_hr_then_info():
    site = "muster.de"
    scores = {e: score_email(e, site) for e in [
        "ausbildung@muster.de", "bewerbung@muster.de", "karriere@muster.de", "hr@muster.de",
        "info@muster.de", "christian.meyer@muster.de",
    ]}
    order = sorted(scores, key=lambda e: -scores[e])
    assert order[:5] == ["ausbildung@muster.de", "bewerbung@muster.de", "karriere@muster.de", "hr@muster.de", "info@muster.de"]
    # "hr" must not match inside a name
    assert scores["christian.meyer@muster.de"] < scores["info@muster.de"]


def test_ranking_blocks_and_penalizes():
    assert score_email("datenschutz@muster.de", "muster.de") is None
    assert score_email("noreply@muster.de", "muster.de") is None
    agency = score_email("info@webagentur-xyz.de", "muster.de")
    own = score_email("info@muster.de", "muster.de")
    assert agency < own - 30
    # A named person on the careers page beats a generic info address
    assert score_email("a.schmidt@muster.de", "muster.de", career_page=True) > own


def test_registrable_domain():
    assert contacts.registrable_domain("www.karriere.muster.de") == "muster.de"
    assert contacts.registrable_domain("shop.muster.co.uk") == "muster.co.uk"


def test_crawl_site_follows_career_and_impressum(fake_fetcher):
    pages = {
        "https://www.muster.de/": """<a href="/impressum">Impressum</a> <a href="/karriere">Karriere</a>
            <a href="https://other.com/kontakt">elsewhere</a> <a href="/produkte">Produkte</a>""",
        "https://www.muster.de/impressum": "<p>E-Mail: info@muster.de · Webdesign: hallo@agentur.de</p>",
        "https://www.muster.de/karriere": '<a href="/karriere/ausbildung">Ausbildung</a>',
        "https://www.muster.de/karriere/ausbildung": '<a href="mailto:ausbildung@muster.de">Frau Weber</a>',
    }
    fetcher = fake_fetcher(pages)
    candidates, site = crawl_site(fetcher, "https://www.muster.de/")
    assert site == "muster.de"
    assert candidates[0].email == "ausbildung@muster.de"
    emails = [c.email for c in candidates]
    assert emails.index("info@muster.de") < emails.index("hallo@agentur.de")
    assert "https://other.com/kontakt" not in fetcher.requested
    assert "https://www.muster.de/produkte" not in fetcher.requested


def test_merge_keeps_best_score():
    merged = contacts.merge([Candidate("a@x.de", 40, "1"), Candidate("a@x.de", 90, "2"), Candidate("b@x.de", 50, "3")])
    assert [(c.email, c.score) for c in merged] == [("a@x.de", 90), ("b@x.de", 50)]


def test_random_department_addresses_are_not_used():
    from app.lookup import MIN_SCORE

    assert score_email("verkehrspolitik@deutschebahn.com", "deutschebahn.com") < MIN_SCORE
    assert score_email("info@muster.de", "muster.de") >= MIN_SCORE
    assert score_email("info@gmx.de", "muster.de") >= MIN_SCORE  # small firms often use GMX/T-Online
    assert score_email("a.schmidt@muster.de", "muster.de", career_page=True) >= MIN_SCORE
    assert score_email("a.schmidt@muster.de", "muster.de", from_listing=True) >= MIN_SCORE
