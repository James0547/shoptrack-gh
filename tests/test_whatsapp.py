"""
WhatsApp webhook + sender drill. Uses a fake http module.
Run from project root:  python -m tests.test_whatsapp
"""
import os, hmac, hashlib, json, sqlite3, tempfile

import config as cfg
from core import migrate as M
from core import models, sales
from sync import whatsapp_send as wa
from sync import whatsapp_webhook as hook


class _Resp:
    def __init__(self, code, data=None, text=""):
        self.status_code = code
        self._data = data or {}
        self.text = text
    def json(self): return self._data


class FakeWA:
    def __init__(self):
        self.sent = []          # (to, body)
        self.uploaded = []
    def request(self, method, url, headers=None, params=None,
                data=None, files=None, timeout=None):
        if method == "POST" and url.endswith("/messages"):
            body = json.loads(data)
            self.sent.append((body["to"], body.get("text", {}).get("body", "<doc>")))
            return _Resp(200, {"messages": [{"id": "fake"}]})
        if method == "POST" and url.endswith("/media"):
            self.uploaded.append(files)
            return _Resp(200, {"id": "media-fake"})
        return _Resp(404, text="unexpected")


def _fresh():
    tmp = tempfile.mkdtemp(prefix="shoptrack_wa_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    M.run_migrations(db)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def run_drill():
    conn, tmp = _fresh()

    # 1. build_daily_report works with no sales
    report = wa.build_daily_report(conn, "Anaji Mart")
    assert "*SHOPTRACK GH*" in report or "*SHOPTRACK GH -" in report
    assert "Anaji Mart" in report
    assert "PRICE [name] [amount]" in report
    print("OK: daily report builds (empty day)")

    # 2. build_daily_report reflects a sale
    pid = models.create_product(conn, name="Indomie", price=8, stock=10)
    sales.checkout(conn, {
        "items": [{"product_id": pid, "name": "Indomie", "price": 8,
                   "qty": 2, "line_total": 16}],
        "lines": 2, "subtotal": 16,
    }, payment="cash", amount_paid=20)
    report = wa.build_daily_report(conn, "Anaji Mart")
    assert "Total: GHS 16.00" in report
    assert "Cash: GHS 16.00" in report
    assert "Top: Indomie (2)" in report
    print("OK: daily report reflects sales, payment split, top seller")

    # 3. sender posts to the right URL (fake)
    cfg.WHATSAPP_TOKEN = "fake-token"
    cfg.WHATSAPP_PHONE_ID = "12345"
    fake = FakeWA()
    wa.set_http(fake)
    wa.send_text("233241234567", "hello")
    assert fake.sent and fake.sent[-1][0] == "233241234567"
    print("OK: send_text posts to fake Cloud API")

    # 4. webhook GET verification
    cfg.WHATSAPP_VERIFY_TOKEN = "vt-123"
    app = __import__("app").create_app()
    client = app.test_client()
    r = client.get("/webhook/whatsapp?hub.mode=subscribe"
                   "&hub.verify_token=vt-123&hub.challenge=CHAL")
    assert r.status_code == 200 and r.data == b"CHAL"
    r = client.get("/webhook/whatsapp?hub.mode=subscribe"
                   "&hub.verify_token=wrong&hub.challenge=CHAL")
    assert r.status_code == 403
    print("OK: GET challenge verified; wrong token rejected")

    # 5. webhook POST rejects unsigned
    r = client.post("/webhook/whatsapp", data=b"{}",
                    content_type="application/json")
    assert r.status_code == 403
    print("OK: unsigned POST rejected")

    # 6. webhook POST with valid signature routes command
    cfg.WHATSAPP_APP_SECRET = "secret-xyz"
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{
            "from": "233241234567",
            "type": "text",
            "text": {"body": "HELP"},
        }]}}]}]
    }
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"secret-xyz", raw, hashlib.sha256).hexdigest()
    fake.sent.clear()
    r = client.post("/webhook/whatsapp", data=raw,
                    content_type="application/json",
                    headers={"X-Hub-Signature-256": sig})
    assert r.status_code == 200
    assert fake.sent and "PRICE" in fake.sent[-1][1]
    print("OK: signed POST routed, help reply sent")

    # 7. PRICE command with no cloud config → politely errors
    fake.sent.clear()
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "PRICE Indomie 9"
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"secret-xyz", raw, hashlib.sha256).hexdigest()
    r = client.post("/webhook/whatsapp", data=raw,
                    content_type="application/json",
                    headers={"X-Hub-Signature-256": sig})
    assert r.status_code == 200
    assert fake.sent and "Product not found" in fake.sent[-1][1]
    print("OK: PRICE with no cloud lookup replies 'not found' gracefully")

    # 8. PRICE validation: bad amount
    fake.sent.clear()
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "PRICE Indomie abc"
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"secret-xyz", raw, hashlib.sha256).hexdigest()
    client.post("/webhook/whatsapp", data=raw,
                content_type="application/json",
                headers={"X-Hub-Signature-256": sig})
    assert fake.sent and "Invalid amount" in fake.sent[-1][1]
    print("OK: PRICE rejects non-numeric amount")

    conn.close()
    print("\n── ALL WHATSAPP DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()