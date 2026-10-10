"""For each company: find its website, then the best HR email address, and save both."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

from . import db, jobsearch
from .contacts import Candidate, crawl_site, emails_in_text, merge, registrable_domain, score_email
from .fetch import Fetcher
from .websites import WebSearch, find_website, is_company_site, urls_in_text

Log = Callable[[str], None]
WORKERS = 4
MIN_SCORE = 25  # below this an address is too unlikely to reach anyone useful


def listing_hints(company_id: int, conn: sqlite3.Connection) -> tuple:
    """Website URLs and email addresses written into the company's job listings."""
    urls: List[str] = []
    emails: List[str] = []
    rows = conn.execute(
        "SELECT refnr, external_url FROM listings WHERE company_id = ? ORDER BY published DESC LIMIT 2",
        (company_id,),
    ).fetchall()
    for row in rows:
        if row["external_url"] and is_company_site(row["external_url"]):
            urls.append(row["external_url"])
        details = jobsearch.job_details(row["refnr"])
        if not details:
            continue
        text = details.get("stellenangebotsBeschreibung") or ""
        emails.extend(sorted(emails_in_text(text)))
        for key in ("arbeitgeberdarstellungUrl", "allianzpartnerUrl", "externeURL", "externeUrl"):
            if details.get(key) and is_company_site(details[key]):
                urls.append(details[key])
        urls.extend(u for u in urls_in_text(text) if is_company_site(u))
    for email in emails:
        domain = email.split("@", 1)[1]
        if is_company_site(domain):
            urls.append("https://www." + registrable_domain(domain) + "/")
    return urls, emails


def lookup_company(company: sqlite3.Row, fetcher: Fetcher, search: Optional[WebSearch]) -> dict:
    with db.session() as conn:
        hint_urls, listing_emails = listing_hints(company["id"], conn)

    website, source = company["website"], company["website_source"]
    if not website:
        found = find_website(fetcher, search, company["name"], company["city"], company["plz"], hint_urls)
        if found:
            website, source = found

    candidates: List[Candidate] = []
    site_domain = None
    if website:
        crawled, site_domain = crawl_site(fetcher, website)
        candidates.extend(crawled)
    for email in listing_emails:
        score = score_email(email, site_domain, from_listing=True)
        if score is not None:
            candidates.append(Candidate(email, score, "job listing"))
    candidates = [c for c in merge(candidates) if c.score >= MIN_SCORE]

    if candidates:
        state, note = "found", f"{len(candidates)} address(es) found"
    elif website:
        state, note = "no_email", "Website found, but no usable email address on it"
    else:
        state, note = "no_website", "Could not find the company's website"
    return {"website": website, "website_source": source, "candidates": candidates, "state": state, "note": note}


def save_result(company_id: int, result: dict) -> None:
    with db.session() as conn:
        row = conn.execute("SELECT email, email_source FROM companies WHERE id = ?", (company_id,)).fetchone()
        conn.execute("DELETE FROM contact_candidates WHERE company_id = ?", (company_id,))
        for c in result["candidates"]:
            conn.execute(
                "INSERT INTO contact_candidates(company_id, email, score, source_url) VALUES (?, ?, ?, ?)",
                (company_id, c.email, c.score, c.source_url),
            )
        email, email_source = row["email"], row["email_source"]
        if email_source != "manual":
            email = result["candidates"][0].email if result["candidates"] else None
            email_source = "auto" if email else None
        conn.execute(
            "UPDATE companies SET website = ?, website_source = ?, email = ?, email_source = ?, "
            "lookup_state = ?, lookup_note = ?, looked_up_at = ?, updated_at = ? WHERE id = ?",
            (
                result["website"],
                result["website_source"],
                email,
                email_source,
                "found" if email else result["state"],
                result["note"],
                db.now(),
                db.now(),
                company_id,
            ),
        )


def run_lookup(company_ids: List[int], log: Log, progress: Callable[[int, int], None], use_search: bool = True) -> dict:
    with db.session() as conn:
        marks = ",".join("?" * len(company_ids))
        companies = conn.execute(f"SELECT * FROM companies WHERE id IN ({marks})", company_ids).fetchall()
    fetcher = Fetcher()
    search = WebSearch(log) if use_search else None
    counts = {"found": 0, "no_email": 0, "no_website": 0, "error": 0}
    done = 0
    progress(0, len(companies))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(lookup_company, c, fetcher, search): c for c in companies}
        for future in as_completed(futures):
            company = futures[future]
            try:
                result = future.result()
                save_result(company["id"], result)
                state = "found" if result["candidates"] else result["state"]
                counts[state] += 1
                if result["candidates"]:
                    log(f"✅ {company['name']}: {result['candidates'][0].email}")
                else:
                    log(f"— {company['name']}: {result['note']}")
            except Exception as exc:  # one broken website must not stop the whole run
                counts["error"] += 1
                with db.session() as conn:
                    conn.execute(
                        "UPDATE companies SET lookup_state = 'error', lookup_note = ?, looked_up_at = ? WHERE id = ?",
                        (f"Error: {exc}"[:300], db.now(), company["id"]),
                    )
                log(f"⚠️ {company['name']}: {exc}")
            done += 1
            progress(done, len(companies))
    return counts
