# Ausbildung Outreach

A small app for your Mac that helps you contact companies directly about an **Ausbildung**, instead of applying through their job portals.

1. **Find companies.** It searches the official Arbeitsagentur Jobbörse for Ausbildung listings in your occupations and cities, with Bavaria first. A company that posts a listing already trains apprentices, so it's a good one to write to.
2. **Find their HR email.** It reads the listing and the company's own website (Impressum, Kontakt, Karriere and Ausbildung pages) and picks the best address, such as `ausbildung@`, `bewerbung@` or `personal@`. You don't type in any addresses.
3. **Send your email.** You write your email once. For each company it fills in the company name, occupation and city. You review the emails and send them in batches from your own Gmail.
4. **Track and follow up.** It sees replies in your Gmail and ignores out-of-office auto-replies. It shows which emails were opened. After 10 days without a reply it reminds you and can send a follow-up in the same email thread.

Everything runs on your computer. Your data (companies, Google login, CV) stays in the `data/` folder, which is never uploaded to GitHub.

Occupations it searches (you can untick any):

| Occupation | Notes |
|---|---|
| Mechatroniker/in | Industrial only. Kfz, Autohaus, Karosserie and similar listings are filtered out |
| Fachinformatiker/in | All specialisations (Anwendungsentwicklung, Systemintegration, …) |
| Technische/r Produktdesigner/in | CAD design for machines and products |
| Bautechnische/r Konstrukteur/in | The new name for Bauzeichner/in (Architektur, Ingenieurbau, Tiefbau) |
| Technische/r Systemplaner/in | Building services and electrical planning |

Places: all of Bavaria, München, Nürnberg, Würzburg, Stuttgart, Frankfurt am Main, Berlin, Düsseldorf and Köln.

---

## 1. Install (once)

You need **Python 3.10 or newer**. Python 3.9 also works but shows warnings. Download it from <https://www.python.org/downloads/macos/> and install it like any Mac app.

Then download this project:

```bash
git clone https://github.com/reizo-xunayed/Application-system.git
cd Application-system
```

(Or on GitHub click **Code → Download ZIP**, unzip it, and open Terminal in that folder.)

## 2. Start the app

```bash
./start.sh
```

You can also double-click `start.command` in Finder. The first start takes about a minute to set things up. Then the dashboard opens at <http://localhost:8000>. Keep the Terminal window open while you use the app, and press `Ctrl+C` to stop it.

## 3. Connect Gmail with "Sign in with Google" (once, about 15 minutes)

The app never sees your Google password. Google gives it a key that only allows sending email and reading your inbox (to spot replies), and you can revoke it at any time. To get that key, you create your own free "Google Cloud project":

1. Open <https://console.cloud.google.com/> and sign in with the Gmail account you want to send from.
2. At the top, click the project picker, then **New project**. Name it `Ausbildung Outreach` and click **Create**. Make sure it's selected.
3. Open <https://console.cloud.google.com/apis/library/gmail.googleapis.com> and click **Enable**.
4. Open **Google Auth Platform** (<https://console.cloud.google.com/auth/overview>) and click **Get started**.
   - App name: `Ausbildung Outreach`. Support email: your Gmail.
   - Audience: **External**.
   - Contact email: your Gmail. Accept the policy, then click **Create**.
5. Go to **Audience**:
   - **Recommended:** click **Publish app**. You stay signed in for good. Google will show an "unverified app" warning during sign-in, which is expected because it's your own app.
   - If you leave it in "Testing" instead, add your Gmail under **Test users**. You'll have to sign in again every 7 days.
6. Go to **Clients → Create client**:
   - Application type: **Desktop app**. Name: `Mac`. Click **Create**.
   - Click **Download JSON**.
7. Rename the downloaded file to `google_client_secret.json` and move it into the `data` folder inside this project. The app creates that folder on first start.
8. In the app, open **Settings** and click **Sign in with Google**. A browser tab opens:
   - Choose your account.
   - Click **Advanced → Go to Ausbildung Outreach (unsafe)**. It's "unsafe" only because Google hasn't reviewed your personal app.
   - Allow both permissions.

That's it. The app remembers the sign-in, so you won't type a password again.

## 4. Open tracking (optional, free, about 10 minutes)

To see whether a company opened your email, every email carries an invisible 1×1 image. When the email is opened, the image loads from a tiny online service. This has to be online because HR's email program can't reach your Mac. The service is in `tracker/worker.js` and runs on Cloudflare's free plan.

**Set it up in the Cloudflare website (nothing to install):**

1. Create a free account at <https://dash.cloudflare.com/sign-up>.
2. **Storage & databases → KV → Create**. Name it `OPENS`.
3. **Workers & Pages → Create → Start with Hello World**. Name it `mail-open-tracker` and click **Deploy**.
4. Click **Edit code**, replace everything with the contents of `tracker/worker.js`, and click **Deploy**.
5. In the Worker's **Bindings** tab, click **Add binding → KV namespace**. Variable name: `OPENS`. Namespace: `OPENS`. Save.
6. In **Settings → Variables and Secrets**, click **Add**:
   - Type: **Secret**. Name: `API_SECRET`.
   - Value: a long random text. To make one, run `openssl rand -hex 24` in Terminal.
   - Click **Deploy**.
7. Copy the Worker's address, for example `https://mail-open-tracker.yourname.workers.dev`.
8. In the project folder, copy `.env.example` to `.env` and fill in:

   ```
   TRACKER_URL=https://mail-open-tracker.yourname.workers.dev
   TRACKER_SECRET=the same long random text as API_SECRET
   ```

9. Restart the app. In **Settings**, click **Test connection**.

(If you have Node.js: `cd tracker`, then `npx wrangler login`, then `npx wrangler kv namespace create OPENS`. Paste the id it prints into `wrangler.toml`. Then run `npx wrangler secret put API_SECRET` and `npx wrangler deploy`.)

**Limits of open tracking:**
- **Approximate:** Apple Mail can report opens that didn't happen. Outlook often blocks images, so some real opens are missed.
- **Your own views count:** looking at a sent email in your own Gmail "Sent" folder counts as an open, so avoid doing that.
- **Scanners:** opens in the first minute after sending are usually mail-security scanners, so the app ignores them.
- **Legal:** under German privacy rules (DSGVO/TDDDG), tracking pixels are a grey area without the recipient's consent. If that worries you, leave tracking off. Replies are tracked either way.

## 5. Daily use

1. **Settings:** write your email once, in German. Use the blanks `{firma}`, `{beruf}` and `{stadt}`, plus a short follow-up text. Set your name.
2. **Find companies:** choose occupations and places, then click **Search**.
3. **Dashboard → Find contacts** for the next 30 companies. It runs in the background at about 15–40 seconds per company, Bavaria first.
4. **Review & send:**
   - Check the emails. Untick any you don't want, or click **Edit for this company** to personalise one.
   - Click **Send selected**. It sends at most 15 per day by default, so your Gmail isn't flagged as spam.
5. **Dashboard:** it checks for replies and opens every 30 minutes while it's open, or click **Check now**.
   - After 10 days without an answer, a reminder appears. **Follow-ups** sends one polite follow-up as a reply in the same thread.
6. **Replies:** when a company replies, answer it in Gmail as usual. Mark the result (Positive or Rejected) on the company's page.

**Attachments:** put your Lebenslauf and certificates (PDF) into `data/attachments/` when they're ready. They're attached to every first email (you can switch this off in Settings).

## How contact finding works, and its limits

The app tries these in order:
1. The email or website written in the Ausbildung listing. About half of all listings have one.
2. Website addresses guessed from the company name, for example `Muster Technik GmbH` → `muster-technik.de`. A guess is only accepted if the site's Impressum names the company.
3. A free web search, as a last resort. Search engines often block or confuse automated searches, so this rarely helps.

On the website it looks at the Ausbildung, Karriere, Impressum and Kontakt pages. It decodes the usual spam protection (`[at]`, Cloudflare, TYPO3), prefers HR and Ausbildung addresses, and ignores `datenschutz@`, `noreply@`, web agencies and random department addresses.

Some companies, especially large groups that only accept applications through their portal, will end up as **No contact found**. They're simply skipped. If you want one anyway, open the company and type its website or email.

## Privacy and safety

- `data/` (database, Google key, attachments) and `.env` are listed in `.gitignore` and never uploaded.
- The dashboard only accepts connections from your own computer, and forms can't be sent from other websites.
- The app reads company websites slowly (one page per second per site) and respects their `robots.txt`.

## For developers

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # app tests (no internet needed)
node --test tests/worker.test.mjs   # tracker tests
```

Layout: `app/jobsearch.py` (Jobbörse API), `app/websites.py` + `app/contacts.py` + `app/lookup.py` (finding HR emails), `app/gmail.py` + `app/mailer.py` + `app/outreach.py` (sending, replies, follow-ups), `app/tracking.py` + `tracker/worker.js` (opens), `app/web.py` + `app/templates/` (dashboard).
