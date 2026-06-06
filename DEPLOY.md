# HackHunt India — Setup & Deployment Guide

Follow these in order. Each step is independent — the app works without any of them
(it just falls back to local/demo behaviour).

---

## 0. Local run (baseline)
```bash
cd hackhunt/backend
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your keys (all optional)
python app.py               # http://localhost:5050
```

---

## 1. Email deadline reminders  ✉️
Sends each student a reminder for their saved events closing within `REMIND_DAYS`.

**A. Get an SMTP sender (pick one):**
- **Gmail** (easiest for testing): Google Account → Security → 2-Step Verification → **App passwords** → generate one.
  - `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`, `SMTP_PASS=<app password>`
- **Resend** (best for production): sign up at resend.com → API Keys.
  - `SMTP_HOST=smtp.resend.com`, `SMTP_PORT=465`, `SMTP_USER=resend`, `SMTP_PASS=<api key>`, `SMTP_FROM=you@yourdomain`

**B. Put those in `.env`**, then test:
```bash
python reminders.py          # sends now to anyone with due saved events
```

**C. Schedule it daily** (any one):
- **Cron** (Linux/Mac): `0 8 * * * cd /path/hackhunt/backend && python reminders.py`
- **Render Cron Job**: command `python reminders.py` (see §4).
- **HTTP trigger**: `POST /api/run-reminders?token=<REMINDER_TOKEN>` from any scheduler (cron-job.org, GitHub Actions).

---

## 2. Google Sign-In  🔵
1. [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → **Credentials**.
2. **Create Credentials → OAuth client ID → Web application**.
3. Authorized JavaScript origins: add `http://localhost:5050` (and your live URL later).
4. Copy the **Client ID** → paste into `GOOGLE_CLIENT_ID` near the top of `frontend/index.html`.
5. Reload — a real Google button appears. (Google's flow is client-side, so no secret needed.)

---

## 3. GitHub Sign-In  ⚫
1. GitHub → Settings → Developer settings → **OAuth Apps → New OAuth App**.
2. Homepage URL: `http://localhost:5050` · Authorization callback URL: `http://localhost:5050/auth/github/callback`.
3. Copy **Client ID** and generate a **Client secret**.
4. Put them in `.env`: `GITHUB_CLIENT_ID=...`, `GITHUB_CLIENT_SECRET=...`, and set `APP_URL`.
5. Restart — "Continue with GitHub" now works (update the URLs to your live domain when you deploy).

---

## ⭐ Fastest hosting path (do this first)
A `render.yaml` blueprint is included — it sets up the web service **and a 1 GB persistent disk** so your database survives restarts.

1. Add a `.gitignore` (already included) and push the project to a **GitHub** repo. The real `backend/.env` is gitignored — good, don't commit it.
2. Go to **render.com** → **New → Blueprint** → connect your repo. Render reads `render.yaml`.
3. When prompted, fill the secret env vars (these are the `sync:false` ones):
   - `APP_URL` = your Render URL (e.g. `https://hackhunt.onrender.com`)
   - `OWNER_USER`, `OWNER_PASS`, `OWNER_TOKEN` = copy from your `backend/.env`
   - `REMINDER_TOKEN`, and the GitHub/SMTP keys when ready (can be blank at first)
4. Deploy. Your DB + cache live on the mounted disk at `/var/data` (via `HH_DATA_DIR`) — **the database is created automatically on first run**, nothing to set up manually.
5. After it's live, update your **GitHub OAuth callback** to `https://YOUR-URL/auth/github/callback` and add your URL to **Google OAuth origins**.
6. Owner console: visit `https://YOUR-URL/owner.html` and log in with `OWNER_USER`/`OWNER_PASS`.
7. Daily reminder emails: create a free job at **cron-job.org** that POSTs to
   `https://YOUR-URL/api/run-reminders?token=YOUR_REMINDER_TOKEN` once a day.

Railway / Fly.io work the same way — create the service, attach a volume, set `HH_DATA_DIR` to its mount path.

---

## 4. Hosting (manual, any host)  🚀
**Frontend + backend are served together by Flask**, so you deploy one service.

### Option A — Render (recommended, free tier)
1. Push the project to GitHub.
2. Render → **New → Web Service** → connect the repo, root = `hackhunt/backend`.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4` (a `Procfile` is included).
5. Add your env vars (from `.env`) in Render → Environment.
6. **Add a Persistent Disk** (Render → Disks, mount e.g. `/var/data`) and set the DB path there
   so your SQLite data + cache survive restarts (see §5). Without a disk, host filesystems are
   wiped on each deploy.
7. For reminders, add a **Render Cron Job** running `python reminders.py` daily.

Railway and Fly.io work the same way (both support volumes for the SQLite file).

---

## 5. Database: SQLite now → Postgres later
**Today:** the app uses `hackhunt.db` (SQLite). This is genuinely fine for thousands of users.
On a host, just point it at a **persistent disk** so it isn't wiped:
- set an env var like `HH_DATA_DIR=/var/data` and (one-line change) build `DB_FILE`/`CACHE_FILE`
  from it. Ask me and I'll wire this up.

**When you scale / want multiple servers:** move to managed Postgres (free tiers):
- **Supabase** or **Neon** → create a project → copy the `DATABASE_URL`.
- This needs a focused refactor of `db.py` (SQLite → psycopg with cursors). It's ~1 file and I can
  do it once you have the `DATABASE_URL` so I can test it properly. `psycopg2-binary` is already
  listed (commented) in `requirements.txt`.

---

## 6. Legal / compliance (before public launch)
- **Terms** (`/terms.html`) and **Privacy** (`/privacy.html`) pages are included and linked in the
  footer + signup consent box.
- Signup now requires a **DPDP consent** checkbox.
- Fill in real contact + grievance-officer emails in those two HTML files.
- Respect each source's `robots.txt`/ToS; keep the built-in caching (don't lower it aggressively).
- This is a template, not legal advice — get a lawyer review before commercial launch.

---

## Quick checklist
- [ ] `.env` filled in
- [ ] Google Client ID in `index.html`
- [ ] GitHub OAuth app + secret
- [ ] SMTP working (`python reminders.py` sends)
- [ ] Deployed on Render with env vars + persistent disk
- [ ] Daily reminder cron set
- [ ] Terms/Privacy contact emails updated
