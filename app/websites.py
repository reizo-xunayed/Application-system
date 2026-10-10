"""Find a company's own website, starting from nothing but its name and address."""
from __future__ import annotations

import base64
import random
import re
import socket
import threading
import time
from typing import Callable, List, Optional, Sequence
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from . import names
from .contacts import GENERIC_MAIL_DOMAINS, parse, registrable_domain, visible_text
from .fetch import HEADERS, Fetcher, Page

# Job boards, directories and social networks: never the company's own site.
NOT_COMPANY_SITES = {
    "arbeitsagentur.de", "gute-jobs.de", "azubi.de", "azubiyo.de", "ausbildung.de", "aubi-plus.de",
    "stepstone.de", "indeed.com", "indeed.de", "linkedin.com", "xing.com", "kununu.com", "glassdoor.de",
    "glassdoor.com", "monster.de", "jobware.de", "stellenanzeigen.de", "meinestadt.de", "kimeta.de",
    "jobs.de", "yourfirm.de", "joblift.de", "jobvector.de", "absolventa.de", "lehrstellen-radar.de",
    "ihk.de", "hwk.de", "northdata.de", "northdata.com", "firmenwissen.de", "unternehmensregister.de",
    "bundesanzeiger.de", "handelsregister.de", "online-handelsregister.de", "creditreform.de",
    "companyhouse.de", "implisense.com", "moneyhouse.de", "dnb.com", "kompass.com", "wlw.de",
    "gelbeseiten.de", "dasoertliche.de", "dastelefonbuch.de", "11880.com", "cylex.de", "branchenbuch.de",
    "facebook.com", "instagram.com", "youtube.com", "twitter.com", "x.com", "tiktok.com",
    "wikipedia.org", "google.com", "google.de", "bing.com", "duckduckgo.com", "maps.apple.com",
    "personio.de", "softgarden.io", "softgarden.de", "dvinci.de", "join.com", "onlyfy.jobs",
    "jobs.personio.de", "recruitee.com", "smartrecruiters.com", "workday.com", "myworkdayjobs.com",
    "successfactors.com", "successfactors.eu", "jobteaser.com", "talent.com", "jooble.org",
    "stellenonline.de", "jobbörse.de", "azubiwelt.de", "berufsstart.de", "ausbildungsmarkt.de",
    "bibb.de", "berufe.tv", "planet-beruf.de", "interamt.de", "bund.de", "berufenet.de",
}

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]{}]+", re.I)

Log = Callable[[str], None]


def is_company_site(url_or_host: str) -> bool:
    host = urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host
    domain = registrable_domain(host)
    return bool(domain) and "." in domain and domain not in NOT_COMPANY_SITES and domain not in GENERIC_MAIL_DOMAINS


def homepage_of(url: str) -> str:
    if not url.lower().startswith("http"):
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}/"


_dns_works: Optional[bool] = None


def resolves(host: str) -> bool:
    """Does this domain exist? Avoids slow timeouts on guessed names. True when DNS can't be checked."""
    global _dns_works
    if _dns_works is None:
        try:
            socket.getaddrinfo("www.arbeitsagentur.de", 443)
            _dns_works = True
        except OSError:
            _dns_works = False  # e.g. everything goes through a proxy: let the request decide
    if not _dns_works:
        return True
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def domain_label(url: str) -> str:
    """'https://www.ssi-schaefer.com/de' -> 'ssischaefer'"""
    domain = registrable_domain(urlsplit(url).hostname)
    return re.sub(r"[^a-z0-9]", "", domain.rsplit(".", 1)[0].translate(names.UMLAUTS))


def domain_matches_name(url: str, company_name: str) -> bool:
    label = domain_label(url)
    return any(len(t) >= 4 and t in label for t in names.name_tokens(company_name))


def urls_in_text(text: str) -> List[str]:
    out = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?")
        if url.lower().startswith("www."):
            url = "https://" + url
        out.append(url)
    return out


def guess_domains(company_name: str) -> List[str]:
    """Likely homepage URLs built from the company name, most likely first."""
    tokens = names.name_tokens(company_name)
    if not tokens:
        return []
    stems = ["".join(tokens), "-".join(tokens)]
    if len(tokens) > 2:
        stems += ["".join(tokens[:2]), "-".join(tokens[:2])]
    if len(tokens) > 1 and len(tokens[0]) >= 4:
        stems.append(tokens[0])
    seen, urls = set(), []
    for stem in stems:
        for tld in ("de", "com"):
            domain = f"{stem}.{tld}"
            if domain not in seen and 3 < len(stem) <= 63:
                seen.add(domain)
                urls.append(f"https://www.{domain}/")
    return urls


# ---------------------------------------------------------------- verification


def _folded_words(text: str) -> str:
    return " " + " ".join(t for t in names.fold(text).split() if t not in names.LEGAL_WORDS) + " "


def _impressum_link(page: Page) -> Optional[str]:
    soup = parse(page)
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if "impressum" in label or "imprint" in label:
            return urljoin(page.url, a["href"])
    return None


def page_mentions_company(text: str, company_name: str, plz: Optional[str] = None, require_plz: bool = False) -> bool:
    tokens = names.name_tokens(company_name)
    if not tokens:
        return False
    words = _folded_words(text)
    phrase_found = f" {' '.join(tokens)} " in words
    if require_plz:
        all_tokens = all(f" {t} " in words for t in tokens)
        return bool(plz) and plz in text and (phrase_found or all_tokens)
    return phrase_found


def verify_site(fetcher: Fetcher, url: str, company_name: str, plz: Optional[str]) -> Optional[str]:
    """Return the homepage URL if this site belongs to the company, judged by its Impressum."""
    start = homepage_of(url)
    home = fetcher.get(start)
    if home is None and "://www." in start:
        home = fetcher.get(start.replace("://www.", "://", 1))
    if not home or home.status >= 400 or not is_company_site(home.url):
        return None
    impressum_url = _impressum_link(home) or urljoin(home.url, "/impressum")
    impressum = fetcher.get(impressum_url)
    if impressum and impressum.status < 400 and page_mentions_company(
        visible_text(parse(impressum)), company_name
    ):
        return homepage_of(home.url)
    home_text = visible_text(parse(home))
    if page_mentions_company(home_text, company_name, plz, require_plz=True):
        return homepage_of(home.url)
    # Brand domains whose Impressum names a different legal entity, e.g. ssi-schaefer.com:
    # accept when the domain is exactly the company name and the homepage names it too.
    tokens = names.name_tokens(company_name)
    if tokens and domain_label(home.url) == "".join(tokens):
        words = " " + names.fold(home_text) + " "
        if all(f" {t} " in words for t in tokens):
            return homepage_of(home.url)
    return None


# ---------------------------------------------------------------- web search


class WebSearch:
    """Free search pages (DuckDuckGo, then Bing). Slow on purpose; turns itself off when blocked."""

    PAUSE = 5.0

    def __init__(self, log: Log):
        self.log = log
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.lock = threading.Lock()
        self.last = 0.0
        self.blocked = set()

    def _pace(self) -> None:
        wait = self.last + self.PAUSE + random.uniform(0, 2) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()

    def search(self, query: str) -> List[str]:
        with self.lock:
            for name, engine in (("DuckDuckGo", self._duckduckgo), ("Bing", self._bing)):
                if name in self.blocked:
                    continue
                self._pace()
                try:
                    urls = engine(query)
                except requests.RequestException:
                    urls = None
                if urls is None:
                    self.blocked.add(name)
                    self.log(f"⚠️ {name} stopped answering searches; skipping it for this run.")
                    continue
                if urls:
                    return urls
            return []

    def _duckduckgo(self, query: str) -> Optional[List[str]]:
        resp = self.session.get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=de-de", timeout=15)
        if resp.status_code != 200 or "anomaly" in resp.text[:20000].lower():
            return None
        return parse_duckduckgo(resp.text)

    def _bing(self, query: str) -> Optional[List[str]]:
        resp = self.session.get(f"https://www.bing.com/search?q={quote_plus(query)}&setlang=de&cc=de", timeout=15)
        if resp.status_code != 200 or "captcha" in resp.text[:20000].lower():
            return None
        return parse_bing(resp.text)


def parse_duckduckgo(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.select("a.result__a"):
        if a.find_parent(class_="result--ad"):
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlsplit(href).query).get("uddg", [""])[0])
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http") and "duckduckgo.com" not in href:
            urls.append(href)
    return urls


def _bing_target(href: str) -> str:
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("bing.com") and parts.path.startswith("/ck/"):
        encoded = parse_qs(parts.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
            try:
                return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", "replace")
            except (ValueError, UnicodeError):
                return ""
    return href


def parse_bing(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.select("li.b_algo h2 a[href]"):
        href = _bing_target(a["href"])
        if href.startswith("http"):
            urls.append(href)
    return urls


def search_candidates(search: WebSearch, company_name: str, city: Optional[str]) -> List[str]:
    query = f'"{company_name}" {city or ""} Impressum'.strip()
    seen, out = set(), []
    for url in search.search(query):
        home = homepage_of(url)
        # Search pages sometimes answer scripts with unrelated junk; the domain must look like the name.
        if is_company_site(home) and domain_matches_name(home, company_name) and home not in seen:
            seen.add(home)
            out.append(home)
    return out[:5]


def find_website(
    fetcher: Fetcher,
    search: Optional[WebSearch],
    company_name: str,
    city: Optional[str],
    plz: Optional[str],
    hints: Sequence[str] = (),
) -> Optional[tuple]:
    """Try hints from the job listing, then name-based guesses, then web search.

    Returns (homepage_url, source) or None.
    """
    tried = set()

    def attempt(urls: Sequence[str], source: str) -> Optional[tuple]:
        for url in urls:
            home = homepage_of(url)
            domain = registrable_domain(urlsplit(home).hostname)
            if domain in tried or not is_company_site(home):
                continue
            tried.add(domain)
            host = urlsplit(home).hostname or ""
            if not (resolves(host) or resolves(domain)):
                continue
            verified = verify_site(fetcher, home, company_name, plz)
            if verified:
                return verified, source
        return None

    return (
        attempt(hints, "job listing")
        or attempt(guess_domains(company_name), "guessed from name")
        or (attempt(search_candidates(search, company_name, city), "web search") if search else None)
    )
