"""Run the daily deadline-reminder emails.

Usage:  python reminders.py
Schedule this once a day (cron, Render cron job, or the desktop app's
scheduled tasks). See DEPLOY.md.
"""
import db
import emailer

if __name__ == "__main__":
    db.init()
    emailer.run_reminders()
