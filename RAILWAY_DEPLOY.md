# Deploy HackHunt India to Railway

## 1. Push to GitHub
```bash
cd ~/Desktop/hackhunt
git config --global user.name "Pranay"          # one-time
git config --global user.email "sonugoenka40@gmail.com"
git init
git add .
git commit -m "HackHunt India — initial deploy"
git branch -M main

# with GitHub CLI:
gh repo create hackhunt --public --source=. --push
# OR manually (after creating an empty repo at github.com/new named "hackhunt"):
git remote add origin https://github.com/YOUR_USERNAME/hackhunt.git
git push -u origin main
```
`.env`, `hackhunt.db`, `cache.json` are gitignored — no secrets are pushed.

## 2. Railway
1. railway.app → **New Project → Deploy from GitHub repo** → choose `hackhunt`.
2. Service → **Settings**:
   - **Root Directory:** `backend`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
3. Settings → **Volumes** → New volume, mount path **`/var/data`** (persistent DB).
4. **Variables** → add (values from backend/.env):
   ```
   HH_DATA_DIR=/var/data
   APP_URL=https://YOUR-APP.up.railway.app
   OWNER_USER=pranay
   OWNER_PASS=LSygaZ3zLMx9GGv-
   OWNER_TOKEN=LM8XzcHKQ4BRA3bUk0sR_exgS4aFaeaa
   REMINDER_TOKEN=fg7EA0xqSBSqspCHfnBu4A
   ```
   (GitHub/SMTP vars optional; add when ready.)
5. Settings → **Networking → Generate Domain**. Set `APP_URL` to that domain and redeploy.

## 3. After it's live
- Site: `https://YOUR-APP.up.railway.app`  ·  Owner console: `…/owner.html`
- GitHub OAuth callback → `https://YOUR-APP.up.railway.app/auth/github/callback`
- Google OAuth → add the domain to Authorized JavaScript origins, paste Client ID into `frontend/index.html`
- Reminder emails → cron-job.org → daily POST to
  `https://YOUR-APP.up.railway.app/api/run-reminders?token=fg7EA0xqSBSqspCHfnBu4A`

## Update later
```bash
cd ~/Desktop/hackhunt
git add -A && git commit -m "update" && git push   # Railway auto-redeploys
```

## Notes
- Free SQLite on the Railway volume is fine for a long time. Move to Postgres (Supabase/Neon)
  only when you need multiple servers — that's a focused db.py change to do with a real DATABASE_URL.
- Change OWNER_PASS to your own before sharing the site. Railway serves HTTPS by default.
