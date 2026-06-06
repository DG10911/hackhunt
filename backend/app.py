"""
HackHunt India — aggregation backend.

Run:  python app.py   (serves API + the frontend at http://localhost:5050)

Endpoints:
  GET /api/hackathons            -> aggregated, cached list (+ meta per source)
  GET /api/hackathons?refresh=1  -> force re-scrape
  GET /api/community             -> conferences / meetups / tickets
  GET /api/health

Caching: results are cached in memory AND on disk (cache.json) so a server
restart serves data instantly, then refreshes in the background.
"""

import datetime as dt
import json
import os
import re
import threading
import time
import concurrent.futures as cf

from flask import Flask, jsonify, request, send_from_directory, redirect
from flask_cors import CORS
import requests

# load .env if present (optional dependency)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

import scrapers
import db
from sample_data import SAMPLE
from community import get_community

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)

TTL = 60 * 30  # 30 min
CACHE_VERSION = 3  # bump to invalidate old on-disk caches (e.g. after data fixes)
CACHE_FILE = os.path.join(os.environ.get("HH_DATA_DIR", os.path.dirname(__file__)), "cache.json")
_CACHE = {"data": None, "ts": 0, "meta": [], "v": CACHE_VERSION}
_LOCK = threading.Lock()
_REFRESHING = False


# ---------- disk cache ----------
def _load_disk():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
        if c.get("data") and c.get("v") == CACHE_VERSION:
            _CACHE.update(c)
            print(f"[cache] loaded {len(c['data'])} records from disk")
        else:
            print("[cache] ignoring outdated disk cache")
    except Exception:
        pass


def _save_disk():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f)
    except Exception as e:
        print("[cache] save failed:", e)


# ---------- ended-event detection ----------
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _last_date(item):
    """Best-effort latest date for an event (end > deadline > start)."""
    for key in ("ends", "deadline", "starts"):
        v = item.get(key)
        if not v:
            continue
        m = _DATE_RE.search(str(v))
        if m:
            try:
                return dt.date(int(m[1]), int(m[2]), int(m[3]))
            except Exception:
                pass
        # Devpost style "May 01 - Jul 15, 2026"
        mm = re.findall(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2}).*?(\d{4})", str(v))
        if mm:
            try:
                mon, day, yr = mm[-1]
                return dt.date(int(yr), _MONTHS.get(mon.lower(), 1), int(day))
            except Exception:
                pass
    return None


def is_ended(item):
    d = _last_date(item)
    return bool(d and d < dt.date.today())


# ---------- normalization (guarantees valid links + images) ----------
def normalize(item):
    url = (item.get("url") or "").strip()
    if url and url.startswith("//"):
        url = "https:" + url
    if url and not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    item["url"] = url

    img = (item.get("image") or "")
    if img:
        if img.startswith("//"):
            img = "https:" + img
        elif not img.startswith("http"):
            img = "https://unstop.com/" + img.lstrip("/") if "unstop" in (item.get("platform", "").lower()) else ""
    item["image"] = img or None
    return item


def aggregate():
    results, meta = [], []

    def run(name, fn):
        t0 = time.time()
        try:
            items = fn() or []
            return name, items, "live" if items else "empty", round(time.time() - t0, 1), None
        except Exception as e:  # noqa
            return name, [], "error", round(time.time() - t0, 1), str(e)[:120]

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(run, n, f) for n, f in scrapers.ADAPTERS.items()]
        for fut in cf.as_completed(futs):
            name, items, status, took, err = fut.result()
            results.extend(items)
            meta.append({"platform": name, "status": status, "count": len(items),
                         "seconds": took, "error": err})

    live_platforms = {m["platform"] for m in meta if m["status"] == "live"}
    for s in SAMPLE:
        if s["platform"] not in live_platforms:
            results.append({**s, "sample": True})

    seen, deduped = set(), []
    for r in results:
        key = r.get("id") or (r.get("title"), r.get("platform"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalize(scrapers.enrich(r)))

    # archive everything (incl. ended) for history, then return only ACTIVE
    try:
        db.archive(deduped, is_ended)
    except Exception as e:
        print("[db] archive failed:", e)
    active = [x for x in deduped if not is_ended(x)]
    return active, meta


def _do_refresh():
    global _REFRESHING
    try:
        data, meta = aggregate()
        with _LOCK:
            _CACHE.update(data=data, meta=meta, ts=time.time())
        _save_disk()
        print(f"[cache] refreshed: {len(data)} records")
    except Exception as e:
        print("[cache] refresh error:", e)
    finally:
        _REFRESHING = False


def get_data(force=False):
    global _REFRESHING
    now = time.time()
    fresh = _CACHE["data"] and (now - _CACHE["ts"] <= TTL)
    if force or not _CACHE["data"]:
        # blocking refresh only when we have nothing to serve
        _do_refresh()
    elif not fresh and not _REFRESHING:
        # stale-while-revalidate: serve stale now, refresh in background
        _REFRESHING = True
        threading.Thread(target=_do_refresh, daemon=True).start()
    return _CACHE


@app.route("/api/hackathons")
def hackathons():
    try:
        c = get_data(force=request.args.get("refresh") == "1")
        return jsonify({"count": len(c["data"] or []), "updated": c["ts"],
                        "sources": c["meta"], "hackathons": c["data"] or []})
    except Exception as e:
        return jsonify({"count": 0, "hackathons": [], "sources": [],
                        "error": str(e)[:160]}), 200


@app.route("/api/community")
def community():
    try:
        allitems = [normalize(e) for e in get_community()]
        try:
            db.archive(allitems, is_ended)
        except Exception:
            pass
        items = [e for e in allitems if not is_ended(e)]  # hide ended
        return jsonify({"count": len(items), "events": items})
    except Exception as e:
        return jsonify({"count": 0, "events": [], "error": str(e)[:160]}), 200


# ---------- accounts + history (SQLite) ----------
@app.route("/api/auth", methods=["POST"])
def auth():
    try:
        u = db.upsert_user(request.get_json(force=True) or {})
        return jsonify({"ok": True, "user": u})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 200


@app.route("/api/me")
def me():
    u = db.get_user(request.args.get("email", ""))
    return jsonify({"ok": bool(u), "user": u, "saved": db.list_saved(request.args.get("email", ""))})


@app.route("/api/save", methods=["POST"])
def save():
    try:
        b = request.get_json(force=True) or {}
        db.set_saved(b.get("email"), b.get("event_id"), b.get("event"), on=bool(b.get("on", True)))
        return jsonify({"ok": True, "saved": db.list_saved(b.get("email"))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 200


@app.route("/api/history")
def history():
    return jsonify({"events": db.history(only_ended=True, limit=200)})


# ---------- team finder ----------
@app.route("/api/teams", methods=["GET", "POST"])
def teams():
    try:
        if request.method == "POST":
            b = request.get_json(force=True) or {}
            if not (b.get("event") and b.get("contact")):
                return jsonify({"ok": False, "error": "event and contact required"}), 200
            db.create_team(b)
        return jsonify({"ok": True, "teams": db.list_teams()})
    except Exception as e:
        return jsonify({"ok": False, "teams": [], "error": str(e)[:160]}), 200


@app.route("/api/teams/close", methods=["POST"])
def teams_close():
    b = request.get_json(force=True) or {}
    db.close_team(b.get("id"), b.get("email"))
    return jsonify({"ok": True, "teams": db.list_teams()})


@app.route("/api/stats")
def stats():
    return jsonify(db.stats())


# ====================== OWNER / ADMIN ======================
OWNER_USER = os.environ.get("OWNER_USER", "owner")
OWNER_PASS = os.environ.get("OWNER_PASS", "hackhunt-secret-2026")
OWNER_TOKEN = os.environ.get("OWNER_TOKEN", "owner-" + str(abs(hash(OWNER_PASS)) % 10**10))


def _is_owner():
    t = request.headers.get("X-Owner-Token") or request.args.get("otoken", "")
    return t and t == OWNER_TOKEN


@app.route("/api/track", methods=["POST"])
def track():
    try:
        b = request.get_json(force=True) or {}
        db.track(b.get("sid", ""), b.get("email", ""), b.get("name", ""),
                 b.get("kind", "view"), b.get("detail", ""), b.get("path", ""))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 200


@app.route("/api/owner/login", methods=["POST"])
def owner_login():
    b = request.get_json(force=True) or {}
    if b.get("user") == OWNER_USER and b.get("pass") == OWNER_PASS:
        return jsonify({"ok": True, "token": OWNER_TOKEN})
    return jsonify({"ok": False, "error": "invalid credentials"}), 401


@app.route("/api/owner/overview")
def owner_overview():
    if not _is_owner():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify({
        "ok": True,
        "live": db.live_users(),
        "activity": db.recent_activity(),
        "users": db.all_users(),
        "stats": db.stats(),
        "maintenance": db.get_setting("maintenance", "0") == "1",
    })


@app.route("/api/owner/delete-user", methods=["POST"])
def owner_delete_user():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    db.delete_user((request.get_json(force=True) or {}).get("email", ""))
    return jsonify({"ok": True, "users": db.all_users()})


@app.route("/api/owner/maintenance", methods=["POST"])
def owner_maintenance():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    on = bool((request.get_json(force=True) or {}).get("on"))
    db.set_setting("maintenance", "1" if on else "0")
    return jsonify({"ok": True, "maintenance": on})


@app.route("/api/owner/wipe", methods=["POST"])
def owner_wipe():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    db.wipe_all()
    return jsonify({"ok": True})


@app.before_request
def _maintenance_gate():
    # When the owner flips maintenance ON, freeze the public site APIs.
    if request.path.startswith("/api/") and db.get_setting("maintenance", "0") == "1":
        allow = ("/api/owner", "/api/track", "/api/health")
        if not any(request.path.startswith(a) for a in allow):
            return jsonify({"maintenance": True,
                            "message": "HackHunt is temporarily down for maintenance."}), 503


@app.route("/api/run-reminders", methods=["POST"])
def run_reminders():
    # protect with a shared secret so it can't be spammed publicly
    token = os.environ.get("REMINDER_TOKEN", "")
    if token and request.args.get("token") != token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        import emailer
        sent = emailer.run_reminders()
        return jsonify({"ok": True, "sent": sent})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 200


# ---------- GitHub OAuth ----------
GH_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GH_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:5050")


@app.route("/auth/github")
def github_login():
    if not GH_ID:
        return redirect("/?autherror=github_not_configured")
    cb = APP_URL.rstrip("/") + "/auth/github/callback"
    return redirect("https://github.com/login/oauth/authorize"
                    f"?client_id={GH_ID}&scope=read:user%20user:email&redirect_uri={cb}")


@app.route("/auth/github/callback")
def github_callback():
    code = request.args.get("code", "")
    if not code or not GH_SECRET:
        return redirect("/?autherror=github")
    try:
        tok = requests.post("https://github.com/login/oauth/access_token",
                            headers={"Accept": "application/json"},
                            data={"client_id": GH_ID, "client_secret": GH_SECRET, "code": code},
                            timeout=15).json().get("access_token")
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        u = requests.get("https://api.github.com/user", headers=h, timeout=15).json()
        email = u.get("email")
        if not email:
            emails = requests.get("https://api.github.com/user/emails", headers=h, timeout=15).json()
            prim = [e for e in emails if isinstance(e, dict) and e.get("primary")]
            email = (prim[0]["email"] if prim else (emails[0]["email"] if emails else ""))
        user = db.upsert_user({"name": u.get("name") or u.get("login"), "email": email,
                               "picture": u.get("avatar_url", ""),
                               "github": u.get("html_url", "")})
        from urllib.parse import urlencode
        return redirect("/?" + urlencode({"login": "github", "name": user["name"],
                                          "email": user["email"], "picture": user.get("picture", "")}))
    except Exception as e:
        print("[github] oauth error:", e)
        return redirect("/?autherror=github")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "cached": bool(_CACHE["data"]), "updated": _CACHE["ts"],
                    "maintenance": db.get_setting("maintenance", "0") == "1"})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


AUTO_REFRESH_SECS = 10 * 60  # re-scrape every 10 min so new events appear live
_BG_STARTED = False


def _auto_refresher():
    while True:
        time.sleep(AUTO_REFRESH_SECS)
        try:
            _do_refresh()
        except Exception as e:
            print("[auto] refresh error:", e)


def start_background():
    """Start cache warm + auto-refresh once (works under gunicorn AND `python app.py`)."""
    global _BG_STARTED
    if _BG_STARTED:
        return
    _BG_STARTED = True
    if not _CACHE["data"]:
        threading.Thread(target=_do_refresh, daemon=True).start()
    threading.Thread(target=_auto_refresher, daemon=True).start()


db.init()
_load_disk()
start_background()   # runs on import too, so hosting (gunicorn) keeps data fresh

if __name__ == "__main__":
    print("HackHunt India running at http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
