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
                                          "Content-Type": "application/json",
                                          "User-Agent": "HackHunt/1.0 (+https://hackhunt.xyz)",
                                          "Accept": "application/json"})
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


def _pill(text, bg, fg):
    return (f'<span style="display:inline-block;background:{bg};color:{fg};font-size:12px;'
            f'font-weight:700;padding:5px 11px;border-radius:20px;margin:0 6px 7px 0;'
            f'mso-line-height-rule:exactly">{text}</span>')


def _event_card(e):
    title = e.get("title", "Event")
    org = e.get("organizer") or e.get("platform") or ""
    url = e.get("url") or e.get("ticket_url") or APP_URL
    loc = e.get("location") or e.get("city") or e.get("mode") or "Online"
    d = _days_to(e.get("deadline") or e.get("starts"))
    if d == 0:
        pills = _pill("Closes today", "#3a1212", "#ff9a9a")
    elif d is not None and d > 0:
        pills = _pill(f"{d} day{'s' if d != 1 else ''} left", "#3a2a0f", "#ffce8a")
    else:
        pills = _pill("Open now", "#0f3a2a", "#7cffc0")
    if e.get("prize"):
        pills += _pill(str(e.get("prize"))[:28], "#2a240f", "#ffe08a")
    pills += _pill(str(loc)[:24], "#1c2433", "#9fc0ff")
    if e.get("participants"):
        try:
            pills += _pill(f"{int(e['participants']):,} joined", "#241c33", "#cdb6ff")
        except Exception:
            pass
    return (f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
            f'style="margin:0 0 14px"><tr><td style="background:#191926;border:1px solid #2a2a40;'
            f'border-radius:14px;padding:18px 18px 14px">'
            f'<div style="font-size:17px;font-weight:700;color:#ffffff;line-height:1.3">{title}</div>'
            f'<div style="font-size:13px;color:#9aa0bb;margin:4px 0 13px">{org}</div>'
            f'<div>{pills}</div>'
            f'<a href="{url}" style="display:inline-block;margin-top:8px;background:#5b3df5;color:#ffffff;'
            f'font-weight:700;font-size:14px;text-decoration:none;padding:10px 22px;border-radius:10px">'
            f'Apply now &rarr;</a></td></tr></table>')


def _shell(headline, sub, cards, cta_label):
    return (
        f'<div style="background:#07070f;padding:26px 12px;font-family:\'Helvetica Neue\',Arial,sans-serif">'
        f'<table align="center" width="600" cellpadding="0" cellspacing="0" role="presentation" '
        f'style="max-width:600px;margin:0 auto;background:#101019;border-radius:18px;overflow:hidden;border:1px solid #20203a">'
        f'<tr><td style="background:#5b3df5;padding:22px 26px">'
        f'<div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:.3px">Hack'
        f'<span style="color:#9af0dd">Hunt</span></div>'
        f'<div style="font-size:11px;color:#d6ccff;letter-spacing:1.5px;text-transform:uppercase;margin-top:2px">'
        f'All hackathons of India, one place</div></td></tr>'
        f'<tr><td style="padding:26px 26px 8px">'
        f'<div style="font-size:20px;font-weight:800;color:#ffffff;margin:0 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:#9aa0bb;line-height:1.6;margin:0 0 20px">{sub}</div>'
        f'{cards}'
        f'<div style="text-align:center;margin:12px 0 6px">'
        f'<a href="{APP_URL}" style="display:inline-block;background:#19e3c7;color:#04211a;font-weight:800;'
        f'font-size:15px;text-decoration:none;padding:13px 28px;border-radius:12px">{cta_label}</a></div>'
        f'</td></tr>'
        f'<tr><td style="padding:18px 26px;border-top:1px solid #20203a">'
        f'<div style="font-size:12px;color:#6b7390;line-height:1.6">You\'re receiving this because you '
        f'joined HackHunt. Open <a href="{APP_URL}" style="color:#8a7bff;text-decoration:none">hackhunt.xyz</a> '
        f'and tap your profile to manage preferences.</div></td></tr>'
        f'</table></div>')


def _render(name, events):
    cards = "".join(_event_card(e) for e in events)
    first = (name or "there").split(" ")[0]
    return _shell(f"Deadlines closing soon, {first}",
                  f"These saved opportunities close within {REMIND_DAYS} days — don't miss your shot:",
                  cards, "Open HackHunt")


def run_reminders():
    sent = 0
    for email, name, events in db.all_saved():
        due = [e for e in events
               if (_days_to(e.get("deadline") or e.get("starts")) is not None
                   and 0 <= _days_to(e.get("deadline") or e.get("starts")) <= REMIND_DAYS)]
        if not due or not email or "@" not in email:
            continue
        due.sort(key=lambda e: _days_to(e.get("deadline") or e.get("starts")))  # soonest first
        subj = f"⏳ {len(due)} hackathon deadline{'s' if len(due) != 1 else ''} closing soon"
        if send_email(email, subj, _render(name, due)):
            sent += 1
    print(f"[reminders] sent {sent} email(s)")
    return sent


def _render_digest(name, events):
    cards = "".join(_event_card(e) for e in events)
    first = (name or "there").split(" ")[0]
    return _shell(f"This week's top hackathons, {first}",
                  "The biggest, most popular opportunities open right now — apply before they close:",
                  cards, "See all on HackHunt")


def run_digest(events):
    """Weekly digest — the most POPULAR upcoming events (by participants) to every user."""
    if not events:
        print("[digest] no events to send")
        return 0
    upcoming = [e for e in events
                if (_days_to(e.get("deadline") or e.get("starts")) is None
                    or _days_to(e.get("deadline") or e.get("starts")) >= 0)]
    # rank by popularity (participants), then soonest deadline as tiebreak
    def _rank(e):
        pop = e.get("participants") or 0
        try:
            pop = int(pop)
        except Exception:
            pop = 0
        return -pop
    top = sorted(upcoming or events, key=_rank)[:8]
    sent = 0
    try:
        users = db.all_users(3000)
    except Exception:
        users = []
    for u in users:
        email = (u.get("email") or "").strip()
        if "@" not in email:
            continue
        if send_email(email, "🚀 Top hackathons this week on HackHunt", _render_digest(u.get("name") or "there", top)):
            sent += 1
    print(f"[digest] sent {sent} email(s)")
    return sent


if __name__ == "__main__":
    run_reminders()
