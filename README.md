# HackHunt India 🚀

A live-aggregating dashboard for **hackathons, ideathons, hiring challenges, government & MNC competitions, and wildlife/climate events** across India — pulling from **Unstop, Devpost, Devfolio, Hack2skill, Devnovate, and Internshala**. Built for engineering students. Premium dark UI with 3D-tilt cards, animated aurora background, motion, theme toggle, search, filters, modals and back-to-top.

## Features
- **Aggregates 6 platforms** — Unstop, Devpost, Devfolio, Hack2skill, Devnovate, Internshala.
- **Smart filters** — quick filters (MNC / Big Tech, Government, Online only, Has prize), category, theme/topic (AI/ML, Web3, Climate, Wildlife, FinTech, Cybersecurity, …), and platform.
- **Attractive image cards** — real banners when a platform provides one, otherwise an animated themed gradient + emoji, with MNC / GOVT ribbons.
- **Community & Tickets tab** — tech conferences, summits & meetups (PyConf Hyderabad, droidcon India, React India, Nullcon Goa, India Blockchain Week, AWS Community Day, …). Each "Get tickets" button goes to the event's **specific registration/ticket page** (verified), not just the org homepage.
- **Add to any calendar** — Google Calendar link plus a downloadable **.ics** file that works with Apple Calendar & Outlook, fully offline.
- **Accounts** — Google sign-in (or instant guest login). Profile with college, year & interests.
- **For students** — ❤️ Save events, ★ "Recommended for you" (sorted by your interests), 📅 Add to Google Calendar, 🔗 Share, and a Resources tab (winning tips, DSA prep, free cloud credits, GitHub Student Pack, etc.).
- **Premium UI** — professional inline SVG icons (no emojis), dark/light toggle, animated aurora, 3D-tilt cards, modals, toasts, loaders, back-to-top, fully mobile-responsive with a collapsing nav.
- **Mandatory account gate** — sign up / log in (Google or local) before the dashboard loads.
- **Images on every card** — real banners from the source, conference photos for community events, and a themed-gradient fallback so a card is never blank.
- **Guaranteed working links** — every "Open event" / "Tickets" button resolves to a valid URL (falls back to the platform's listing page if a record is missing one).
- **Student power-tools** — live deadline countdowns (Last day / Nd left, colour-coded), "Closing soon" filter, sort by Deadline / Prize / Popularity / Recommended, and a "Surprise me" random pick.
- **Fast & resilient** — backend caches to disk (instant restarts) with stale-while-revalidate background refresh; frontend caches the last load in your browser so the app paints instantly and never shows errors.

## What's inside
```
hackhunt/
├─ backend/
│  ├─ app.py            # Flask API + serves the frontend
│  ├─ scrapers.py       # one adapter per platform (live JSON/HTML) + enrich()
│  ├─ community.py      # curated conferences / meetups / tickets
│  ├─ sample_data.py    # curated fallback (govt/MNC/wildlife/ideathon/hiring)
│  └─ requirements.txt
└─ frontend/
   └─ index.html        # single-file premium UI (no build step)
```

## Quick start
```bash
cd hackhunt/backend
pip install -r requirements.txt
python app.py
```
Open **http://localhost:5000** — the frontend is served by the backend, so live data flows automatically.

> You can also just double-click `frontend/index.html`. Without the backend it runs in **demo mode** with curated data; start the backend for live results.

## How data fetching works
- **Devpost, Unstop, Devfolio** use their public JSON endpoints → real live data.
- **Hack2skill, Devnovate, Internshala** are JavaScript-rendered with no clean public API. The adapters attempt a fetch and, if nothing usable comes back, the app **falls back to curated sample entries** so every platform and every category always appears. To make these fully live, plug a real parser (or a scraping service like Bright Data / Playwright) into `fetch_hack2skill` / `fetch_devnovate` / `fetch_internshala` in `scrapers.py`.
- Results are **cached 30 min**; the **↻ Refresh** button forces a re-scrape (`/api/hackathons?refresh=1`).
- The colored dots under the filters show each source's live status (green = live, amber = fallback, pink = error).

## Categories auto-tagged
Hackathon · Ideathon · Hiring Challenge · Government · Wildlife / Climate — classified by keywords in `scrapers.py` (`CATEGORY_RULES`). Edit that list to tune.

## Google Sign-In (optional)
The app works immediately with **guest login** (and a one-click demo Google button). To enable **real Google sign-in**:
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → Create OAuth client ID → **Web application**.
2. Under *Authorized JavaScript origins* add `http://localhost:5050`.
3. Copy the **Client ID** and paste it into `GOOGLE_CLIENT_ID` near the top of the `<script>` in `frontend/index.html`.
4. Reload — a native Google button appears and profiles use your real Google account.

Accounts, saved events and interests are stored in your browser (localStorage) — no server database needed.

## Live, always-fresh & no ended events
- **Refreshes on every open** — the app paints instantly from cache, then pulls fresh data each time it loads.
- **Ended events are hidden** — any event whose end/deadline has passed is filtered out of the live lists (both backend and frontend), so you only ever see things you can still join.
- **Past events archive** — ended events aren't deleted; they're saved in the database and viewable under the **Past** tab.

## Team Finder & profiles
- **Team Finder tab** — students post "looking for teammates for SIH — need ML + UI/UX", with role, skills, a pitch, and a contact (email/WhatsApp/Discord). Others tap **Connect** to reach them. Posts live in the DB (`teams` table).
- **Rich profiles** — name, college, year, skills, achievements, GitHub, LinkedIn and interests, synced to the account.

## Live real-time updates
- The backend re-scrapes every 10 minutes in a background thread, and serves stale-while-revalidate in between.
- The frontend polls every 3 minutes while the tab is open and, when new hackathons appear, updates the list in place and shows a "N new hackathons just added" toast — no reload needed.

## Sources & legitimacy (important)
This aggregator **links out** to each event's official page and shows only short factual fields — it does not mirror full descriptions or pose as the source. Still, "scraping all sites" depends on each platform's rules:
- **Devpost** — public hackathons feed; lowest risk.
- **Unstop / Devfolio / Hack2skill / Devnovate** — internal/JSON or JS-rendered; governed by each site's ToS. Use their official/partner feeds where possible.
- **Internshala** — its Terms of Use prohibit data mining; treat as higher risk and prefer their official channels.
To keep it clearly legit: respect each `robots.txt` and ToS, rate-limit (the 10-min cache helps), keep linking back, add Terms + Privacy pages, and — once it's a real multi-user product — get partner API access and comply with India's DPDP Act for stored user data. This is guidance, not legal advice.

## Database (SQLite)
`backend/db.py` creates `hackhunt.db` with:
- `users` — every account (sign-up / Google login), with college, year & interests.
- `saved` — server-synced bookmarks (so saved events follow the account, not just the browser).
- `events` — a full archive of every event ever seen, kept even after it ends (powers the Past tab & history).
- `activity` — lightweight log of saves.

New endpoints: `POST /api/auth`, `GET /api/me?email=`, `POST /api/save`, `GET /api/history`, `GET /api/stats`.

## Data authenticity
- **Live data** (Unstop, Devpost, Devfolio) is real and fetched on demand.
- **Curated fallback** (shown only when a live source is offline) now contains **only real, verified flagship events** with official links — Smart India Hackathon, Flipkart GRiD, Amazon ML Challenge, Walmart Sparkathon, Myntra HackerRamp, TCS CodeVita, Microsoft Imagine Cup, ETHIndia, plus the conferences in the Community tab. These show a green **Verified** badge.
- **Dates are cross-checked.** Each curated event shows a status in its details: **Confirmed** (date verified from the official site — e.g. React India Oct 29–31, AWS Community Day Bengaluru Jul 11, India Blockchain Week Nov 1–2, DesignUp Oct 1–4) or **Approximate** (next-edition estimate — confirm on the official page). Past editions (e.g. PyConf Hyderabad Mar, Nullcon Goa Feb–Mar) are auto-moved to the Past tab.
- The on-disk cache is version-tagged, so any older cache from a previous version is discarded automatically on the next run.

## Notes
- This aggregates **public listings**; always confirm dates/eligibility on the source site before applying.
- Engineered to never show a blank page: live → fallback → demo, in that order.
```
API: GET /api/hackathons   ·   GET /api/hackathons?refresh=1   ·   GET /api/health
```
