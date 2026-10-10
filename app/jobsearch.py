"""Search Ausbildung listings on the Arbeitsagentur Jobbörse and store the companies behind them."""
from __future__ import annotations

import base64
import sqlite3
import time
import urllib.parse
from typing import Callable, Dict, Iterable, Iterator, List, Optional

import requests

from . import db, names
from .occupations import Occupation, Place, match_listing, priority_for_region

API = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
# Public client id used by the Jobbörse website itself.
HEADERS = {"X-API-Key": "jobboerse-jobsuche", "User-Agent": "Mozilla/5.0 (Macintosh) AusbildungOutreach/1.0"}
AUSBILDUNG = 4  # angebotsart: Ausbildung / duales Studium
PAGE_SIZE = 100
MAX_PAGES = 20
PAUSE = 0.4  # seconds between API calls

Log = Callable[[str], None]


class SearchError(RuntimeError):
    pass


def _get(path: str, params: Optional[dict] = None, session: Optional[requests.Session] = None) -> dict:
    http = session or requests
    resp = http.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise SearchError(f"Jobbörse answered HTTP {resp.status_code} for {path}")
    return resp.json()


def iter_listings(term: str, place: Place, radius_km: int, session: Optional[requests.Session] = None) -> Iterator[dict]:
    """Yield every Ausbildung listing for one search word and place, following pages."""
    params = {"was": term, "wo": place.query, "angebotsart": AUSBILDUNG, "size": PAGE_SIZE}
    if not place.whole_state:
        params["umkreis"] = radius_km
    seen = 0
    for page in range(1, MAX_PAGES + 1):
        data = _get("pc/v6/jobs", dict(params, page=page), session)
        results = data.get("ergebnisliste") or []
        if not results:
            return
        yield from results
        seen += len(results)
        if seen >= int(data.get("maxErgebnisse") or 0):
            return
        time.sleep(PAUSE)


def job_details(refnr: str, session: Optional[requests.Session] = None) -> Optional[dict]:
    """Full listing (with description text). Returns None if the listing is gone."""
    encoded = urllib.parse.quote(base64.b64encode(refnr.encode()).decode(), safe="")
    try:
        return _get(f"pc/v4/jobdetails/{encoded}", session=session)
    except (SearchError, requests.RequestException, ValueError):
        return None


def _address(entry: dict) -> dict:
    for loc in entry.get("stellenlokationen") or []:
        if loc.get("adresse"):
            return loc["adresse"]
    return {}


def store_listing(conn: sqlite3.Connection, entry: dict, occupation: Occupation, occupation_name: str) -> Dict[str, bool]:
    """Insert the listing and its company. Returns which of the two were new."""
    firm = (entry.get("firma") or "").strip()
    refnr = entry.get("referenznummer")
    if not firm or not refnr:
        return {"company": False, "listing": False}

    addr = _address(entry)
    key = names.company_key(firm)
    now = db.now()
    row = conn.execute("SELECT id, priority FROM companies WHERE name_key = ?", (key,)).fetchone()
    new_company = row is None
    priority = priority_for_region(addr.get("region"))
    if new_company:
        cur = conn.execute(
            "INSERT INTO companies(name, name_key, street, plz, city, region, priority, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                firm,
                key,
                " ".join(filter(None, [addr.get("strasse"), addr.get("hausnummer")])) or None,
                addr.get("plz"),
                addr.get("ort"),
                addr.get("region"),
                priority,
                now,
                now,
            ),
        )
        company_id = cur.lastrowid
    else:
        company_id = row["id"]
        if priority < row["priority"]:
            conn.execute("UPDATE companies SET priority = ?, updated_at = ? WHERE id = ?", (priority, now, company_id))

    cur = conn.execute(
        "INSERT OR IGNORE INTO listings(company_id, refnr, title, occupation, occupation_key, city, plz, "
        "start_date, published, external_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            company_id,
            refnr,
            entry.get("stellenangebotsTitel"),
            occupation_name,
            occupation.key,
            addr.get("ort"),
            addr.get("plz"),
            (entry.get("eintrittszeitraum") or {}).get("von"),
            entry.get("datumErsteVeroeffentlichung"),
            entry.get("externeURL"),
            now,
        ),
    )
    return {"company": new_company, "listing": cur.rowcount == 1}


def import_listings(conn: sqlite3.Connection, entries: Iterable[dict], occupation: Occupation) -> Dict[str, int]:
    counts = {"seen": 0, "matched": 0, "new_companies": 0, "new_listings": 0}
    for entry in entries:
        counts["seen"] += 1
        name = match_listing(
            occupation,
            entry.get("hauptberuf") or "",
            entry.get("alleBerufe") or [],
            entry.get("stellenangebotsTitel") or "",
        )
        if not name:
            continue
        counts["matched"] += 1
        result = store_listing(conn, entry, occupation, name)
        counts["new_companies"] += result["company"]
        counts["new_listings"] += result["listing"]
    return counts


def run_search(occupations: List[Occupation], places: List[Place], radius_km: int, log: Log) -> Dict[str, int]:
    totals = {"seen": 0, "matched": 0, "new_companies": 0, "new_listings": 0}
    session = requests.Session()
    for occupation in occupations:
        for place in places:
            for term in occupation.search_terms:
                where = place.label if place.whole_state else f"{place.label} (+{radius_km} km)"
                try:
                    with db.session() as conn:
                        counts = import_listings(conn, iter_listings(term, place, radius_km, session), occupation)
                except (SearchError, requests.RequestException, ValueError) as exc:
                    log(f"⚠️ {term} in {where}: {exc}")
                    continue
                for k in totals:
                    totals[k] += counts[k]
                log(
                    f"{term} in {where}: {counts['seen']} listings, {counts['matched']} matching, "
                    f"{counts['new_companies']} new companies"
                )
                time.sleep(PAUSE)
    return totals
