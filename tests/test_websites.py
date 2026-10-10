from urllib.parse import quote

from app import websites
from app.websites import find_website, guess_domains, page_mentions_company, parse_bing, parse_duckduckgo, verify_site


def test_guess_domains_from_name():
    urls = guess_domains("ULM Ingenieurgesellschaft mbH & Co. KG")
    assert urls[0] == "https://www.ulmingenieurgesellschaft.de/"
    assert "https://www.ulm-ingenieurgesellschaft.de/" in urls
    assert all("gmbh" not in u and "-kg" not in u for u in urls)
    assert guess_domains("Müller Präzisionstechnik GmbH")[0] == "https://www.muellerpraezisionstechnik.de/"


def test_page_mentions_company_ignores_legal_words_and_punctuation():
    text = "Angaben gemäß § 5 TMG: ulm-Ingenieurgesellschaft mbH & Co. KG, Marie-Curie-Str. 1, 91052 Erlangen"
    assert page_mentions_company(text, "ULM Ingenieurgesellschaft mbH & Co. KG")
    assert not page_mentions_company("Stadt Ulm – Rathaus", "ULM Ingenieurgesellschaft mbH & Co. KG")
    # Name split by a legal form in between still counts
    assert page_mentions_company("Müller GmbH & Co. KG Maschinenbau", "Müller Maschinenbau GmbH & Co. KG")


def test_verify_site_uses_impressum(fake_fetcher):
    pages = {
        "https://www.muster.de/": '<a href="/rechtliches/impressum">Impressum</a>',
        "https://www.muster.de/rechtliches/impressum": "<p>Muster Maschinenbau GmbH, 97070 Würzburg</p>",
        "https://www.wrong.de/": '<a href="/impressum">Impressum</a>',
        "https://www.wrong.de/impressum": "<p>Ganz Andere GmbH</p>",
    }
    fetcher = fake_fetcher(pages)
    assert verify_site(fetcher, "https://www.muster.de/", "Muster Maschinenbau GmbH", "97070") == "https://www.muster.de/"
    assert verify_site(fetcher, "https://www.wrong.de/", "Muster Maschinenbau GmbH", "97070") is None


def test_verify_site_falls_back_to_bare_domain(fake_fetcher):
    pages = {
        "https://muster.de/": "<p>Muster Maschinenbau GmbH · 97070 Würzburg</p>",
    }
    assert verify_site(fake_fetcher(pages), "https://www.muster.de/", "Muster Maschinenbau GmbH", "97070") == "https://muster.de/"


def test_job_boards_are_never_company_sites():
    assert not websites.is_company_site("https://www.azubi.de/ausbildungsplatz/1")
    assert not websites.is_company_site("https://de.linkedin.com/company/x")
    assert not websites.is_company_site("gmail.com")
    assert websites.is_company_site("https://karriere.muster.de/jobs")


def test_parse_duckduckgo():
    target = "https://www.muster.de/impressum"
    html = f"""
      <div class="result results_links result--ad"><a class="result__a" href="https://ads.example/x">Ad</a></div>
      <div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg={quote(target, safe='')}&rut=abc">Muster</a></div>
      <div class="result"><a class="result__a" href="https://www.northdata.de/Muster">Northdata</a></div>"""
    assert parse_duckduckgo(html) == [target, "https://www.northdata.de/Muster"]


def test_parse_bing_decodes_redirects():
    import base64

    encoded = base64.urlsafe_b64encode(b"https://www.muster.de/").decode().rstrip("=")
    html = f"""<ol><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&&p=x&u=a1{encoded}&ntb=1">Muster</a></h2></li>
               <li class="b_algo"><h2><a href="https://www.other.de/">Other</a></h2></li></ol>"""
    assert parse_bing(html) == ["https://www.muster.de/", "https://www.other.de/"]


class FakeSearch:
    def __init__(self, urls):
        self.urls = urls
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.urls


def test_find_website_order_hints_guess_search(fake_fetcher):
    good = {
        "https://www.muster-technik.de/": "<p>Muster Technik GmbH 97070 Würzburg</p>",
        "https://www.muster-technik.de/impressum": "<p>Muster Technik GmbH</p>",
    }
    # 1. From the job listing
    found = find_website(fake_fetcher(good), FakeSearch([]), "Muster Technik GmbH", "Würzburg", "97070",
                         hints=["https://www.muster-technik.de/karriere"])
    assert found == ("https://www.muster-technik.de/", "job listing")
    # 2. Guessed from the name (no hints)
    search = FakeSearch([])
    assert find_website(fake_fetcher(good), search, "Muster Technik GmbH", "Würzburg", "97070") == (
        "https://www.muster-technik.de/", "guessed from name")
    assert search.queries == []
    # 3. Web search, skipping directories
    other = {
        "https://www.muster-wuerzburg.de/": '<a href="/impressum">Impressum</a>',
        "https://www.muster-wuerzburg.de/impressum": "<p>Muster Technik GmbH, Würzburg</p>",
    }
    search = FakeSearch(["https://www.northdata.de/Muster", "https://www.muster-wuerzburg.de/kontakt"])
    assert find_website(fake_fetcher(other), search, "Muster Technik GmbH", "Würzburg", "97070") == (
        "https://www.muster-wuerzburg.de/", "web search")
    assert search.queries == ['"Muster Technik GmbH" Würzburg Impressum']


def test_homepage_of_drops_paths_and_userinfo():
    assert websites.homepage_of("www.muster.de/karriere?x=1") == "https://www.muster.de/"
    assert websites.homepage_of("https://mailto:hr@muster.de/") == "https://muster.de/"


def test_search_results_must_look_like_the_company():
    assert websites.domain_matches_name("https://www.ssi-schaefer.com/de", "SSI Schäfer")
    assert websites.domain_matches_name("https://herrhammer.de/", "Herrhammer GmbH Spezialmaschinen")
    assert not websites.domain_matches_name("https://www.answers.com/x", "Herrhammer GmbH Spezialmaschinen")


def test_brand_domain_accepted_when_impressum_names_legal_entity(fake_fetcher):
    pages = {
        "https://www.ssi-schaefer.com/": "<h1>SSI SCHÄFER – Intralogistik</h1>",
        "https://www.ssi-schaefer.com/impressum": "<p>Fritz Schäfer GmbH & Co KG, Neunkirchen</p>",
    }
    assert verify_site(fake_fetcher(pages), "https://www.ssi-schaefer.com/", "SSI Schäfer", None) == "https://www.ssi-schaefer.com/"
    # but a random site that merely mentions the name is not accepted
    pages = {"https://www.blog.de/": "<p>Heute über SSI Schäfer</p>"}
    assert verify_site(fake_fetcher(pages), "https://www.blog.de/", "SSI Schäfer", None) is None


def test_guess_skips_generic_first_word():
    urls = guess_domains("Stadt Würzburg")
    assert "https://www.wuerzburg.de/" in urls
    assert "https://www.stadt.de/" not in urls


def test_brand_domain_with_unfindable_impressum(fake_fetcher):
    # warema.de redirects to warema.com/de/, the Impressum isn't linked as "Impressum"
    pages = {"https://www.warema.com/de/": "<footer>© WAREMA Renkhoff SE · Sonnenschutz</footer>"}
    fetcher = fake_fetcher(pages, redirects={"https://www.warema.de/": "https://www.warema.com/de/"})
    assert verify_site(fetcher, "https://www.warema.de/", "Warema Renkhoff SE", "97828") == "https://www.warema.com/"
    # First-word domain without the full name on the page is not enough
    pages = {"https://www.warema.com/de/": "<p>Warema</p>"}
    fetcher = fake_fetcher(pages, redirects={"https://www.warema.de/": "https://www.warema.com/de/"})
    assert verify_site(fetcher, "https://www.warema.de/", "Warema Renkhoff SE", "97828") is None


def test_impressum_found_by_legal_notice_link_or_relative_path(fake_fetcher):
    pages = {
        "https://www.muster.de/en/": '<a href="/en/legal-notice">Legal notice</a>',
        "https://www.muster.de/en/legal-notice": "<p>Muster Antriebstechnik GmbH</p>",
    }
    fetcher = fake_fetcher(pages, redirects={"https://www.muster.de/": "https://www.muster.de/en/"})
    assert verify_site(fetcher, "https://www.muster.de/", "Muster Antriebstechnik GmbH", None) == "https://www.muster.de/"
    pages = {
        "https://www.muster.de/de/": "<p>Willkommen</p>",
        "https://www.muster.de/de/impressum": "<p>Muster Antriebstechnik GmbH</p>",
    }
    fetcher = fake_fetcher(pages, redirects={"https://www.muster.de/": "https://www.muster.de/de/"})
    assert verify_site(fetcher, "https://www.muster.de/", "Muster Antriebstechnik GmbH", None) == "https://www.muster.de/"
