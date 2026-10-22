"""
Background jobs:
  1. Sync cycle       — every 60 s
  2. Daily WhatsApp   — every day at 20:00 (server local time)
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.db import standalone_connect
from sync import engine, daily_job

log = logging.getLogger("shoptrack.scheduler")

_sched = None


def _sync_tick():
    conn = standalone_connect()
    try:
        r = engine.run_sync(conn)
        if r["pushed"] or r["pulled"] or r["applied"]:
            log.info(f"sync: {r}")
    except Exception as e:
        log.warning(f"sync tick error: {e}")
    finally:
        conn.close()


def _daily_tick():
    try:
        r = daily_job.run_daily_report()
        log.info(f"daily job: {r}")
    except Exception as e:
        log.exception(f"daily tick error: {e}")


def start(app):
    global _sched
    if _sched is not None:
        return
    _sched = BackgroundScheduler(daemon=True)
    _sched.add_job(_sync_tick, "interval", seconds=60,
                   id="shoptrack-sync", max_instances=1, coalesce=True)
    _sched.add_job(_daily_tick, CronTrigger(hour=20, minute=0),
                   id="shoptrack-daily", max_instances=1, coalesce=True)
    _sched.start()
    log.info("scheduler started (sync 60s, daily 20:00)")


def stop():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None