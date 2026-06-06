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
import sqlite3
import threading
import time

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
        """)
        # migrations: add newer profile columns if missing (older DBs)
        for col in ("skills TEXT", "achievements TEXT", "github TEXT", "linkedin TEXT"):
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col}")
            except Exception:
                pass


# ---------- users ----------
def upsert_user(u):
    now = time.time()
    email = (u.get("email") or "").strip().lower() or ("guest_" + str(int(now)))
    with _LOCK, _conn() as c:
        row = c.execute("SELECT email FROM users WHERE email=?", (email,)).fetchone()
        interests = json.dumps(u.get("interests") or [])
        skills = json.dumps(u.get("skills") or [])
        ach = json.dumps(u.get("achievements") or [])
        if row:
            c.execute("""UPDATE users SET name=?, picture=?, college=?, year=?, interests=?,
                         skills=?, achievements=?, github=?, linkedin=?, last_seen=? WHERE email=?""",
                      (u.get("name"), u.get("picture", ""), u.get("college", ""), u.get("year", ""),
                       interests, skills, ach, u.get("github", ""), u.get("linkedin", ""), now, email))
        else:
            c.execute("""INSERT INTO users(email,name,picture,college,year,interests,skills,
                         achievements,github,linkedin,created,last_seen)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (email, u.get("name"), u.get("picture", ""), u.get("college", ""), u.get("year", ""),
                       interests, skills, ach, u.get("github", ""), u.get("linkedin", ""), now, now))
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
                        ((p.get("email") or "").lower(), p.get("name"), p.get("picture", ""),
                         p.get("event", ""), p.get("role", ""), p.get("looking_for", ""),
                         json.dumps(p.get("skills") or []), p.get("message", ""),
                         p.get("contact", ""), now))
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


# ---------- analytics / presence (owner dashboard) ----------
def track(sid, email, name, kind, detail, path):
    now = time.time()
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO analytics(ts,sid,email,name,kind,detail,path)
                     VALUES(?,?,?,?,?,?,?)""",
                  (now, sid, (email or "").lower(), name, kind, (detail or "")[:300], path))
        # update presence heartbeat
        c.execute("""INSERT INTO presence(sid,email,name,path,last_seen) VALUES(?,?,?,?,?)
                     ON CONFLICT(sid) DO UPDATE SET email=excluded.email,name=excluded.name,
                     path=excluded.path,last_seen=excluded.last_seen""",
                  (sid, (email or "").lower(), name, path, now))
        # keep analytics table from growing forever
        c.execute("DELETE FROM analytics WHERE id < (SELECT MAX(id)-5000 FROM analytics)")
    return True


def live_users(window=70):
    cut = time.time() - window
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT sid,email,name,path,last_seen FROM presence WHERE last_seen>=? ORDER BY last_seen DESC",
            (cut,))]


def recent_activity(limit=80):
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts,email,name,kind,detail,path FROM analytics ORDER BY id DESC LIMIT ?", (limit,))]


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
