# Gill Studio — website + booking backend

One FastAPI app serves **the website, the API and the admin dashboard from the same address**, so there is no CORS and no "API URL" to configure — the form calls `/api/inquiries` on whatever address you deploy to.

```
app/            FastAPI code (routes_public, routes_admin, models, availability, emailer, security)
static/         index.html (your site, form wired to the API) · admin/ (dashboard)
tests/          24 automated tests
backup/         gill-studio.original.html  ← your untouched site before this work
```

## 1. What changed in your site (and what didn't)
Backup: `backup/gill-studio.original.html`. Design, logo, sections, pricing, animations and form steps are **untouched**. Only the form script changed:
1. `CONFIG.endpoint` is now `/api/inquiries` (same-origin) and `CONFIG.availabilityUrl` is `/api/availability`. The old empty endpoint is gone.
2. Time slots now come from the server (booked slots show "taken"); the static slot list is only a fallback if no availability URL is set.
3. The form sends a per-visit idempotency key, consent flag, start time and honeypot; it shows server validation errors on the right step, a "slot just taken" message on conflict, and the real server reference on success.
4. The "Not sent" WhatsApp fallback stays for network/server failures.

## 2. Run locally (Windows/Mac/Linux)
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                      # fill SECRET_KEY, run python -m app.hashpw for ADMIN_PASSWORD_HASH
set -a; . ./.env; set +a                                  # or use your IDE's env loader
uvicorn app.main:app --reload                             # http://127.0.0.1:8000  ·  admin: /admin
pytest -q                                                 # run the tests
```
**Database setup:** automatic. Tables (inquiries, appointments, notes, settings) are created on first start. Locally it uses a SQLite file; in production it uses your Neon Postgres via `DATABASE_URL`.

## 3. Zero-budget deployment (no card, no domain)
| Piece | Service | Notes |
|---|---|---|
| App | **Render** free Web Service | free plan needs no card per Render's docs; you get `https://<name>.onrender.com` |
| Database | **Neon** free Postgres | no card; 0.5 GB; auto-suspends after 5 min idle, wakes in ~hundreds of ms |
| Email | **Resend** free | HTTPS API (Render's free plan blocks SMTP ports 25/465/587) |
| Code host | **GitHub** free | Render deploys from a repo |

Don't use Render's own free Postgres (expires) or SQLite on Render (wiped on every restart/spin-down).

1. **GitHub:** create a repo, upload this folder (`.env` is git-ignored — never upload secrets).
2. **Neon:** sign up → create project → copy the connection string (`postgresql://…?sslmode=require`).
3. **Resend:** sign up with **the email where you want inquiries delivered** → API Keys → create key. Without a verified domain, Resend only delivers to that sign-up address (sender `onboarding@resend.dev`). Set `NOTIFY_EMAIL` to exactly that address.
4. **Admin password hash:** `python -m app.hashpw` → copy the `ADMIN_PASSWORD_HASH=…` line.
5. **Render:** New → Web Service → connect the repo. Runtime Python; Build `pip install -r requirements.txt`; Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`; Instance type **Free**; Health check path `/api/health`. Environment: `PYTHON_VERSION=3.12.3`, `APP_ENV=production`, `SECRET_KEY` (random 64 hex), `DATABASE_URL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, `NOTIFY_EMAIL`. (In production the app refuses to start if `SECRET_KEY`/`ADMIN_PASSWORD_HASH` are missing.)
6. Open `https://<name>.onrender.com`, then run the checklist in Section 5. Put that link in your Instagram bio.

### Honest limitations of the free setup
- **Cold starts:** Render free services sleep after ~15 idle minutes; the next visitor waits up to about a minute. A free uptime pinger on `/api/health` avoids this, but uses instance hours (750/month per workspace — only viable if this is your only free service; check the pinger's current free terms).
- **Email goes only to you.** Clients do **not** get an automatic confirmation email (needs a verified domain, which needs a purchased domain). Instead, the admin page gives a prefilled WhatsApp message you send yourself. Free Resend also has daily/monthly sending caps.
- **Backups:** Neon free has no scheduled backups and a short restore window. Use **Export CSV** in the admin regularly.
- **Rate limiting is in memory** (fine for one instance; resets on restart). The "max pending requests per slot" cap is a soft limit; **confirmed bookings are hard-enforced by a database unique index.**
- Free-tier terms change; re-check each provider's current limits when you sign up.

## 4. How the rules work
- **Appointments** are always "requested — pending confirmation". Only the admin **Confirm** / **Reschedule & confirm** actions mark one confirmed, and only after the server validated the slot against the rules and the database committed it. Two confirmed bookings in one slot are impossible (partial unique index on `date,time WHERE status='confirmed'`).
- **Availability** (days, slots, notice, window, blocked dates, pending cap) is edited in the admin → *Availability*; the public form uses it immediately. Times are in `BUSINESS_TZ`.
- **Duplicates:** per-visit idempotency key + same-email/same-purpose within 10 minutes both return the original reference.
- **Spam:** honeypot field, minimum fill time, 5 inquiries/hour/IP, request size cap, strict server-side validation.
- **Admin security:** scrypt password hash, signed HttpOnly SameSite=Strict (Secure in production) cookie, CSRF header on every change, 5 failed logins/15 min/IP, all `/admin/api/*` need a session, dashboard renders client text with `textContent` (no HTML injection), CSV export guards against spreadsheet formulas, `noindex` on admin/API.
- **Email status** is recorded per inquiry (`sent` / `failed` / `not_configured`) and shown in the dashboard with a "send again" button. The client's success screen never claims an email was sent.

## 5. Manual testing checklist (do this on your deployed URL)
1. Open `/api/health` → `{"ok":true}`; open `/` and each nav link and CTA.
2. Service card and package buttons preselect the form fields.
3. Submit step 1 empty → errors; bad email/phone → errors; fix and continue.
4. Step 4: pick a date (Sunday / past dates rejected), pick a slot, continue; try skipping the consultation too.
5. Review step: submit without consent → blocked; with consent → success screen with a `GS-…` reference.
6. Check your Resend inbox: subject *New Gill Studio Project Inquiry — <name>* with every answer. Reply-To is the client.
7. Double-click Send quickly / refresh and resubmit → still one inquiry.
8. `/admin`: wrong password → error; 6 wrong tries → locked for 15 min; correct login works.
9. Open the inquiry: change status, add a note, Confirm the appointment; reload the public form on that date → the slot shows "taken".
10. Submit another inquiry for the same slot → "just taken" message.
11. Reschedule, Decline, edit Availability (block a date), Export CSV, Sign out → `/admin/api/inquiries` now returns 401.
12. On a phone: 320–430 px widths, no sideways scroll, whole booking flow works.
13. In Render logs: no errors; in Resend dashboard: delivery status of the test mail.

## 6. Test results (automated, run in the build sandbox)
- 24 pytest tests pass on SQLite **and** on PostgreSQL 16: submission, persistence, validation errors, honeypot/too-fast/rate limit, duplicates, email request contents (against a **mock** Resend server), email failure/not-configured states, unauthorized admin access, CSRF, login lockout, status/notes, confirm/reschedule/decline, double-booking (app check + raw DB constraint), availability editing, CSV guard.
- A live uvicorn run drove the real form page (jsdom) through all five steps to a server reference, confirmed the booking as admin, and verified the slot then showed as taken in a fresh form.
- **Not tested (needs your accounts/deployment):** real email delivery via Resend, Neon specifically, Render deployment, real phones/browsers (layout and touch), Google Fonts loading, and behaviour under real traffic.
