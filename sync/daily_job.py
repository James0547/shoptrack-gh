"""
20:00 daily WhatsApp report. Idempotent per (date, kind=daily_report).
Runs from the scheduler thread — uses standalone_connect().
"""
import logging
from datetime import date, datetime
import sqlite3

from core.db import standalone_connect
from sync import whatsapp_send as wa
import config as cfg


log = logging.getLogger("shoptrack.daily")


def _already_sent_today(conn, day: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM export_log WHERE kind='daily_report' "
        "AND for_date=? AND status='ok' LIMIT 1", (day,)).fetchone() is not None


def _log(conn, kind, day, filepath, status, detail=""):
    conn.execute(
        """INSERT INTO export_log(kind, for_date, filepath, status, detail, created_at)
           VALUES (?,?,?,?,?,?)""",
        (kind, day, filepath, status, detail[:500],
         datetime.utcnow().isoformat(timespec="seconds")))


def run_daily_report(force: bool = False) -> dict:
    """
    Returns {sent: bool, reason: str}. Never raises on send failure — logs instead.
    force=True bypasses the 'already sent today' check (used by admin button).
    """
    conn = standalone_connect()
    try:
        enabled = conn.execute(
            "SELECT value FROM settings WHERE key='daily_report_enabled'").fetchone()
        if not enabled or enabled["value"] != "1":
            return {"sent": False, "reason": "disabled"}

        to = conn.execute(
            "SELECT value FROM settings WHERE key='daily_report_to'").fetchone()
        to_number = (to["value"] if to else "").strip()
        if not to_number:
            return {"sent": False, "reason": "no recipient configured"}

        today = date.today().isoformat()
        if not force and _already_sent_today(conn, today):
            return {"sent": False, "reason": "already sent today"}

        shop = conn.execute(
            "SELECT value FROM settings WHERE key='shop_name'").fetchone()
        shop_name = shop["value"] if shop else "My Shop"

        try:
            body = wa.build_daily_report(conn, shop_name)
            wa.send_text(to_number, body)
            _log(conn, "daily_report", today, None, "ok", f"sent to {to_number}")
            log.info(f"Daily report sent to {to_number}")
            return {"sent": True, "reason": "ok"}
        except Exception as e:
            _log(conn, "daily_report", today, None, "error", str(e))
            log.error(f"Daily report failed: {e}")
            return {"sent": False, "reason": str(e)}
    finally:
        conn.close()