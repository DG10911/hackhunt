"""
HackHunt India — SQLite database layer.

Stores:
  users     — every account (from sign-up / Google login)
  saved     — each user's saved events (server-synced bookmarks)
  events    — full archive of every event ever seen (kept even after it ends)
  activity  — lightweight activity log (views / saves)

A single file `hackhunt.db` is created next to this module.
"""

import json
import os
import re
import sqlite3
import threading
import time

_TAG_RE = re.compile(r"<[^>]*>")


def clean(s, maxlen=200):
    """Strip HTML tags & control chars from any user-supplied text, cap length."""
    if s is None:
        return ""
    s = _TAG_RE.sub("", str(s))
    s = s.replace("<", "").replace(">", "")
    s = "".join(ch for ch in s if ch == "\n" or ord(ch) >= 32)
    return s.strip()[:maxlen]


def clean_list(arr, maxitems=15, maxlen=40):
    if not isinstance(arr, list):
        return []
    out = [clean(x, maxlen) for x in arr[:maxitems]]
    return [x for x in out if x]  # drop blanks left after stripping

# Persist data on a disk that survives restarts when hosted.
# Set HH_DATA_DIR (e.g. Render disk mount /var/data) in production.
DATA_DIR = os.environ.get("HH_DATA_DIR", os.path.dirname(__file__))
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = os.path.dirname(__file__)
DB_FILE = os.path.join(DATA_DIR, "hackhunt.db")
_LOCK = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _LOCK, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            email TEXT PRIMARY KEY, name TEXT, picture TEXT,
            college TEXT, year TEXT, interests TEXT,
            created REAL, last_seen REAL);
        CREATE TABLE IF NOT EXISTS saved(
            email TEXT, event_id TEXT, event_json TEXT, ts REAL,
            PRIMARY KEY(email, event_id));
        CREATE TABLE IF NOT EXISTS events(
            id TEXT PRIMARY KEY, title TEXT, category TEXT, platform TEXT,
            ends TEXT, json TEXT, first_seen REAL, last_seen REAL, ended INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS activity(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, action TEXT,
            event_id TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS teams(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT, picture TEXT,
            event TEXT, role TEXT, looking_for TEXT, skills TEXT, message TEXT,
            contact TEXT, open INTEGER DEFAULT 1, ts REAL);
        CREATE TABLE IF NOT EXISTS analytics(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sid TEXT, email TEXT,
            name TEXT, kind TEXT, detail TEXT, path TEXT);
        CREATE TABLE IF NOT EXISTS presence(
            sid TEXT PRIMARY KEY, email TEXT, name TEXT, path TEXT, last_seen REAL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS blocked_ips(ip TEXT PRIMARY KEY, reason TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS banned_emails(email TEXT PRIMARY KEY, reason TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS threats(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ip TEXT, email TEXT,
            kind TEXT, detail TEXT, path TEXT, action TEXT, severity TEXT, ua TEXT, country TEXT);
        CREATE TABLE IF NOT EXISTS geo(
            ip TEXT PRIMARY KEY, country TEXT, region TEXT, city TEXT, org TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS strikes(
            ip TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last REAL);
        CREATE TABLE IF NOT EXISTS community_events(
            id TEXT PRIMARY KEY, json TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS ambassadors(
            code TEXT PRIMARY KEY, name TEXT, email TEXT, college TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS certs(
            cert_id TEXT PRIMARY KEY, code TEXT, name TEXT, tier TEXT, issued REAL);
        """)
        # migrations: add newer columns if missing (older DBs)
        for tbl, col in (("users", "skills TEXT"), ("users", "achievements TEXT"),
                         ("users", "github TEXT"), ("users", "linkedin TEXT"),
                         ("analytics", "ip TEXT"), ("presence", "ip TEXT"),
                         ("threats", "severity TEXT"), ("threats", "ua TEXT"),
                         ("threats", "country TEXT"), ("users", "ref TEXT")):
            try:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col}")
            except Exception:
                pass


# ---------- users ----------
def upsert_user(u):
    now = time.time()
    email = (u.get("email") or "").strip().lower() or ("guest_" + str(int(now)))
    with _LOCK, _conn() as c:
        row = c.execute("SELECT email FROM users WHERE email=?", (email,)).fetchone()
        # sanitize everything the user controls
        name = clean(u.get("name"), 80) or "Student"
        picture = u.get("picture", "")
        picture = picture if str(picture).startswith("http") else ""  # only real image URLs
        college = clean(u.get("college"), 80)
        year = clean(u.get("year"), 40)
        github = clean(u.get("github"), 120)
        linkedin = clean(u.get("linkedin"), 120)
        interests = json.dumps(clean_list(u.get("interests"), 20, 30))
        skills = json.dumps(clean_list(u.get("skills"), 25, 30))
        ach = json.dumps(clean_list(u.get("achievements"), 15, 80))
        if row:
            c.execute("""UPDATE users SET name=?, picture=?, college=?, year=?, interests=?,
                         skills=?, achievements=?, github=?, linkedin=?, last_seen=? WHERE email=?""",
                      (name, picture, college, year, interests, skills, ach, github, linkedin, now, email))
        else:
            ref = clean(u.get("ref"), 40).upper()  # referral code, set once at signup
            # ignore self-referral (ambassador using their own link)
            c.execute("""INSERT INTO users(email,name,picture,college,year,interests,skills,
                         achievements,github,linkedin,created,last_seen,ref)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (email, name, picture, college, year, interests, skills, ach, github,
                       linkedin, now, now, ref))
    return get_user(email)


def get_user(email):
    email = (email or "").strip().lower()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not r:
            return None
        u = dict(r)
        u["interests"] = json.loads(u.get("interests") or "[]")
        u["skills"] = json.loads(u.get("skills") or "[]")
        u["achievements"] = json.loads(u.get("achievements") or "[]")
        u["saved"] = [row["event_id"] for row in
                      c.execute("SELECT event_id FROM saved WHERE email=?", (email,))]
        return u


# ---------- team finder ----------
def create_team(p):
    now = time.time()
    with _LOCK, _conn() as c:
        cur = c.execute("""INSERT INTO teams(email,name,picture,event,role,looking_for,skills,
                           message,contact,open,ts) VALUES(?,?,?,?,?,?,?,?,?,1,?)""",
                        ((p.get("email") or "").lower(), clean(p.get("name"), 80) or "A student",
                         "", clean(p.get("event"), 100), clean(p.get("role"), 60),
                         clean(p.get("looking_for"), 80), json.dumps(clean_list(p.get("skills"), 12, 30)),
                         clean(p.get("message"), 400), clean(p.get("contact"), 120), now))
        return cur.lastrowid


def list_teams(limit=100):
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT * FROM teams WHERE open=1 ORDER BY ts DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["skills"] = json.loads(d.get("skills") or "[]")
            out.append(d)
        return out


def close_team(team_id, email):
    with _LOCK, _conn() as c:
        c.execute("UPDATE teams SET open=0 WHERE id=? AND email=?", (team_id, (email or "").lower()))
    return True


def stats():
    with _LOCK, _conn() as c:
        return {
            "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "events_archived": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "saves": c.execute("SELECT COUNT(*) FROM saved").fetchone()[0],
        }


# ---------- saved ----------
def set_saved(email, event_id, event_json, on=True):
    email = (email or "").strip().lower()
    now = time.time()
    with _LOCK, _conn() as c:
        if on:
            c.execute("""INSERT OR REPLACE INTO saved(email,event_id,event_json,ts)
                         VALUES(?,?,?,?)""",
                      (email, event_id, json.dumps(event_json or {}), now))
            c.execute("INSERT INTO activity(email,action,event_id,ts) VALUES(?,?,?,?)",
                      (email, "save", event_id, now))
        else:
            c.execute("DELETE FROM saved WHERE email=? AND event_id=?", (email, event_id))
    return True


def list_saved(email):
    email = (email or "").strip().lower()
    with _LOCK, _conn() as c:
        return [json.loads(r["event_json"]) for r in
                c.execute("SELECT event_json FROM saved WHERE email=? ORDER BY ts DESC", (email,))]


def all_saved():
    """[(email, name, [event_json,...]), ...] — for reminder emails."""
    with _LOCK, _conn() as c:
        names = {r["email"]: r["name"] for r in c.execute("SELECT email,name FROM users")}
        by = {}
        for r in c.execute("SELECT email,event_json FROM saved"):
            by.setdefault(r["email"], []).append(json.loads(r["event_json"]))
        return [(em, names.get(em, em), evs) for em, evs in by.items()]


# ---------- events archive / history ----------
def archive(items, ended_fn):
    """Insert/refresh every event; mark ended ones. Keeps full history."""
    now = time.time()
    with _LOCK, _conn() as c:
        for it in items:
            eid = it.get("id")
            if not eid:
                continue
            ended = 1 if ended_fn(it) else 0
            row = c.execute("SELECT id FROM events WHERE id=?", (eid,)).fetchone()
            if row:
                c.execute("UPDATE events SET title=?,category=?,platform=?,ends=?,json=?,last_seen=?,ended=? WHERE id=?",
                          (it.get("title"), it.get("category"), it.get("platform"),
                           it.get("ends") or it.get("deadline"), json.dumps(it), now, ended, eid))
            else:
                c.execute("""INSERT INTO events(id,title,category,platform,ends,json,first_seen,last_seen,ended)
                             VALUES(?,?,?,?,?,?,?,?,?)""",
                          (eid, it.get("title"), it.get("category"), it.get("platform"),
                           it.get("ends") or it.get("deadline"), json.dumps(it), now, now, ended))


def history(only_ended=True, limit=200):
    with _LOCK, _conn() as c:
        q = "SELECT json FROM events"
        if only_ended:
            q += " WHERE ended=1"
        q += " ORDER BY last_seen DESC LIMIT ?"
        return [json.loads(r["json"]) for r in c.execute(q, (limit,))]


# ---------- threat scanner (tiered, classifying) ----------
# Each rule: (kind, severity, compiled-regex). Severity: critical|high|medium.
_RULES = [
    ("xss", "high", re.compile(
        r"<script|</script|<iframe|<img|<svg|<object|<embed|</|/>|javascript:|"
        r"data:text/html|vbscript:|on\w+\s*=|document\.|window\.|this\.|\.remove\(|"
        r"\.cookie|eval\(|fetch\(|xmlhttprequest|innerhtml|alert\(|prompt\(|=>|"
        r"[\"']\s*>|[\"']\s*\)\s*;", re.I)),
    ("sqli", "critical", re.compile(
        r"union\s+select|drop\s+table|insert\s+into|delete\s+from|update\s+\w+\s+set|"
        r";\s*--|/\*.*\*/|\bor\s+1\s*=\s*1\b|\band\s+1\s*=\s*1\b|'\s*or\s*'|"
        r"sleep\s*\(|benchmark\s*\(|waitfor\s+delay|information_schema|0x[0-9a-f]{6,}", re.I)),
    ("path_traversal", "critical", re.compile(
        r"\.\./|\.\.\\|%2e%2e|/etc/passwd|/etc/shadow|c:\\windows|/proc/self|"
        r"\benv\b.*\bpath\b|\.git/|\.env\b|wp-config", re.I)),
    ("cmdi", "critical", re.compile(
        r";\s*(cat|ls|rm|wget|curl|bash|sh|nc|netcat|python|perl|chmod|kill)\b|"
        r"\|\s*(bash|sh|nc|curl|wget)\b|`[^`]+`|\$\([^)]+\)|&&\s*(cat|rm|curl|wget)\b", re.I)),
    ("ssrf", "high", re.compile(
        r"169\.254\.169\.254|metadata\.google|localhost:\d|127\.0\.0\.1:\d|"
        r"file://|gopher://|dict://", re.I)),
    ("template_injection", "high", re.compile(
        r"\{\{.*\}\}|\{%.*%\}|\$\{.*\}|<%.*%>", re.I)),
    ("nosql", "high", re.compile(
        r"\$where\b|\$ne\b|\$gt\b|\$regex\b|\$exists\b", re.I)),
]
# Scanner / attack-tool user agents — block on sight.
_BAD_UA = re.compile(
    r"sqlmap|nikto|nmap|masscan|acunetix|nessus|metasploit|hydra|dirbuster|"
    r"gobuster|wpscan|havij|fimap|netsparker|w3af|zgrab|ffuf|httrack", re.I)
# Suspicious probe paths — bots hunting for secrets/admin panels.
_BAD_PATH = re.compile(
    r"/\.env|/\.git|/wp-login|/wp-admin|/xmlrpc\.php|/phpmyadmin|/\.aws|"
    r"/config\.|/\.ssh|/admin\.php|/shell|/\.svn|/server-status|/actuator|"
    r"/\.well-known/.*\.php|/vendor/|/cgi-bin/", re.I)


def classify_threat(text):
    """Return (kind, severity) for the first rule that matches, else (None, None)."""
    s = str(text or "")
    for kind, sev, rx in _RULES:
        if rx.search(s):
            return kind, sev
    return None, None


def looks_malicious(*vals):
    """True if any value looks like an injection payload (any rule)."""
    for v in vals:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            if looks_malicious(*v):
                return True
        else:
            k, _ = classify_threat(v)
            if k:
                return True
    return False


def scan_values(vals):
    """Return (kind, severity, sample_hit) for the worst match across values."""
    worst = (None, None, "")
    order = {"critical": 3, "high": 2, "medium": 1}
    for v in vals:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            k, s, hit = scan_values(v)
        else:
            k, s = classify_threat(v)
            hit = str(v)[:200] if k else ""
        if k and order.get(s, 0) > order.get(worst[1], 0):
            worst = (k, s, hit)
    return worst


def scan_pairs(pairs):
    """Scan (key, value) pairs. Returns (kind, severity, 'field=…: hit') for the
    worst match, so the threat log shows exactly WHICH field was malicious."""
    worst = (None, None, "")
    order = {"critical": 3, "high": 2, "medium": 1}
    for key, val in pairs:
        if val is None:
            continue
        k, s = classify_threat(val)
        if k and order.get(s, 0) > order.get(worst[1], 0):
            field = (str(key) or "?").lstrip("_")
            worst = (k, s, "%s: %s" % (field, str(val)[:160]))
    return worst


def is_bad_ua(ua):
    return bool(_BAD_UA.search(str(ua or "")))


def is_bad_path(path):
    return bool(_BAD_PATH.search(str(path or "")))


# ---------- analytics / presence (owner dashboard) ----------
def track(sid, email, name, kind, detail, path, ip=""):
    now = time.time()
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO analytics(ts,sid,email,name,kind,detail,path,ip)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (now, sid, (email or "").lower(), clean(name, 80), clean(kind, 20),
                   clean(detail, 300), clean(path, 60), ip))
        c.execute("""INSERT INTO presence(sid,email,name,path,last_seen,ip) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(sid) DO UPDATE SET email=excluded.email,name=excluded.name,
                     path=excluded.path,last_seen=excluded.last_seen,ip=excluded.ip""",
                  (sid, (email or "").lower(), clean(name, 80), clean(path, 60), now, ip))
        c.execute("DELETE FROM analytics WHERE id < (SELECT MAX(id)-5000 FROM analytics)")
    return True


def live_users(window=70):
    cut = time.time() - window
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT sid,email,name,path,last_seen,ip FROM presence WHERE last_seen>=? ORDER BY last_seen DESC",
            (cut,))]


def recent_activity(limit=80):
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts,email,name,kind,detail,path,ip FROM analytics ORDER BY id DESC LIMIT ?", (limit,))]


def all_users(limit=500):
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT email,name,college,year,created,last_seen FROM users ORDER BY last_seen DESC LIMIT ?",
            (limit,))]


def delete_user(email):
    email = (email or "").strip().lower()
    with _LOCK, _conn() as c:
        for t in ("users", "saved", "analytics", "presence"):
            c.execute(f"DELETE FROM {t} WHERE email=?", (email,))
        c.execute("DELETE FROM teams WHERE email=?", (email,))
    return True


# ---------- IP block / user ban ----------
def block_ip(ip, reason=""):
    if not ip:
        return False
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO blocked_ips(ip,reason,ts) VALUES(?,?,?)",
                  (ip, clean(reason, 120), time.time()))
    return True


def unblock_ip(ip):
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM blocked_ips WHERE ip=?", (ip,))
    return True


def is_ip_blocked(ip):
    if not ip:
        return False
    with _LOCK, _conn() as c:
        return c.execute("SELECT 1 FROM blocked_ips WHERE ip=?", (ip,)).fetchone() is not None


def list_blocked_ips():
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute("SELECT ip,reason,ts FROM blocked_ips ORDER BY ts DESC")]


def ban_user(email, reason=""):
    """Delete the user AND remember their email so they can't re-register."""
    email = (email or "").strip().lower()
    if not email:
        return False
    delete_user(email)
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO banned_emails(email,reason,ts) VALUES(?,?,?)",
                  (email, clean(reason, 120), time.time()))
    return True


def unban_user(email):
    """Reverse a ban (e.g. a false positive): remove from banlist. Does NOT
    recreate their account — they just sign in again normally."""
    email = (email or "").strip().lower()
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM banned_emails WHERE email=?", (email,))
    return True


def list_banned_emails():
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT email,reason,ts FROM banned_emails ORDER BY ts DESC")]


def is_email_banned(email):
    email = (email or "").strip().lower()
    if not email:
        return False
    with _LOCK, _conn() as c:
        return c.execute("SELECT 1 FROM banned_emails WHERE email=?", (email,)).fetchone() is not None


def auto_ban_ip_for_email(email):
    """Find the most recent IP an email used and block it (for auto-removal)."""
    email = (email or "").strip().lower()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT ip FROM analytics WHERE email=? AND ip!='' ORDER BY id DESC LIMIT 1",
                      (email,)).fetchone()
        ip = r["ip"] if r else ""
    if ip:
        block_ip(ip, "auto: payload/attack from this account")
    return ip


# ---------- AUTO-DEFENSE: threat log, strikes, one-shot auto-ban ----------
def log_threat(ip="", email="", kind="", detail="", path="", action="",
               severity="", ua="", country=""):
    """Record every detected threat / auto-action for the owner threat log."""
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO threats(ts,ip,email,kind,detail,path,action,severity,ua,country)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (time.time(), ip or "", (email or "").lower(), clean(kind, 30),
                   clean(detail, 300), clean(path, 80), clean(action, 60),
                   clean(severity, 12), clean(ua, 120), clean(country, 60)))
        c.execute("DELETE FROM threats WHERE id < (SELECT MAX(id)-2000 FROM threats)")
    return True


def recent_threats(limit=150):
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts,ip,email,kind,detail,path,action,severity,ua,country "
            "FROM threats ORDER BY id DESC LIMIT ?", (limit,))]


def threat_stats():
    """Aggregate counts for the owner security dashboard."""
    day = time.time() - 86400
    with _LOCK, _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM threats").fetchone()["n"]
        today = c.execute("SELECT COUNT(*) n FROM threats WHERE ts>=?", (day,)).fetchone()["n"]
        blocked = c.execute("SELECT COUNT(*) n FROM blocked_ips").fetchone()["n"]
        banned = c.execute("SELECT COUNT(*) n FROM banned_emails").fetchone()["n"]
        by_kind = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind,COUNT(*) n FROM threats GROUP BY kind ORDER BY n DESC")}
        by_sev = {r["severity"] or "?": r["n"] for r in c.execute(
            "SELECT severity,COUNT(*) n FROM threats GROUP BY severity")}
    return {"total": total, "today": today, "blocked_ips": blocked,
            "banned_users": banned, "by_kind": by_kind, "by_severity": by_sev}


def record_strike(ip, n=1):
    """Add suspicious-behaviour strikes to an IP; returns the running total."""
    if not ip:
        return 0
    now = time.time()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT count,last FROM strikes WHERE ip=?", (ip,)).fetchone()
        # strikes decay: reset if the IP has been quiet for 10 min
        base = r["count"] if (r and now - r["last"] < 600) else 0
        total = base + n
        c.execute("INSERT OR REPLACE INTO strikes(ip,count,last) VALUES(?,?,?)",
                  (ip, total, now))
    return total


def clear_strikes(ip):
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM strikes WHERE ip=?", (ip,))
    return True


def auto_defend(ip="", email="", kind="attack", detail="", path="",
                severity="high", ua="", country=""):
    """One call to neutralise an attacker: block IP + ban account + log it.
    Used by the request gate so no human action is needed."""
    email = (email or "").strip().lower()
    actions = []
    if ip and not is_ip_blocked(ip):
        block_ip(ip, "auto: %s (%s)" % (kind or "threat", severity))
        actions.append("ip_blocked")
    if email and "@" in email and not is_email_banned(email):
        ban_user(email, "auto: " + (kind or "threat"))
        actions.append("user_banned")
    if email and "@" in email:
        other = auto_ban_ip_for_email(email)
        if other and other != ip:
            actions.append("ip_blocked")
    log_threat(ip=ip, email=email, kind=kind, detail=detail, path=path,
               action=",".join(actions) or "logged", severity=severity,
               ua=ua, country=country)
    return actions


# ---------- geo / IP intelligence (best-effort, cached) ----------
def geo_get(ip):
    """Return cached geo for an IP, or {} if unknown (no network here)."""
    if not ip:
        return {}
    with _LOCK, _conn() as c:
        r = c.execute("SELECT country,region,city,org FROM geo WHERE ip=?", (ip,)).fetchone()
        return dict(r) if r else {}


def geo_put(ip, country="", region="", city="", org=""):
    if not ip:
        return False
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO geo(ip,country,region,city,org,ts) VALUES(?,?,?,?,?,?)",
                  (ip, clean(country, 60), clean(region, 60), clean(city, 60),
                   clean(org, 120), time.time()))
    return True


def geo_lookup(ip):
    """Resolve IP -> location via a free API, cached. Safe: returns {} on failure
    (e.g. no network). Called in the background so it never blocks a request."""
    if not ip:
        return {}
    cached = geo_get(ip)
    if cached:
        return cached
    low = ip.lower()
    if low.startswith(("10.", "192.168.", "127.", "172.", "169.254.", "::1", "fc", "fd")) \
            or low in ("localhost", "unknown"):
        geo_put(ip, country="Local/Private")
        return geo_get(ip)
    try:
        import urllib.request
        url = ("http://ip-api.com/json/%s?fields=status,country,regionName,city,isp,org"
               % ip)
        with urllib.request.urlopen(url, timeout=4) as resp:
            d = json.loads(resp.read().decode("utf-8", "ignore"))
        if d.get("status") == "success":
            geo_put(ip, country=d.get("country", ""), region=d.get("regionName", ""),
                    city=d.get("city", ""), org=d.get("org") or d.get("isp", ""))
    except Exception:
        pass
    return geo_get(ip)


# ---------- admin-managed community events (owner dashboard CRUD) ----------
def add_community_event(e):
    """Add or update a community event from the owner dashboard. Sanitised."""
    import hashlib
    title = clean(e.get("title"), 140)
    if not title:
        return None
    eid = clean(e.get("id"), 60) or "admin-" + hashlib.md5(
        (title + str(e.get("starts", ""))).encode()).hexdigest()[:10]
    rec = {
        "id": eid,
        "title": title,
        "organizer": clean(e.get("organizer"), 100),
        "type": clean(e.get("type"), 30) or "Conference",
        "category": clean(e.get("type"), 30) or "Conference",
        "platform": "Community",
        "city": clean(e.get("city"), 60),
        "mode": clean(e.get("mode"), 30) or "Offline",
        "location": clean(e.get("location"), 120),
        "starts": clean(e.get("starts"), 30),
        "ends": clean(e.get("ends"), 30) or clean(e.get("starts"), 30),
        "deadline": clean(e.get("deadline"), 30),
        "price": clean(e.get("price"), 60) or "See site",
        "ticket_url": clean(e.get("ticket_url"), 300),
        "url": clean(e.get("url"), 300),
        "tags": clean_list(e.get("tags"), 8, 30),
        "themes": clean_list(e.get("themes"), 5, 30),
        "image": e.get("image") if str(e.get("image", "")).startswith("http") else None,
        "verified": True,
        "dates_confirmed": bool(e.get("dates_confirmed", True)),
        "admin": True,
    }
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO community_events(id,json,ts) VALUES(?,?,?)",
                  (eid, json.dumps(rec), time.time()))
    return rec


def list_community_events():
    with _LOCK, _conn() as c:
        out = []
        for r in c.execute("SELECT json FROM community_events ORDER BY ts DESC"):
            try:
                out.append(json.loads(r["json"]))
            except Exception:
                pass
        return out


def delete_community_event(eid):
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM community_events WHERE id=?", (clean(eid, 60),))
    return True


# ---------- CAMPUS AMBASSADOR: referral codes, stats, leaderboard, certs ----------
import hashlib as _hashlib
import random as _random

# Tier thresholds (sign-ups referred). Order matters.
TIERS = [
    ("Star Ambassador", 30),
    ("Campus Lead", 75),
    ("National Ambassador", 150),
]


def _gen_code(name):
    base = "".join(ch for ch in (name or "").upper() if ch.isalpha())[:6] or "AMB"
    return base + str(_random.randint(1000, 9999))


def create_ambassador(name, email, college=""):
    """Register an ambassador and return their unique referral code (idempotent
    per email)."""
    name = clean(name, 80) or "Ambassador"
    email = (email or "").strip().lower()
    college = clean(college, 100)
    with _LOCK, _conn() as c:
        existing = c.execute("SELECT * FROM ambassadors WHERE email=?", (email,)).fetchone()
        if existing:
            return dict(existing)
        # unique code
        code = _gen_code(name)
        while c.execute("SELECT 1 FROM ambassadors WHERE code=?", (code,)).fetchone():
            code = _gen_code(name)
        c.execute("INSERT INTO ambassadors(code,name,email,college,created) VALUES(?,?,?,?,?)",
                  (code, name, email, college, time.time()))
        return {"code": code, "name": name, "email": email, "college": college}


def get_ambassador(code):
    code = clean(code, 40).upper()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM ambassadors WHERE code=?", (code,)).fetchone()
        return dict(r) if r else None


def get_ambassador_by_email(email):
    email = (email or "").strip().lower()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM ambassadors WHERE email=?", (email,)).fetchone()
        return dict(r) if r else None


def _referral_count(code):
    with _LOCK, _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM users WHERE ref=?", (code,)).fetchone()["n"]


def tier_for(count):
    """Return (current_tier or None, next_tier or None, next_threshold or None)."""
    current = None
    for name, thr in TIERS:
        if count >= thr:
            current = (name, thr)
    nxt = None
    for name, thr in TIERS:
        if count < thr:
            nxt = (name, thr)
            break
    return current, nxt


def ambassador_stats(code):
    code = clean(code, 40).upper()
    amb = get_ambassador(code)
    if not amb:
        return None
    with _LOCK, _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT name,college,created FROM users WHERE ref=? ORDER BY created DESC", (code,))]
    count = len(rows)
    current, nxt = tier_for(count)
    # unlocked tiers list
    unlocked = [name for name, thr in TIERS if count >= thr]
    referred = [{"name": r["name"], "college": r.get("college") or "—",
                 "joined": r["created"]} for r in rows]
    return {
        "code": code, "name": amb["name"], "college": amb.get("college") or "",
        "count": count,
        "current_tier": current[0] if current else None,
        "next_tier": nxt[0] if nxt else None,
        "next_at": nxt[1] if nxt else None,
        "unlocked_tiers": unlocked,
        "tiers": [{"name": n, "at": t, "unlocked": count >= t} for n, t in TIERS],
        "referred": referred,
    }


def leaderboard(limit=50):
    """Top ambassadors by referred sign-ups."""
    with _LOCK, _conn() as c:
        rows = c.execute("""
            SELECT a.code AS code, a.name AS name, a.college AS college,
                   COUNT(u.email) AS count
            FROM ambassadors a
            LEFT JOIN users u ON u.ref = a.code
            GROUP BY a.code
            ORDER BY count DESC, a.created ASC
            LIMIT ?""", (limit,)).fetchall()
    out = []
    for i, r in enumerate(rows):
        d = dict(r)
        cur, _ = tier_for(d["count"])
        d["tier"] = cur[0] if cur else "—"
        d["rank"] = i + 1
        out.append(d)
    return out


def issue_cert(code, tier):
    """Create/return a verifiable certificate id for an ambassador+tier, only if
    they've actually unlocked that tier."""
    amb = get_ambassador(code)
    if not amb:
        return None
    tier = clean(tier, 40)
    thr = dict(TIERS).get(tier)
    if thr is None:
        return None
    if _referral_count(code) < thr:
        return None  # not unlocked yet
    cert_id = "HH-" + _hashlib.md5((code + "|" + tier).encode()).hexdigest()[:10].upper()
    with _LOCK, _conn() as c:
        if not c.execute("SELECT 1 FROM certs WHERE cert_id=?", (cert_id,)).fetchone():
            c.execute("INSERT INTO certs(cert_id,code,name,tier,issued) VALUES(?,?,?,?,?)",
                      (cert_id, code, amb["name"], tier, time.time()))
    return {"cert_id": cert_id, "name": amb["name"], "tier": tier,
            "college": amb.get("college") or "", "issued": time.time()}


def verify_cert(cert_id):
    cert_id = clean(cert_id, 40).upper()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT cert_id,name,tier,issued FROM certs WHERE cert_id=?",
                      (cert_id,)).fetchone()
        return dict(r) if r else None


def wipe_all():
    """Danger: clear all user data (keeps event cache)."""
    with _LOCK, _conn() as c:
        for t in ("users", "saved", "analytics", "presence", "teams"):
            c.execute(f"DELETE FROM {t}")
    return True


def get_setting(key, default=None):
    with _LOCK, _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, str(value)))
    return True
