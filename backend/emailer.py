"""
HackHunt India — email reminders.

Sends each signed-up student a reminder for their SAVED events whose deadline
falls within the next REMIND_DAYS days.

Config via environment variables (see .env.example):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
  REMIND_DAYS         (default 3)
  APP_URL             (link back, default http://localhost:5050)

Works with any SMTP provider:
  • Gmail  -> host smtp.gmail.com, port 587, user=your address, pass=App Password
  • Resend -> host smtp.resend.com, port 465, user "resend", pass=API key
  • SendGrid -> host smtp.sendgrid.net, port 587, user "apikey", pass=API key

Run manually:   python reminders.py
Schedule daily: cron / the app's scheduled tasks (see DEPLOY.md).
"""

import datetime as dt
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import db

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@hackhunt.local")
REMIND_DAYS = int(os.environ.get("REMIND_DAYS", "3"))
APP_URL = os.environ.get("APP_URL", "http://localhost:5050")
# Resend (HTTPS email API) — works on hosts that block SMTP ports, like Railway.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "HackHunt <onboarding@resend.dev>")

_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _days_to(d):
    if not d:
        return None
    m = _DATE.search(str(d))
    if not m:
        return None
    try:
        return (dt.date(int(m[1]), int(m[2]), int(m[3])) - dt.date.today()).days
    except Exception:
        return None


def _send_resend(to, subject, html):
    """Send via Resend's HTTPS API (port 443 — never blocked by cloud hosts)."""
    import json
    import urllib.request
    body = json.dumps({"from": RESEND_FROM, "to": [to], "subject": subject,
                       "html": html}).encode("utf-8")
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + RESEND_API_KEY,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return True
    except Exception as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]  # type: ignore
        except Exception:
            pass
        print(f"[email] resend failed to {to}: {e} {detail}")
        return False


def send_email(to, subject, html):
    # Prefer Resend (HTTPS) when configured — required on Railway (SMTP ports blocked).
    if RESEND_API_KEY:
        return _send_resend(to, subject, html)
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        print(f"[email] SMTP not configured — would send to {to}: {subject}")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"HackHunt India <{SMTP_FROM}>"
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    try:
        if SMTP_PORT == 465:
            s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        else:
            s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
            s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_FROM, [to], msg.as_string())
        s.quit()
        return True
    except Exception as e:
        print(f"[email] failed to {to}: {e}")
        return False


def _render(name, events):
    rows = ""
    for e in events:
        dl = e.get("deadline") or e.get("starts") or "TBA"
        d = _days_to(e.get("deadline") or e.get("starts"))
        when = "today" if d == 0 else (f"in {d} day{'s' if d != 1 else ''}" if d and d > 0 else dl)
        url = e.get("ticket_url") or e.get("url") or APP_URL
        rows += f"""<tr>
          <td style="padding:10px 0;border-bottom:1px solid #eee">
            <b>{e.get('title','Event')}</b><br>
            <span style="color:#666;font-size:13px">{e.get('organizer','')} · deadline {dl} ({when})</span><br>
            <a href="{url}" style="color:#7c5cff;font-size:13px">Open event →</a>
          </td></tr>"""
    return f"""<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto">
      <h2 style="color:#7c5cff">⏳ Deadlines coming up, {name.split(' ')[0]}!</h2>
      <p style="color:#444">These saved events close within {REMIND_DAYS} days:</p>
      <table style="width:100%;border-collapse:collapse">{rows}</table>
      <p style="margin-top:20px"><a href="{APP_URL}"
        style="background:#7c5cff;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">
        Open HackHunt</a></p>
      <p style="color:#999;font-size:12px;margin-top:24px">
        You're getting this because you saved events on HackHunt India.
        Manage or unsubscribe in your profile.</p>
    </div>"""


def run_reminders():
    sent = 0
    for email, name, events in db.all_saved():
        due = [e for e in events
               if (_days_to(e.get("deadline") or e.get("starts")) is not None
                   and 0 <= _days_to(e.get("deadline") or e.get("starts")) <= REMIND_DAYS)]
        if not due or not email or "@" not in email:
            continue
        if send_email(email, f"⏳ {len(due)} HackHunt deadline(s) within {REMIND_DAYS} days", _render(name, due)):
            sent += 1
    print(f"[reminders] sent {sent} email(s)")
    return sent


def _render_digest(name, events):
    rows = ""
    for e in events:
        dl = e.get("deadline") or e.get("starts") or "TBA"
        d = _days_to(e.get("deadline") or e.get("starts"))
        when = ("closes today" if d == 0 else
                (f"closes in {d} day{'s' if d != 1 else ''}" if (d is not None and d > 0) else "open now"))
        url = e.get("url") or e.get("ticket_url") or APP_URL
        prize = (" · " + str(e.get("prize"))) if e.get("prize") else ""
        rows += f"""<tr><td style="padding:11px 0;border-bottom:1px solid #eee">
            <a href="{url}" style="color:#1a1530;font-weight:600;font-size:15px;text-decoration:none">{e.get('title','Event')}</a><br>
            <span style="color:#666;font-size:13px">{e.get('organizer','')}{prize} · <b style="color:#7c5cff">{when}</b></span>
          </td></tr>"""
    return f"""<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto">
      <h2 style="color:#7c5cff;margin:0 0 4px">🚀 This week's top hackathons, {name.split(' ')[0]}</h2>
      <p style="color:#444;margin:0 0 14px">Fresh opportunities on HackHunt — don't miss the deadlines:</p>
      <table style="width:100%;border-collapse:collapse">{rows}</table>
      <p style="margin:22px 0"><a href="{APP_URL}"
        style="background:linear-gradient(90deg,#7c5cff,#19e3c7);color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none">
        See all on HackHunt</a></p>
      <p style="color:#999;font-size:12px;margin-top:24px">You're getting this weekly digest because you joined HackHunt.
        Open the app and tap your profile to manage preferences.</p>
    </div>"""


def run_digest(events):
    """Weekly digest of the soonest-closing upcoming events to every user."""
    if not events:
        print("[digest] no events to send")
        return 0
    def _key(e):
        d = _days_to(e.get("deadline") or e.get("starts"))
        return d if (d is not None and d >= 0) else 9999
    top = sorted(events, key=_key)[:10]
    sent = 0
    try:
        users = db.all_users(3000)
    except Exception:
        users = []
    for u in users:
        email = (u.get("email") or "").strip()
        if "@" not in email:
            continue
        if send_email(email, "🚀 Top hackathons this week — HackHunt", _render_digest(u.get("name") or "there", top)):
            sent += 1
    print(f"[digest] sent {sent} email(s)")
    return sent


if __name__ == "__main__":
    run_reminders()
