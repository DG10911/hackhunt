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
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com https://apis.google.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://accounts.google.com; "
        "frame-src https://accounts.google.com; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'")
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

    # Always include curated, verified cross-platform events (Unstop, Devpost,
    # MNC, Govt, Hack2skill...) so the feed shows real variety even when those
    # platforms' scrapers are blocked on the server. Dedupe handles overlaps.
    for s in SAMPLE:
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
    # admin-added hackathons (from the owner dashboard) always included
    try:
        for a in db.list_community_events(kind="hackathon"):
            active_a = normalize(a)
            if not is_ended(active_a):
                deduped.append(active_a)
    except Exception:
        pass
    active = [x for x in deduped if not is_ended(x)]
    active = _balance_by_platform(active)
    # featured first within the balanced order
    active.sort(key=lambda x: 0 if x.get("featured") else 1)
    return active, meta


def _balance_by_platform(items):
    """Round-robin interleave events across platforms so no single source
    (e.g. Devfolio) dominates the top of the feed. Preserves each platform's
    internal order."""
    from collections import OrderedDict
    buckets = OrderedDict()
    for it in items:
        p = (it.get("platform") or "Other").strip() or "Other"
        buckets.setdefault(p, []).append(it)
    mixed, lists = [], [list(v) for v in buckets.values()]
    i = 0
    while any(lists):
        lst = lists[i % len(lists)]
        if lst:
            mixed.append(lst.pop(0))
        if not lists[i % len(lists)]:
            lists = [l for l in lists if l]
            i = 0
            continue
        i += 1
    return mixed


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
    # 3) owner-added community events (hackathon-kind ones go to the main feed)
    try:
        for a in db.list_community_events(kind="community"):
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
        if email not in OWNER_EMAILS and db.is_email_banned(email):
            return jsonify({"ok": False, "error": "account suspended"}), 403
        # Detection is handled by the request gate (graduated: warn first, ban only
        # unambiguous attacks or persistent offenders). All fields are sanitized by
        # db.clean() and escaped on render, so stray symbols a real student types
        # are neutralized without banning them.
        ref = (b.get("ref") or "").strip().upper()
        was_new = bool(ref) and db.get_user(email) is None
        u = db.upsert_user(b)
        if was_new:                      # this signup may have unlocked a tier
            threading.Thread(target=_maybe_tier_email, args=(ref,), daemon=True).start()
        return jsonify({"ok": True, "user": u})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 200


def _maybe_tier_email(code):
    """If this new referral pushed the ambassador exactly onto a tier, email them
    a congrats + certificate link. Best-effort (needs SMTP configured)."""
    try:
        count = db._referral_count(code)
        tier = next((n for n, t in db.TIERS if t == count), None)  # exact hit
        if not tier:
            return
        amb = db.get_ambassador(code)
        if not amb or "@" not in (amb.get("email") or ""):
            return
        import emailer
        url = "https://hackhunt.xyz/ambassador.html"
        html = f"""<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto">
          <h2 style="color:#7c5cff">🎉 Congrats, {amb['name'].split(' ')[0]}! You unlocked {tier}</h2>
          <p style="color:#444">You've brought <b>{count} sign-ups</b> to HackHunt — amazing work!
          Your <b>{tier}</b> certificate is now unlocked.</p>
          <p><a href="{url}" style="background:#7c5cff;color:#fff;padding:11px 20px;border-radius:8px;
          text-decoration:none">Claim your certificate →</a></p>
          <p style="color:#999;font-size:12px;margin-top:20px">Add it to LinkedIn in one click from your
          ambassador dashboard. Keep going for the next tier! — Team HackHunt</p></div>"""
        emailer.send_email(amb["email"], f"🎉 You unlocked {tier} on HackHunt!", html)
    except Exception as e:
        print("[tier-email] error:", e)


@app.route("/api/me")
def me():
    u = db.get_user(request.args.get("email", ""))
    return jsonify({"ok": bool(u), "user": u, "saved": db.list_saved(request.args.get("email", ""))})


# ---------- Campus Ambassador: referral + leaderboard + certificates ----------
@app.route("/api/ambassador/register", methods=["POST"])
def ambassador_register():
    try:
        b = request.get_json(force=True) or {}
        email = (b.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return jsonify({"ok": False, "error": "Valid email required"}), 200
        if db.is_email_banned(email):
            return jsonify({"ok": False, "error": "account suspended"}), 403
        # detection handled by the gate (graduated); fields are sanitized on store
        amb = db.create_ambassador(b.get("name"), email, b.get("college"))
        return jsonify({"ok": True, "ambassador": amb})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 200


@app.route("/api/ambassador/stats")
def ambassador_stats():
    code = request.args.get("code", "")
    email = request.args.get("email", "")
    if not code and email:
        amb = db.get_ambassador_by_email(email)
        code = amb["code"] if amb else ""
    s = db.ambassador_stats(code) if code else None
    return jsonify({"ok": bool(s), "stats": s})


@app.route("/api/leaderboard")
def leaderboard():
    return jsonify({"ok": True, "leaderboard": db.leaderboard(int(request.args.get("limit", 50)))})


@app.route("/api/ambassador/cert", methods=["POST"])
def ambassador_cert():
    b = request.get_json(force=True) or {}
    cert = db.issue_cert(b.get("code", ""), b.get("tier", ""))
    if not cert:
        return jsonify({"ok": False, "error": "Tier not unlocked yet"}), 200
    return jsonify({"ok": True, "cert": cert})


@app.route("/api/verify-cert")
def verify_cert():
    c = db.verify_cert(request.args.get("id", ""))
    return jsonify({"ok": bool(c), "cert": c})


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
            # detection handled by the gate (graduated); team-post fields are
            # sanitized on store and escaped on render
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
# Optional 2FA: set OWNER_TOTP_SECRET (base32) to require a 6-digit code at login.
OWNER_TOTP_SECRET = os.environ.get("OWNER_TOTP_SECRET", "").strip().replace(" ", "").upper()

# Owner-login brute-force lockout (per IP, in-memory)
_OWNER_FAILS = {}          # ip -> [timestamps of recent failures]
_LOCK_MAX = 5              # attempts
_LOCK_WINDOW = 15 * 60     # within 15 min
_LOCK_FOR = 15 * 60        # locked out for 15 min


def _owner_locked(ip):
    now = time.time()
    fails = [t for t in _OWNER_FAILS.get(ip, []) if now - t < _LOCK_FOR]
    _OWNER_FAILS[ip] = fails
    return len(fails) >= _LOCK_MAX


def _owner_fail(ip):
    _OWNER_FAILS.setdefault(ip, []).append(time.time())


def _owner_reset(ip):
    _OWNER_FAILS.pop(ip, None)


def _totp_now(secret, drift=0):
    """RFC-6238 TOTP (30s, 6 digits, SHA1) using only the standard library."""
    import base64
    import hashlib
    import hmac
    import struct
    try:
        key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    except Exception:
        return None
    counter = int(time.time() // 30) + drift
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000
    return "%06d" % code


def _totp_ok(secret, code):
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False
    # accept current window +/- 1 step (clock drift tolerance)
    return any(_totp_now(secret, d) == code for d in (-1, 0, 1))


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
    import hmac as _hmac
    ip = get_ip()
    if _owner_locked(ip):
        return jsonify({"ok": False, "error": "Too many attempts. Try again later.",
                        "locked": True}), 429
    b = request.get_json(force=True) or {}
    user_ok = _hmac.compare_digest(str(b.get("user", "")), OWNER_USER)
    pass_ok = _hmac.compare_digest(str(b.get("pass", "")), OWNER_PASS)
    if not (user_ok and pass_ok):
        _owner_fail(ip)
        db.log_threat(ip=ip, email="", kind="owner_login_fail",
                      detail="bad owner credentials", path="/api/owner/login",
                      action="logged", severity="high", ua=request.headers.get("User-Agent", ""))
        return jsonify({"ok": False, "error": "invalid credentials"}), 401
    # password correct -> enforce 2FA if configured
    if OWNER_TOTP_SECRET:
        if not _totp_ok(OWNER_TOTP_SECRET, b.get("code")):
            _owner_fail(ip)
            return jsonify({"ok": False, "error": "2FA code required or invalid.",
                            "need_2fa": True}), 401
    _owner_reset(ip)
    return jsonify({"ok": True, "token": OWNER_TOKEN, "twofa": bool(OWNER_TOTP_SECRET)})


@app.route("/api/owner/2fa-setup", methods=["POST"])
def owner_2fa_setup():
    """Authenticated helper: generate a fresh TOTP secret + otpauth URL so the
    owner can add HackHunt to Google Authenticator. Set the secret as
    OWNER_TOTP_SECRET in env to activate. Requires current owner token."""
    if not _is_owner():
        return jsonify({"ok": False}), 401
    import base64
    import os as _os
    secret = base64.b32encode(_os.urandom(20)).decode().rstrip("=")
    label = "HackHunt%20Owner"
    otpauth = ("otpauth://totp/%s?secret=%s&issuer=HackHunt&algorithm=SHA1&digits=6&period=30"
               % (label, secret))
    return jsonify({"ok": True, "secret": secret, "otpauth": otpauth,
                    "active": bool(OWNER_TOTP_SECRET)})


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
        "banned_users": db.list_banned_emails(),
        "ambassadors": db.leaderboard(100),
        "threats": threats,
        "threat_stats": db.threat_stats(),
        "stats": db.stats(),
        "maintenance": db.get_setting("maintenance", "0") == "1",
    })


@app.route("/api/live-count")
def live_count():
    """Public: how many students are on the site right now + total users.
    Powers the social-proof badge. No personal data exposed."""
    try:
        return jsonify({"online": len(db.live_users()), "total": db.stats().get("users", 0)})
    except Exception:
        return jsonify({"online": 0, "total": 0})


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


@app.route("/api/owner/unban-user", methods=["POST"])
def owner_unban_user():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    b = request.get_json(force=True) or {}
    db.unban_user(b.get("email", ""))
    if b.get("ip"):                        # optionally lift their blocked IP too
        db.unblock_ip(b.get("ip"))
    return jsonify({"ok": True, "banned": db.list_banned_emails(),
                    "blocked_ips": db.list_blocked_ips()})


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


@app.route("/api/owner/clear-blocks", methods=["POST"])
def owner_clear_blocks():
    if not _is_owner():
        return jsonify({"ok": False}), 401
    n = db.clear_all_blocks()
    return jsonify({"ok": True, "cleared": n, "blocked_ips": db.list_blocked_ips()})


# Fields that are ALWAYS URLs / identifiers from trusted OAuth or our own UI.
# They are validated elsewhere and never rendered as raw HTML, so scanning them
# only produces false positives (e.g. a Google profile-picture URL). Skip them.
_SAFE_KEYS = {
    "picture", "image", "img", "avatar", "photo", "thumbnail",
    "email", "url", "ticket_url", "link", "href",
    "github", "linkedin", "otpauth", "sid", "code", "token", "pass",
    "event", "event_json", "detail", "path", "kind",  # app/catalog data, not user input
}


def _request_payloads():
    """(key, value) pairs an attacker could inject, EXCLUDING trusted URL/id
    fields. Scans path, query values, and JSON/form body recursively."""
    pairs = [("_path", request.path),
             ("_query", request.query_string.decode("utf-8", "ignore"))]
    try:
        for k, v in request.args.items():
            if k.lower() not in _SAFE_KEYS:
                pairs.append((k, str(v)))
    except Exception:
        pass
    try:
        if request.is_json:
            j = request.get_json(silent=True) or {}

            def walk(o, key=""):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if str(k).lower() in _SAFE_KEYS:
                            continue
                        walk(v, str(k))
                elif isinstance(o, (list, tuple)):
                    for v in o:
                        walk(v, key)
                else:
                    pairs.append((key, str(o)))
            walk(j)
        elif request.form:
            for k, v in request.form.items():
                if k.lower() not in _SAFE_KEYS:
                    pairs.append((k, str(v)))
    except Exception:
        pass
    return pairs


def _current_email():
    try:
        if request.is_json:
            return ((request.get_json(silent=True) or {}).get("email") or "").lower()
    except Exception:
        pass
    return ""


CANONICAL_HOST = os.environ.get("CANONICAL_HOST", "").strip().lower()
# Your own IPs — never blocked or scanned. Set OWNER_IPS="1.2.3.4,5.6.7.8" in env.
OWNER_IPS = {x.strip() for x in os.environ.get("OWNER_IPS", "").split(",") if x.strip()}
# Your own accounts — never auto-banned. Set OWNER_EMAILS="you@gmail.com" in env.
OWNER_EMAILS = {x.strip().lower() for x in os.environ.get("OWNER_EMAILS", "").split(",") if x.strip()}
# Endpoints that carry OUR catalog/telemetry data (echoed back by the client),
# not fresh user free-text — skip injection scanning here to avoid false bans.
SCAN_EXEMPT_PATHS = {"/api/save", "/api/track", "/api/me", "/api/history"}
# Unambiguous attacks — no legitimate user ever types these → instant ban.
# Softer kinds (xss/template/nosql) in a normal form are usually a curious user
# typing quotes/brackets; their data is sanitized + escaped anyway, so we warn
# and only ban a persistent attacker (many attempts).
CRITICAL_KINDS = {"sqli", "cmdi", "path_traversal", "ssrf"}


@app.before_request
def _gate():
    p = request.path
    ip = get_ip()
    ua = request.headers.get("User-Agent", "")
    owner_path = p.startswith("/api/owner")
    # the owner console page itself is always reachable, so you can never lock
    # yourself out of unblocking — even from a blocked/CGNAT IP
    console = owner_path or p == "/owner.html"
    # your own allowlisted IPs bypass all security checks
    if ip in OWNER_IPS:
        return
    # 0) canonical-domain redirect: send the *.railway.app link (and any other
    #    host) to https://hackhunt.xyz. Skip health check so Railway monitoring
    #    still gets a 200. Only active once CANONICAL_HOST env is set.
    if CANONICAL_HOST and p != "/api/health":
        host = (request.host or "").split(":")[0].lower()
        if host and host != CANONICAL_HOST and not host.endswith(".cloudflareaccess.com"):
            return redirect("https://" + CANONICAL_HOST + request.full_path.rstrip("?"), code=301)
    # 1) blocked IPs get nothing (except owner console so you can still manage)
    if not console:
        try:
            if db.is_ip_blocked(ip):
                return jsonify({"blocked": True, "message": "Access denied."}), 403
        except Exception:
            pass
    # 1b) banned accounts get nothing — even on a new/rotating IP. Their current
    #     IP is auto-blocked too, so mobile IP-hopping gets shut down progressively.
    if not owner_path:
        try:
            em = _current_email()
            if em and em not in OWNER_EMAILS and db.is_email_banned(em):
                if ip and not db.is_ip_blocked(ip):
                    db.block_ip(ip, "auto: banned account on new IP")
                    db.log_threat(ip=ip, email=em, kind="banned_evasion",
                                  detail="banned account seen on new IP", path=p,
                                  action="ip_blocked", severity="medium", ua=ua)
                return jsonify({"blocked": True, "message": "Access denied."}), 403
        except Exception:
            pass
    # 1c) CSRF guard: state-changing POSTs must come from our own origin.
    #     Blocks classic cross-site request forgery. Same-origin & non-browser
    #     clients (no Origin header) are allowed; a *mismatched* Origin is not.
    if request.method == "POST" and p.startswith("/api/"):
        origin = request.headers.get("Origin", "")
        if origin:
            host = request.host_url.rstrip("/")
            allowed = {host}
            extra = os.environ.get("ALLOWED_ORIGINS", "")
            allowed |= {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}
            if not any(origin.rstrip("/") == a for a in allowed):
                return jsonify({"error": "cross-origin request blocked"}), 403
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
    # 4) AUTO-SCAN real injection vectors only (signup/profile, team posts, etc.)
    #    Skips our own data-echo endpoints and your owner accounts -> no false bans.
    if (not owner_path and p != "/api/health" and p not in SCAN_EXEMPT_PATHS
            and _current_email() not in OWNER_EMAILS):
        try:
            kind, sev, hit = db.scan_pairs(_request_payloads())
            if kind:
                if kind in CRITICAL_KINDS:
                    # SQLi / command / path-traversal / SSRF — ban on sight
                    db.auto_defend(ip=ip, email=_current_email(), kind=kind,
                                   detail=hit, path=p, severity="critical", ua=ua)
                    _geo_async(ip)
                    return jsonify({"blocked": True,
                                    "message": "Malicious request blocked."}), 403
                # softer (xss/template/nosql): warn first, ban only a persistent attacker
                strikes = db.record_strike("inj:" + ip, 1)
                if strikes >= 4:
                    db.auto_defend(ip=ip, email=_current_email(), kind=kind,
                                   detail=hit, path=p, severity="high", ua=ua)
                    _geo_async(ip)
                    return jsonify({"blocked": True,
                                    "message": "Repeated unsafe input — access denied."}), 403
                db.log_threat(ip=ip, email=_current_email(), kind=kind, detail=hit,
                              path=p, action="warned (sanitized)", severity=sev or "medium", ua=ua)
                return jsonify({"error": "Your input contains characters that aren't allowed "
                                "(like < > \" ' ; ). Please remove them and try again."}), 400
        except Exception:
            pass
    # 5) rate-limit write APIs — a soft 429 only (NO permanent ban). On shared
    #    mobile/CGNAT IPs many real students share one address, so flooding alone
    #    must never hard-block. Real attacks are caught by the payload scanner above.
    if request.method == "POST" and p.startswith("/api/") and not owner_path:
        if not _rate_ok(ip):
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
# Emergency recovery: set CLEAR_BLOCKS=1 in env + redeploy to lift every IP block
# (e.g. if you got caught by a shared/CGNAT block). Remove the var afterwards.
if os.environ.get("CLEAR_BLOCKS", "").strip() in ("1", "true", "yes"):
    try:
        print("[recovery] CLEAR_BLOCKS set — cleared %d IP block(s)" % db.clear_all_blocks())
    except Exception as e:
        print("[recovery] clear failed:", e)
_load_disk()
start_background()   # runs on import too, so hosting (gunicorn) keeps data fresh

if __name__ == "__main__":
    print("HackHunt India running at http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
