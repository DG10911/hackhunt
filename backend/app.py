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
import community_scrapers
import db
from sample_data import SAMPLE
from community import get_community

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


def get_ip():
    """Real client IP across proxies/CDNs.
    Order: Cloudflare -> common real-IP headers -> X-Forwarded-For (first hop)
    -> remote_addr. Ignores private/loopback hops so the *public* IP wins."""
    def _public(ip):
        ip = (ip or "").strip()
        if not ip:
            return ""
        low = ip.lower()
        if (low.startswith(("10.", "192.168.", "127.", "169.254.", "::1", "fc", "fd"))
                or low in ("localhost", "unknown")):
            return ""
        if low.startswith("172."):
            try:
                if 16 <= int(ip.split(".")[1]) <= 31:
                    return ""
            except Exception:
                pass
        return ip

    for h in ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP", "Fly-Client-IP"):
        v = _public(request.headers.get(h, ""))
        if v:
            return v
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        for part in xff.split(","):          # left-most public address
            v = _public(part)
            if v:
                return v
        return xff.split(",")[0].strip()      # fall back to first hop
    return request.remote_addr or ""


_GEO_SEEN = set()


def _geo_async(ip):
    """Resolve an IP's location in the background (cached, never blocks)."""
    if not ip or ip in _GEO_SEEN:
        return
    _GEO_SEEN.add(ip)
    try:
        threading.Thread(target=db.geo_lookup, args=(ip,), daemon=True).start()
    except Exception:
        pass


# simple in-memory rate limiter for write endpoints (per IP)
_RL = {}
_RL_MAX, _RL_WINDOW = 40, 60  # 40 writes / minute / IP


def _rate_ok(ip):
    now = time.time()
    bucket = [t for t in _RL.get(ip, []) if now - t < _RL_WINDOW]
    bucket.append(now)
    _RL[ip] = bucket
    return len(bucket) <= _RL_MAX


@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

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


# Community cache: merge curated + live-scraped + admin-added, refresh every 30 min
_COMM = {"data": None, "ts": 0, "meta": []}
_COMM_TTL = 60 * 30
_COMM_REFRESHING = False


def _merge_community():
    """Curated list + live scrapers + owner-added events, de-duplicated."""
    merged, meta = [], []
    # 1) curated (always present, verified brands)
    for c in get_community():
        merged.append(c)
    # 2) live scrapers (best-effort)
    try:
        scraped, meta = community_scrapers.fetch_all()
        for s in scraped:
            s.setdefault("verified", False)
            s.setdefault("sample", False)
            merged.append(s)
    except Exception as e:
        meta = [{"platform": "scrapers", "status": "error", "error": str(e)[:100]}]
    # 3) owner-added events (highest trust, override duplicates by id)
    try:
        for a in db.list_community_events():
            merged.append(a)
    except Exception:
        pass
    # de-dupe by id, then by (title, starts)
    seen, out = set(), []
    for e in merged:
        key = e.get("id") or (e.get("title"), e.get("starts"))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out, meta


def _refresh_community():
    global _COMM_REFRESHING
    try:
        data, meta = _merge_community()
        _COMM.update(data=data, meta=meta, ts=time.time())
    except Exception as e:
        print("[community] refresh error:", e)
    finally:
        _COMM_REFRESHING = False


def get_community_merged(force=False):
    global _COMM_REFRESHING
    now = time.time()
    fresh = _COMM["data"] is not None and (now - _COMM["ts"] <= _COMM_TTL)
    if force or _COMM["data"] is None:
        _refresh_community()
    elif not fresh and not _COMM_REFRESHING:
        _COMM_REFRESHING = True
        threading.Thread(target=_refresh_community, daemon=True).start()
    return _COMM["data"] or []


@app.route("/api/community")
def community():
    try:
        raw = get_community_merged(force=request.args.get("refresh") == "1")
        allitems = [normalize(e) for e in raw]
        try:
            db.archive(allitems, is_ended)
        except Exception:
            pass
        items = [e for e in allitems if not is_ended(e)]  # hide ended
        return jsonify({"count": len(items), "events": items, "meta": _COMM["meta"]})
    except Exception as e:
        return jsonify({"count": 0, "events": [], "error": str(e)[:160]}), 200


# ---------- accounts + history (SQLite) ----------
@app.route("/api/auth", methods=["POST"])
def auth():
    try:
        b = request.get_json(force=True) or {}
        email = (b.get("email") or "").strip().lower()
        if db.is_email_banned(email):
            return jsonify({"ok": False, "error": "account suspended"}), 403
        # auto-scan: if the profile carries an attack payload, ban + block IP
        if db.looks_malicious(b.get("name"), b.get("college"), b.get("year"),
                              b.get("github"), b.get("linkedin"), b.get("skills"),
                              b.get("achievements")):
            db.block_ip(get_ip(), "auto: malicious profile payload")
            if email:
                db.ban_user(email, "auto: injection attempt")
            return jsonify({"ok": False, "error": "blocked"}), 403
        u = db.upsert_user(b)
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
            if db.is_email_banned((b.get("email") or "").lower()):
                return jsonify({"ok": False, "error": "account suspended"}), 403
            if db.looks_malicious(b.get("event"), b.get("role"), b.get("looking_for"),
                                  b.get("message"), b.get("contact"), b.get("skills")):
                db.block_ip(get_ip(), "auto: malicious team post")
                if b.get("email"):
                    db.ban_user((b.get("email") or "").lower(), "auto: injection attempt")
                return jsonify({"ok": False, "error": "blocked"}), 403
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
                 b.get("kind", "view"), b.get("detail", ""), b.get("path", ""), ip=get_ip())
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
    live = db.live_users()
    for u in live:                         # attach cached geo + warm new IPs
        u["geo"] = db.geo_get(u.get("ip", ""))
        _geo_async(u.get("ip", ""))
    blocked = db.list_blocked_ips()
    for b in blocked:
        b["geo"] = db.geo_get(b.get("ip", ""))
    threats = db.recent_threats()
    for t in threats:
        if not t.get("country"):
            g = db.geo_get(t.get("ip", ""))
            t["country"] = g.get("country", "") if g else ""
    return jsonify({
        "ok": True,
        "live": live,
        "activity": db.recent_activity(),
        "users": db.all_users(),
        "blocked_ips": blocked,
        "threats": threats,
        "threat_stats": db.threat_stats(),
        "stats": db.stats(),
        "maintenance": db.get_setting("maintenance", "0") == "1",
    })


@app.route("/api/security")
def security_status():
    """Public, lightweight: confirms the protection layer is active.
    Powers the green 'Protected' badge in the app. Reveals nothing sensitive."""
    return jsonify({
        "protected": True,
        "engine": "HackHunt Shield",
        "features": ["injection-scan", "auto-ban", "ip-block", "rate-limit",
                     "scanner-detect", "secure-headers"],
        "version": 2,
    })


@app.route("/api/owner/block-ip", methods=["POST"])
def owner_block_ip():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    b = request.get_json(force=True) or {}
    db.block_ip(b.get("ip", ""), b.get("reason", "manual"))
    return jsonify({"ok": True, "blocked_ips": db.list_blocked_ips()})


@app.route("/api/owner/unblock-ip", methods=["POST"])
def owner_unblock_ip():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    db.unblock_ip((request.get_json(force=True) or {}).get("ip", ""))
    return jsonify({"ok": True, "blocked_ips": db.list_blocked_ips()})


@app.route("/api/owner/ban-user", methods=["POST"])
def owner_ban_user():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    b = request.get_json(force=True) or {}
    email = b.get("email", "")
    ip = db.auto_ban_ip_for_email(email) if b.get("block_ip") else ""
    db.ban_user(email, "manual ban")
    return jsonify({"ok": True, "users": db.all_users(),
                    "blocked_ips": db.list_blocked_ips(), "auto_blocked_ip": ip})


@app.route("/api/owner/delete-user", methods=["POST"])
def owner_delete_user():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    db.delete_user((request.get_json(force=True) or {}).get("email", ""))
    return jsonify({"ok": True, "users": db.all_users()})


@app.route("/api/owner/community/add", methods=["POST"])
def owner_community_add():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    rec = db.add_community_event(request.get_json(force=True) or {})
    _refresh_community()  # reflect immediately
    return jsonify({"ok": bool(rec), "event": rec,
                    "events": db.list_community_events()})


@app.route("/api/owner/community/delete", methods=["POST"])
def owner_community_delete():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    db.delete_community_event((request.get_json(force=True) or {}).get("id", ""))
    _refresh_community()
    return jsonify({"ok": True, "events": db.list_community_events()})


@app.route("/api/owner/community/list")
def owner_community_list():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    return jsonify({"ok": True, "events": db.list_community_events()})


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


def _request_payloads():
    """Everything an attacker could inject: path, query values, JSON/form body."""
    vals = [request.path, request.query_string.decode("utf-8", "ignore")]
    try:
        vals += [str(v) for v in request.args.values()]
    except Exception:
        pass
    try:
        if request.is_json:
            j = request.get_json(silent=True) or {}
            def walk(o):
                if isinstance(o, dict):
                    for v in o.values():
                        walk(v)
                elif isinstance(o, (list, tuple)):
                    for v in o:
                        walk(v)
                else:
                    vals.append(str(o))
            walk(j)
        elif request.form:
            vals += [str(v) for v in request.form.values()]
    except Exception:
        pass
    return vals


def _current_email():
    try:
        if request.is_json:
            return ((request.get_json(silent=True) or {}).get("email") or "").lower()
    except Exception:
        pass
    return ""


@app.before_request
def _gate():
    p = request.path
    ip = get_ip()
    ua = request.headers.get("User-Agent", "")
    owner_path = p.startswith("/api/owner")
    # 1) blocked IPs get nothing (except owner console so you can still manage)
    if not owner_path:
        try:
            if db.is_ip_blocked(ip):
                return jsonify({"blocked": True, "message": "Access denied."}), 403
        except Exception:
            pass
    # 2) scanner/attack-tool user agents -> instant ban
    if not owner_path and db.is_bad_ua(ua):
        db.auto_defend(ip=ip, email="", kind="scanner_tool",
                       detail="UA: " + ua[:120], path=p, severity="critical", ua=ua)
        _geo_async(ip)
        return jsonify({"blocked": True, "message": "Access denied."}), 403
    # 3) probing for secret/admin paths (/.env, /wp-login, /.git ...) -> ban
    if not owner_path and db.is_bad_path(p):
        db.auto_defend(ip=ip, email="", kind="path_probe",
                       detail="probe: " + p[:120], path=p, severity="high", ua=ua)
        _geo_async(ip)
        return jsonify({"blocked": True, "message": "Access denied."}), 403
    # 4) AUTO-SCAN every request body/query for injection -> instant auto-defend
    if not owner_path and p != "/api/health":
        try:
            kind, sev, hit = db.scan_values(_request_payloads())
            if kind:
                db.auto_defend(ip=ip, email=_current_email(), kind=kind,
                               detail=hit, path=p, severity=sev or "high", ua=ua)
                _geo_async(ip)
                return jsonify({"blocked": True,
                                "message": "Malicious request blocked."}), 403
        except Exception:
            pass
    # 5) rate-limit write APIs — repeated flooding escalates to an auto-block
    if request.method == "POST" and p.startswith("/api/") and not owner_path:
        if not _rate_ok(ip):
            strikes = db.record_strike(ip, 1)
            if strikes >= 5:  # sustained flood => treat as attack
                db.auto_defend(ip=ip, email=_current_email(), kind="flood",
                               detail="rate-limit exceeded %d times" % strikes,
                               path=p, severity="medium", ua=ua)
                return jsonify({"blocked": True, "message": "Access denied."}), 403
            return jsonify({"error": "Too many requests, slow down."}), 429
    # 4) maintenance freeze
    if p.startswith("/api/") and db.get_setting("maintenance", "0") == "1":
        allow = ("/api/owner", "/api/track", "/api/health")
        if not any(p.startswith(a) for a in allow):
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
        try:
            _refresh_community()   # keep conferences/meetups fresh too
        except Exception as e:
            print("[auto] community refresh error:", e)


def start_background():
    """Start cache warm + auto-refresh once (works under gunicorn AND `python app.py`)."""
    global _BG_STARTED
    if _BG_STARTED:
        return
    _BG_STARTED = True
    if not _CACHE["data"]:
        threading.Thread(target=_do_refresh, daemon=True).start()
    threading.Thread(target=_refresh_community, daemon=True).start()
    threading.Thread(target=_auto_refresher, daemon=True).start()


db.init()
_load_disk()
start_background()   # runs on import too, so hosting (gunicorn) keeps data fresh

if __name__ == "__main__":
    print("HackHunt India running at http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
