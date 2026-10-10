"""Polite HTTP fetching for company websites: robots.txt, per-host pauses, size limits."""
from __future__ import annotations

import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.6"}
TIMEOUT = 12
MAX_BYTES = 3_000_000
HOST_PAUSE = 1.0  # seconds between two requests to the same host


@dataclass
class Page:
    url: str  # final URL after redirects
    status: int
    content: bytes
    content_type: str


class Fetcher:
    def __init__(self, respect_robots: bool = True):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.respect_robots = respect_robots
        self._robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._last_hit: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait_for_host(self, host: str) -> None:
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            wait = last + HOST_PAUSE - time.monotonic()
            self._last_hit[host] = max(time.monotonic(), last + HOST_PAUSE)
        if wait > 0:
            time.sleep(wait)

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser: Optional[urllib.robotparser.RobotFileParser] = urllib.robotparser.RobotFileParser()
            try:
                self._wait_for_host(parts.netloc)
                resp = self.session.get(base + "/robots.txt", timeout=TIMEOUT)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    parser = None  # no robots.txt: everything allowed
            except requests.RequestException:
                parser = None
            self._robots[base] = parser
        parser = self._robots[base]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def get(self, url: str, html_only: bool = True) -> Optional[Page]:
        """Fetch a page; None on any network problem, robots refusal or non-HTML content."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return None
        try:
            if not self.allowed(url):
                return None
            self._wait_for_host(parts.netloc)
            with self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if html_only and "html" not in ctype.lower():
                    return None
                chunks, size = [], 0
                for chunk in resp.iter_content(65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_BYTES:
                        break
                return Page(url=resp.url, status=resp.status_code, content=b"".join(chunks), content_type=ctype)
        except (requests.RequestException, UnicodeError, ValueError):
            return None
