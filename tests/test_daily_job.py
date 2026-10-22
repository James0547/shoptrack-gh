"""
Daily job drill. Run from project root:
    python -m tests.test_daily_job
"""
import os, sqlite3, tempfile
import config as cfg
from core import migrate as M
from core import models, sales
from sync import daily_job, whatsapp_send as wa


class _Resp:
    def __init__(self, code, data=None):
        self.status_code = code
        self._data = data or {}
        self.text = ""
    def json(self): return self._data


class FakeWA:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail
    def request(self, method, url, headers=None, params=None,
                data=None, files=None, timeout=None):
        import json
        if self.fail:
            return _Resp(500)
        body = json.loads(data)
        self.sent.append((body["to"], body.get("text", {}).get("body")))
        return _Resp(200, {"messages": [{"id": "fake"}]})


def _fresh():
    tmp = tempfile.mkdtemp(prefix="shoptrack_daily_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    v = M.run_migrations(db)
    assert v == cfg.SCHEMA_TARGET
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp, db


def run_drill():
    conn, tmp, db = _fresh()
    cfg.WHATSAPP_TOKEN = "fake"
    cfg.WHATSAPP_PHONE_ID = "123"
    fake = FakeWA()
    wa.set_http(fake)

    # 1. Disabled → returns "disabled"
    conn.execute("UPDATE settings SET value='0' WHERE key='daily_report_enabled'")
    r = daily_job.run_daily_report()
    assert r["sent"] is False and r["reason"] == "disabled"
    print("OK: disabled returns early")

    # 2. Enabled but no recipient → returns "no recipient"
    conn.execute("UPDATE settings SET value='1' WHERE key='daily_report_enabled'")
    conn.execute("UPDATE settings SET value='' WHERE key='daily_report_to'")
    r = daily_job.run_daily_report()
    assert r["sent"] is False and "recipient" in r["reason"]
    print("OK: missing recipient returns early")

    # 3. Enabled with recipient → sends, logs ok
    conn.execute("UPDATE settings SET value='233241234567' WHERE key='daily_report_to'")
    r = daily_job.run_daily_report()
    assert r["sent"] is True, r
    assert fake.sent and fake.sent[-1][0] == "233241234567"
    assert "SHOPTRACK" in fake.sent[-1][1]
    n = conn.execute(
        "SELECT COUNT(*) FROM export_log WHERE kind='daily_report' AND status='ok'"
    ).fetchone()[0]
    assert n == 1
    print("OK: report sent + logged")

    # 4. Idempotent: second call same day does NOT resend
    r = daily_job.run_daily_report()
    assert r["sent"] is False and "already sent" in r["reason"]
    assert len(fake.sent) == 1
    print("OK: second call same day skipped")

    # 5. force=True bypasses
    r = daily_job.run_daily_report(force=True)
    assert r["sent"] is True
    assert len(fake.sent) == 2
    print("OK: force bypasses idempotency")

    # 6. Send failure logged, not raised
    fake.fail = True
    # delete today's 'ok' log so idempotency check doesn't block us
    conn.execute(
        "DELETE FROM export_log WHERE kind='daily_report' AND status='ok'")
    r = daily_job.run_daily_report()
    assert r["sent"] is False and "500" in r["reason"] or "failed" in r["reason"]
    err = conn.execute(
        "SELECT COUNT(*) FROM export_log WHERE kind='daily_report' AND status='error'"
    ).fetchone()[0]
    assert err >= 1
    print("OK: send failure logged as error, not raised")

    conn.close()
    print("\n── ALL DAILY-JOB DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()