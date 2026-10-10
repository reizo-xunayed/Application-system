"""Dashboard pages."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import List

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from . import config, db, gmail, jobsearch, lookup, mailer, names, outreach, tasks, tracking
from .occupations import OCCUPATIONS, OCCUPATIONS_BY_KEY, PLACES, PLACES_BY_KEY
from .websites import homepage_of

bp = Blueprint("main", __name__)
PAGE_SIZE = 100
AUTO_SYNC_MINUTES = 30


# ---------------------------------------------------------------- safety


@bp.before_app_request
def protect():
    # Only answer requests addressed to this computer (blocks DNS-rebinding tricks)...
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        abort(403)
    # ...and only accept forms that came from these pages (blocks other websites posting here).
    if request.method == "POST":
        token = session.get("csrf")
        if not token or not secrets.compare_digest(token, request.form.get("csrf", "")):
            abort(400, "Form expired. Go back, reload the page and try again.")


@bp.app_context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return {
        "csrf": session["csrf"],
        "active_job": tasks.running(),
        "STAGES": outreach.STAGES,
        "OCCUPATIONS_BY_KEY": OCCUPATIONS_BY_KEY,
    }


@bp.app_template_filter("localtime")
def localtime(value, fmt="%d.%m.%Y %H:%M"):
    parsed = db.parse_time(value)
    return parsed.astimezone().strftime(fmt) if parsed else ""


@bp.app_template_filter("days_ago")
def days_ago(value):
    parsed = db.parse_time(value)
    if not parsed:
        return ""
    days = (datetime.now(timezone.utc) - parsed).days
    return "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")


def ids_from_form() -> List[int]:
    return [int(v) for v in request.form.getlist("ids") if v.isdigit()]


def start_job(kind: str, title: str, fn):
    busy = tasks.running(kind)
    if busy:
        flash(f"“{busy.title}” is still running. Start this again when it has finished.", "error")
        return redirect(url_for("main.job", job_id=busy.id))
    job = tasks.start(kind, title, fn)
    return redirect(url_for("main.job", job_id=job.id))


# ---------------------------------------------------------------- dashboard


@bp.route("/")
def dashboard():
    with db.session() as conn:
        settings = db.get_settings(conn)
        rows = outreach.companies(conn)
        today = outreach.sent_today(conn)
    days = int(settings["followup_days"] or 10)
    counts = {key: 0 for key in outreach.STAGES}
    due = 0
    for row in rows:
        counts[outreach.stage(row)] += 1
        due += outreach.is_followup_due(row, days)
    connected = gmail.connected()

    last_sync = db.parse_time(settings.get("last_sync"))
    stale = last_sync is None or datetime.now(timezone.utc) - last_sync > timedelta(minutes=AUTO_SYNC_MINUTES)
    if connected and stale and counts["sent"] + counts["opened"] and not tasks.running("sync"):
        tasks.start("sync", "Checking replies and opens", lambda job: outreach.sync(job.log))

    setup = {
        "template": bool(settings["subject_template"].strip() and settings["body_template"].strip()),
        "gmail": connected,
        "tracker": tracking.configured(),
        "companies": bool(rows),
    }
    return render_template(
        "dashboard.html",
        counts=counts,
        total=len(rows),
        due=due,
        days=days,
        today=today,
        settings=settings,
        setup=setup,
        jobs=tasks.recent(),
    )


@bp.route("/sync", methods=["POST"])
def sync():
    return start_job("sync", "Checking replies and opens", lambda job: outreach.sync(job.log))


# ---------------------------------------------------------------- search


@bp.route("/search", methods=["GET", "POST"])
def search():
    if request.method == "GET":
        return render_template("search.html", occupations=OCCUPATIONS, places=PLACES)
    occupations = [OCCUPATIONS_BY_KEY[k] for k in request.form.getlist("occupations") if k in OCCUPATIONS_BY_KEY]
    places = [PLACES_BY_KEY[k] for k in request.form.getlist("places") if k in PLACES_BY_KEY]
    radius = max(5, min(200, int(request.form.get("radius") or 30)))
    if not occupations or not places:
        flash("Pick at least one occupation and one place.", "error")
        return redirect(url_for("main.search"))

    def run(job):
        totals = jobsearch.run_search(occupations, places, radius, job.log)
        job.log(f"Done: {totals['matched']} matching listings, {totals['new_companies']} new companies.")
        return totals

    return start_job("search", "Searching Ausbildung listings", run)


# ---------------------------------------------------------------- companies


def _filtered_companies(conn, args):
    rows = outreach.companies(conn, "ORDER BY c.priority, c.name COLLATE NOCASE")
    stage = args.get("stage", "")
    occupation = args.get("occupation", "")
    query = names.fold(args.get("q", ""))
    out = []
    for row in rows:
        if stage and outreach.stage(row) != stage:
            continue
        if occupation and occupation not in (row["occupation_keys"] or "").split(","):
            continue
        if query and query not in names.fold(f"{row['name']} {row['city']} {row['email']}"):
            continue
        out.append(row)
    return out


@bp.route("/companies")
def companies():
    page = max(1, int(request.args.get("page") or 1))
    with db.session() as conn:
        rows = _filtered_companies(conn, request.args)
    shown = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return render_template(
        "companies.html",
        rows=shown,
        total=len(rows),
        page=page,
        pages=max(1, -(-len(rows) // PAGE_SIZE)),
        stage_of=outreach.stage,
        occupations=OCCUPATIONS,
        args=request.args,
    )


@bp.route("/companies/add", methods=["POST"])
def add_company():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Company name is required.", "error")
        return redirect(url_for("main.companies"))
    website = request.form.get("website", "").strip()
    email = request.form.get("email", "").strip().lower()
    now = db.now()
    with db.session() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO companies(name, name_key, city, priority, website, website_source, email, "
            "email_source, lookup_state, created_at, updated_at) VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                names.company_key(name),
                request.form.get("city", "").strip() or None,
                homepage_of(website) if website else None,
                "manual" if website else None,
                email or None,
                "manual" if email else None,
                "found" if email else "pending",
                now,
                now,
            ),
        )
        if not cur.rowcount:
            flash("That company is already in the list.", "error")
            return redirect(url_for("main.companies", q=name))
    flash(f"Added {name}.", "ok")
    return redirect(url_for("main.company", company_id=cur.lastrowid))


@bp.route("/companies/<int:company_id>")
def company(company_id: int):
    with db.session() as conn:
        row = outreach.company(conn, company_id)
        if row is None:
            abort(404)
        settings = db.get_settings(conn)
        listings = conn.execute(
            "SELECT * FROM listings WHERE company_id = ? ORDER BY published DESC", (company_id,)
        ).fetchall()
        candidates = conn.execute(
            "SELECT * FROM contact_candidates WHERE company_id = ? ORDER BY score DESC, email", (company_id,)
        ).fetchall()
        emails = conn.execute("SELECT * FROM emails WHERE company_id = ? ORDER BY sent_at", (company_id,)).fetchall()
        opens = conn.execute(
            "SELECT o.*, e.kind FROM opens o JOIN emails e ON e.id = o.email_id WHERE e.company_id = ? ORDER BY o.opened_at",
            (company_id,),
        ).fetchall()
        subject, body, customized = mailer.draft_for(conn, row, settings)
    return render_template(
        "company.html",
        c=row,
        stage=outreach.stage(row),
        listings=listings,
        candidates=candidates,
        emails=emails,
        opens=opens,
        subject=subject,
        body=body,
        customized=customized,
        readable=mailer.readable_occupation,
    )


@bp.route("/companies/<int:company_id>/update", methods=["POST"])
def update_company(company_id: int):
    form = request.form
    with db.session() as conn:
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        if row is None:
            abort(404)
        website = form.get("website", "").strip()
        email = form.get("email", "").strip().lower()
        website = homepage_of(website) if website else None
        website_source = row["website_source"] if website == row["website"] else ("manual" if website else None)
        email_source = row["email_source"] if email == (row["email"] or "") else ("manual" if email else None)
        bounced_at = row["bounced_at"] if email == (row["email"] or "") else None
        state = row["lookup_state"]
        if email and state != "found":
            state = "found"
        conn.execute(
            "UPDATE companies SET website = ?, website_source = ?, email = ?, email_source = ?, bounced_at = ?, "
            "lookup_state = ?, notes = ?, outcome = ?, excluded = ?, updated_at = ? WHERE id = ?",
            (
                website,
                website_source,
                email or None,
                email_source,
                bounced_at,
                state,
                form.get("notes", "").strip() or None,
                form.get("outcome") if form.get("outcome") in ("positive", "rejected") else None,
                1 if form.get("excluded") else 0,
                db.now(),
                company_id,
            ),
        )
    flash("Saved.", "ok")
    return redirect(url_for("main.company", company_id=company_id))


@bp.route("/companies/<int:company_id>/email", methods=["POST"])
def choose_email(company_id: int):
    email = request.form.get("email", "").strip().lower()
    with db.session() as conn:
        conn.execute(
            "UPDATE companies SET email = ?, email_source = 'manual', bounced_at = NULL, lookup_state = 'found', "
            "updated_at = ? WHERE id = ?",
            (email, db.now(), company_id),
        )
    flash(f"Will send to {email}.", "ok")
    return redirect(url_for("main.company", company_id=company_id))


@bp.route("/companies/<int:company_id>/lookup", methods=["POST"])
def lookup_one(company_id: int):
    def run(job):
        return lookup.run_lookup([company_id], job.log, job.progress)

    return start_job("lookup", "Finding contact", run)


@bp.route("/companies/<int:company_id>/draft", methods=["POST"])
def save_draft(company_id: int):
    with db.session() as conn:
        if request.form.get("reset"):
            conn.execute("DELETE FROM drafts WHERE company_id = ?", (company_id,))
            flash("Back to your standard template.", "ok")
        else:
            conn.execute(
                "INSERT INTO drafts(company_id, subject, body) VALUES (?, ?, ?) "
                "ON CONFLICT(company_id) DO UPDATE SET subject = excluded.subject, body = excluded.body",
                (company_id, request.form.get("subject", "").strip(), request.form.get("body", "").strip()),
            )
            flash("Saved the custom email for this company.", "ok")
    return redirect(url_for("main.company", company_id=company_id) + "#email")


@bp.route("/lookup", methods=["POST"])
def lookup_many():
    limit = max(1, min(500, int(request.form.get("limit") or 30)))
    retry = bool(request.form.get("retry"))
    states = ("pending", "no_website", "no_email", "error") if retry else ("pending",)
    with db.session() as conn:
        marks = ",".join("?" * len(states))
        ids = [
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM companies WHERE excluded = 0 AND email IS NULL AND lookup_state IN ({marks}) "
                "ORDER BY priority, id LIMIT ?",
                (*states, limit),
            )
        ]
    if not ids:
        flash("No companies left to look up. Search for more companies first.", "ok")
        return redirect(url_for("main.companies"))

    def run(job):
        job.log(f"Looking up {len(ids)} companies (Bavaria first)…")
        counts = lookup.run_lookup(ids, job.log, job.progress)
        job.log(
            f"Done: {counts['found']} with email, {counts['no_email']} website without email, "
            f"{counts['no_website']} without website, {counts['error']} errors."
        )
        return counts

    return start_job("lookup", f"Finding contacts for {len(ids)} companies", run)


# ---------------------------------------------------------------- sending


@bp.route("/send", methods=["GET", "POST"])
def send():
    if request.method == "POST":
        ids = ids_from_form()
        if not ids:
            flash("Select at least one company.", "error")
            return redirect(url_for("main.send"))
        return start_job(
            "send", f"Sending {len(ids)} emails", lambda job: {"sent": outreach.send_first_emails(ids, job.log, job.progress)}
        )
    with db.session() as conn:
        settings = db.get_settings(conn)
        ready = [r for r in outreach.companies(conn, "ORDER BY c.priority, c.id") if outreach.stage(r) == "ready"]
        budget = max(0, int(settings["daily_limit"] or 15) - outreach.sent_today(conn))
        previews = []
        for row in ready[:PAGE_SIZE]:
            subject, body, customized = mailer.draft_for(conn, row, settings)
            previews.append({"c": row, "subject": subject, "body": body, "customized": customized})
    return render_template(
        "send.html",
        previews=previews,
        total=len(ready),
        budget=budget,
        settings=settings,
        connected=gmail.connected(),
        tracker=tracking.configured(),
        attachments=mailer.attachment_files() if settings["attach_files"] == "1" else [],
    )


@bp.route("/followups", methods=["GET", "POST"])
def followups():
    if request.method == "POST":
        ids = ids_from_form()
        if not ids:
            flash("Select at least one company.", "error")
            return redirect(url_for("main.followups"))
        return start_job(
            "send", f"Sending {len(ids)} follow-ups", lambda job: {"sent": outreach.send_followups(ids, job.log, job.progress)}
        )
    with db.session() as conn:
        settings = db.get_settings(conn)
        days = int(settings["followup_days"] or 10)
        rows = [r for r in outreach.companies(conn, "ORDER BY c.priority, c.id") if outreach.is_followup_due(r, days)]
        previews = [
            {"c": r, "body": mailer.render(settings["followup_body_template"], mailer.context_for(conn, r))} for r in rows
        ]
        budget = max(0, int(settings["daily_limit"] or 15) - outreach.sent_today(conn))
    return render_template(
        "followups.html", previews=previews, days=days, settings=settings, budget=budget, connected=gmail.connected()
    )


# ---------------------------------------------------------------- settings


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        with db.session() as conn:
            for key in ("sender_name", "subject_template", "body_template", "followup_body_template"):
                db.set_setting(conn, key, request.form.get(key, "").strip())
            db.set_setting(conn, "daily_limit", str(max(1, min(100, int(request.form.get("daily_limit") or 15)))))
            db.set_setting(conn, "followup_days", str(max(1, min(60, int(request.form.get("followup_days") or 10)))))
            db.set_setting(conn, "attach_files", "1" if request.form.get("attach_files") else "0")
        flash("Settings saved.", "ok")
        return redirect(url_for("main.settings"))
    with db.session() as conn:
        values = db.get_settings(conn)
    address = None
    connected = gmail.connected()
    if connected:
        try:
            address = gmail.my_address()
        except gmail.GmailError:
            connected = False
    return render_template(
        "settings.html",
        s=values,
        connected=connected,
        address=address,
        client_present=gmail.client_file_present(),
        tracker_configured=tracking.configured(),
        tracker_url=config.tracker_url(),
        attachments=mailer.attachment_files(),
        attachments_dir=config.ATTACHMENTS_DIR,
        data_dir=config.DATA_DIR,
        placeholders=mailer.PLACEHOLDERS,
    )


@bp.route("/gmail/connect", methods=["POST"])
def gmail_connect():
    def run(job):
        job.log("A Google sign-in page opened in your browser. Finish signing in there (5 minutes max).")
        address = gmail.sign_in()
        job.log(f"Connected as {address}.")
        return {"address": address}

    return start_job("gmail", "Signing in to Gmail", run)


@bp.route("/gmail/disconnect", methods=["POST"])
def gmail_disconnect():
    gmail.disconnect()
    flash("Gmail disconnected.", "ok")
    return redirect(url_for("main.settings"))


@bp.route("/tracker/test", methods=["POST"])
def tracker_test():
    problem = tracking.check()
    flash(problem or "Open tracking works.", "error" if problem else "ok")
    return redirect(url_for("main.settings"))


# ---------------------------------------------------------------- jobs


@bp.route("/jobs/<int:job_id>")
def job(job_id: int):
    found = tasks.get(job_id)
    if found is None:
        flash("That job is gone (the app was restarted).", "error")
        return redirect(url_for("main.dashboard"))
    return render_template("job.html", job=found)
