"""Pull email addresses out of web pages and rank them by how likely they reach HR."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .fetch import Fetcher, Page

EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}")
BRACKET_AT = re.compile(r"\s*[\[\(\{]\s*(?:at|ät|@)\s*[\]\)\}]\s*", re.I)
BRACKET_DOT = re.compile(r"\s*[\[\(\{]\s*(?:dot|punkt|\.)\s*[\]\)\}]\s*", re.I)
SPACED_AT = re.compile(r"(?<=\w)\s+@\s*(?=\w)|(?<=\w)\s*@\s+(?=\w)")
TYPO3_CALL = re.compile(r"linkTo_UnCryptMailto\(\s*(?:'|\"|%27|%22)([^'\"]+?)(?:'|\"|%27|%22)")

FILE_ENDINGS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "tif", "tiff", "css", "js", "mp4", "woff", "woff2"}
PLACEHOLDER_DOMAINS = {
    "example.com", "example.de", "example.org", "domain.de", "domain.com", "musterfirma.de",
    "mustermann.de", "beispiel.de", "firma.de", "ihrefirma.de", "sentry.io", "wixpress.com",
}
PLACEHOLDER_LOCALS = {"max.mustermann", "maxmustermann", "vorname.nachname", "name", "ihre.email", "email", "your.name", "you"}

# Local parts that never reach HR.
BLOCKED_LOCAL = (
    "datenschutz", "privacy", "dsb", "dpo", "noreply", "no-reply", "donotreply", "do-not-reply",
    "webmaster", "presse", "press", "newsletter", "rechnung", "invoice", "buchhaltung", "abuse",
    "postmaster", "hostmaster", "billing", "einkauf", "purchas", "marketing", "compliance",
    "hinweisgeber", "whistle", "media", "redaktion", "support", "sales", "vertrieb",
    "order", "bestellung", "reklamation",
)

# (keywords, score). Keywords of 5+ letters match anywhere in the local part,
# shorter ones only as a whole piece (so "hr" does not match "christian").
KEYWORD_SCORES: List[Tuple[Tuple[str, ...], int]] = [
    (("ausbildung", "azubi", "nachwuchs", "auszubild", "lehrstelle", "apprentice", "trainee"), 100),
    (("bewerbung", "bewerben", "application", "apply"), 90),
    (("karriere", "career", "jobs", "job", "stellen", "recruiting", "recruit", "talent"), 80),
    (("personal", "hr", "humanresources", "human", "people", "personalwesen"), 75),
    (("info", "kontakt", "contact", "office", "mail", "post", "zentrale", "service", "hallo", "hello", "empfang"), 40),
]
OTHER_ON_CAREER_PAGE = 65  # usually a named recruiter
# Any other address (e.g. verkehrspolitik@ on a big corporate site) is a department, not HR:
# low enough that lookup.MIN_SCORE drops it.
OTHER = 10

GENERIC_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "gmx.com", "t-online.de",
    "yahoo.de", "yahoo.com", "outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.de",
    "freenet.de", "aol.com", "icloud.com", "me.com", "arcor.de", "posteo.de", "mail.de", "online.de",
}

TWO_LEVEL_SUFFIXES = {"co.uk", "org.uk", "com.au", "co.at", "or.at", "gv.at", "com.tr", "co.jp"}

CAREER_WORDS = ("ausbildung", "azubi", "nachwuchs", "karriere", "career", "jobs", "job", "stellen", "bewerb", "personal")
LINK_SCORES: List[Tuple[Tuple[str, ...], int]] = [
    (("ausbildung", "azubi", "nachwuchs", "schueler", "schüler", "lehrstelle"), 10),
    (("impressum", "imprint", "legal-notice"), 9),
    (("karriere", "career", "jobs", "stellen", "bewerb", "job"), 9),
    (("kontakt", "contact"), 8),
    (("ansprechpartner", "personal", "team"), 5),
    (("ueber-uns", "über-uns", "about", "unternehmen", "company"), 3),
]
FALLBACK_PATHS = ("/impressum", "/kontakt", "/karriere", "/ausbildung")


@dataclass
class Candidate:
    email: str
    score: int
    source_url: str


def registrable_domain(host: Optional[str]) -> str:
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---------------------------------------------------------------- decoding


def decode_cfemail(hexstr: str) -> Optional[str]:
    """Cloudflare 'email protection' hex encoding."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return None


def _typo3_shift(text: str, offset: int) -> str:
    out = []
    for ch in text:
        n = ord(ch)
        for start, end in ((0x2B, 0x3A), (0x40, 0x5A), (0x61, 0x7A)):
            if start <= n <= end:
                n += offset
                if offset > 0 and n > end:
                    n = start + (n - end - 1)
                elif offset < 0 and n < start:
                    n = end - (start - n - 1)
                break
        out.append(chr(n))
    return "".join(out)


def decode_typo3(token: str) -> Optional[str]:
    """TYPO3 spam protection (linkTo_UnCryptMailto / data-mailto-token): a shifted 'mailto:...'."""
    token = unquote(token)
    for offset in range(-10, 11):
        decoded = _typo3_shift(token, offset)
        if decoded.lower().startswith("mailto:"):
            return decoded[7:]
    return None


def deobfuscate(text: str) -> str:
    text = BRACKET_AT.sub("@", text)
    text = BRACKET_DOT.sub(".", text)
    return SPACED_AT.sub("@", text)


def clean_email(raw: str) -> Optional[str]:
    raw = unquote(raw or "").strip().strip(".,;:<>()[]\"'").lower()
    if raw.startswith("mailto:"):
        raw = raw[7:]
    raw = raw.split("?")[0]
    match = EMAIL_RE.fullmatch(raw) or EMAIL_RE.search(raw)
    if not match:
        return None
    email = match.group(0).strip(".")
    local, _, domain = email.partition("@")
    if domain.rsplit(".", 1)[-1] in FILE_ENDINGS or domain in PLACEHOLDER_DOMAINS or local in PLACEHOLDER_LOCALS:
        return None
    if ".." in email:
        return None
    return email


def emails_in_text(text: str) -> Set[str]:
    found = set()
    for match in EMAIL_RE.finditer(deobfuscate(text or "")):
        email = clean_email(match.group(0))
        if email:
            found.add(email)
    return found


def parse(page: Page) -> BeautifulSoup:
    return BeautifulSoup(page.content, "html.parser")


def visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "template"]):
        tag.extract()
    return soup.get_text(" ")


def emails_in_page(page: Page) -> Set[str]:
    soup = parse(page)
    found: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            email = clean_email(href)
        elif "/cdn-cgi/l/email-protection#" in href:
            email = clean_email(decode_cfemail(href.split("#", 1)[1]) or "")
        elif "linkTo_UnCryptMailto" in href:
            m = TYPO3_CALL.search(unquote(href))
            email = clean_email(decode_typo3(m.group(1)) or "") if m else None
        else:
            email = None
        if email:
            found.add(email)
    for tag in soup.find_all(attrs={"data-mailto-token": True}):
        email = clean_email(decode_typo3(tag["data-mailto-token"]) or "")
        if email:
            found.add(email)
    for tag in soup.find_all(attrs={"data-cfemail": True}):
        email = clean_email(decode_cfemail(tag["data-cfemail"]) or "")
        if email:
            found.add(email)
    # JSON-LD and other script data often carry the address too.
    for script in soup.find_all("script", type="application/ld+json"):
        found |= emails_in_text(script.get_text())
    found |= emails_in_text(visible_text(soup))
    return found


# ---------------------------------------------------------------- ranking


def _keyword_hit(local: str, keywords: Iterable[str]) -> bool:
    pieces = set(re.split(r"[._+\-0-9]+", local))
    return any((kw in local) if len(kw) >= 5 else (kw in pieces) for kw in keywords)


def is_career_url(url: str) -> bool:
    path = urlsplit(url).path.lower() + " " + (urlsplit(url).hostname or "")
    return any(word in path for word in CAREER_WORDS)


def score_email(email: str, site_domain: Optional[str], career_page: bool = False, from_listing: bool = False) -> Optional[int]:
    """Higher means more likely to reach the people who decide on Ausbildung places. None = never use."""
    local, _, domain = email.partition("@")
    if any(word in local for word in BLOCKED_LOCAL):
        return None
    score = None
    for keywords, points in KEYWORD_SCORES:
        if _keyword_hit(local, keywords):
            score = points
            break
    if score is None:
        score = OTHER_ON_CAREER_PAGE if career_page or from_listing else OTHER
    if from_listing:
        score += 15
    if site_domain:
        if registrable_domain(domain) == registrable_domain(site_domain):
            score += 10
        elif domain in GENERIC_MAIL_DOMAINS:
            score -= 10
        else:
            score -= 40  # usually the web agency named in the Impressum
    return score


def merge(candidates: Iterable[Candidate]) -> List[Candidate]:
    best: Dict[str, Candidate] = {}
    for c in candidates:
        if c.email not in best or c.score > best[c.email].score:
            best[c.email] = c
    return sorted(best.values(), key=lambda c: (-c.score, c.email))


# ---------------------------------------------------------------- crawling


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _link_score(url: str, text: str) -> int:
    haystack = (urlsplit(url).path + " " + (urlsplit(url).hostname or "") + " " + text).lower()
    return max((points for words, points in LINK_SCORES if any(w in haystack for w in words)), default=0)


def _same_site(url: str, site: str) -> bool:
    return registrable_domain(urlsplit(url).hostname) == site


def scored_links(page: Page, site: str) -> List[Tuple[int, str]]:
    soup = parse(page)
    links: Dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = _normalize_url(urljoin(page.url, href))
        if not url.startswith("http") or not _same_site(url, site):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?)$", urlsplit(url).path, re.I):
            continue
        score = _link_score(url, a.get_text(" ", strip=True))
        if score:
            links[url] = max(score, links.get(url, 0))
    return sorted(((s, u) for u, s in links.items()), key=lambda x: -x[0])


def crawl_site(fetcher: Fetcher, start_url: str, max_pages: int = 8) -> Tuple[List[Candidate], Optional[str]]:
    """Visit the homepage plus the most promising subpages (Ausbildung, Karriere, Impressum, Kontakt).

    Returns ranked candidates and the site's domain (None if the homepage could not be loaded).
    """
    home = fetcher.get(start_url)
    if not home or home.status >= 400:
        return [], None
    site = registrable_domain(urlsplit(home.url).hostname)
    visited = {_normalize_url(home.url)}
    pages = [home]
    queue = scored_links(home, site)
    have = " ".join(u for _, u in queue)
    for path in FALLBACK_PATHS:
        if path.strip("/") not in have:
            queue.append((1, _normalize_url(urljoin(home.url, path))))

    while queue and len(pages) < max_pages:
        queue.sort(key=lambda x: -x[0])
        score, url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        page = fetcher.get(url)
        if not page or page.status >= 400:
            continue
        pages.append(page)
        if is_career_url(page.url):
            # One level deeper on career pages, e.g. /karriere -> /karriere/ausbildung
            for s, u in scored_links(page, site):
                if u not in visited and s >= 9:
                    queue.append((s - 1, u))

    candidates = []
    for page in pages:
        career = is_career_url(page.url)
        for email in emails_in_page(page):
            score = score_email(email, site, career_page=career)
            if score is not None:
                candidates.append(Candidate(email, score, page.url))
    return merge(candidates), site
