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
            kind TEXT, detail TEXT, path TEXT, action TEXT);
        CREATE TABLE IF NOT EXISTS strikes(
            ip TEXT PRIMARY KEY, count INTEGER DEFAULT 0, last REAL);
        """)
        # migrations: add newer columns if missing (older DBs)
        for tbl, col in (("users", "skills TEXT"), ("users", "achievements TEXT"),
                         ("users", "github TEXT"), ("users", "linkedin TEXT"),
                         ("analytics", "ip TEXT"), ("presence", "ip TEXT")):
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
            c.execute("""INSERT INTO users(email,name,picture,college,year,interests,skills,
                         achievements,github,linkedin,created,last_seen)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (email, name, picture, college, year, interests, skills, ach, github, linkedin, now, now))
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


# ---------- threat scanner ----------
_THREAT = re.compile(
    r"<script|</script|<iframe|<img|<svg|</|/>|javascript:|data:text/html|"
    r"on\w+\s*=|document\.|window\.|this\.|\.remove\(|\.cookie|eval\(|fetch\(|"
    r"innerhtml|alert\(|prompt\(|=>|[\"']\s*>|[\"']\s*\)\s*;|"
    r"union\s+select|drop\s+table|insert\s+into|;--|/\*|\bor\s+1=1\b|0x[0-9a-f]{6,}",
    re.IGNORECASE)


def looks_malicious(*vals):
    """True if any value looks like an XSS/SQLi/script-injection payload."""
    for v in vals:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            if looks_malicious(*v):
                return True
        elif _THREAT.search(str(v)):
            return True
    return False


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
def log_threat(ip="", email="", kind="", detail="", path="", action=""):
    """Record every detected threat / auto-action for the owner threat log."""
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO threats(ts,ip,email,kind,detail,path,action)
                     VALUES(?,?,?,?,?,?,?)""",
                  (time.time(), ip or "", (email or "").lower(), clean(kind, 30),
                   clean(detail, 300), clean(path, 80), clean(action, 60)))
        c.execute("DELETE FROM threats WHERE id < (SELECT MAX(id)-2000 FROM threats)")
    return True


def recent_threats(limit=120):
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts,ip,email,kind,detail,path,action FROM threats ORDER BY id DESC LIMIT ?",
            (limit,))]


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


def auto_defend(ip="", email="", kind="attack", detail="", path=""):
    """One call to neutralise an attacker: block IP + ban account + log it.
    Used by the request gate so no human action is needed."""
    email = (email or "").strip().lower()
    actions = []
    if ip and not is_ip_blocked(ip):
        block_ip(ip, "auto: " + (kind or "threat"))
        actions.append("ip_blocked")
    if email and "@" in email and not is_email_banned(email):
        ban_user(email, "auto: " + (kind or "threat"))
        actions.append("user_banned")
    # also pin any other IPs this email has used
    if email and "@" in email:
        other = auto_ban_ip_for_email(email)
        if other and other != ip:
            actions.append("ip_blocked")
    log_threat(ip=ip, email=email, kind=kind, detail=detail, path=path,
               action=",".join(actions) or "logged")
    return actions


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
