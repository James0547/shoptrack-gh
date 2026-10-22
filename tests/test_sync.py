"""
Sync drill. Uses an in-memory fake Supabase (dict-backed).
Run from project root:  python -m tests.test_sync
"""
import os, sqlite3, tempfile
from typing import Dict, Any, List

import config as cfg
from core import migrate as M
from core import models, sales
from sync import cloud, engine


# ── Fake HTTP transport ───────────────────────────────────────
class _FakeResp:
    def __init__(self, code: int, data=None):
        self.status_code = code
        self._data = data or []
        self.text = "" if code < 400 else "fake error"
    def json(self):
        return self._data


class FakeCloud:
    """In-memory Supabase REST shim. Only what cloud.py touches."""
    def __init__(self):
        self.sales: Dict[str, Dict[str, Any]] = {}      # receipt_no -> payload
        self.updates: List[Dict[str, Any]] = []         # id asc
        self._next_update_id = 1
        self.fail_next = 0                              # inject N failures

    # Simulate an owner pushing an update (used in pull test)
    def add_update(self, **fields):
        fields["id"] = self._next_update_id
        self._next_update_id += 1
        self.updates.append(fields)

    def request(self, method, url, headers=None, params=None,
                data=None, timeout=None):
        import json as _json
        if self.fail_next > 0:
            self.fail_next -= 1
            return _FakeResp(500)
        if method == "POST" and url.endswith("/rest/v1/sales"):
            batch = _json.loads(data)
            for s in batch:
                self.sales[s["receipt_no"]] = s     # upsert
            return _FakeResp(201)
        if method == "GET" and url.endswith("/rest/v1/product_updates"):
            after = 0
            if params and "id" in params and params["id"].startswith("gt."):
                after = int(params["id"][3:])
            limit = int((params or {}).get("limit", 100))
            hits = [u for u in self.updates if u["id"] > after][:limit]
            return _FakeResp(200, hits)
        return _FakeResp(404)


def _fresh_db():
    tmp = tempfile.mkdtemp(prefix="shoptrack_sync_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    v = M.run_migrations(db)
    assert v == cfg.SCHEMA_TARGET, f"schema v{v} != target v{cfg.SCHEMA_TARGET}"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def _make_sale(conn, product_id, name, price, qty=1, cashier_id=1):
    total = price * qty
    return sales.checkout(
        conn,
        {"items": [{"product_id": product_id, "name": name, "price": price,
                    "qty": qty, "line_total": total}],
         "lines": qty, "subtotal": total},
        payment="cash", cashier_id=cashier_id, amount_paid=total,
    )


def run_drill():
    conn, tmp = _fresh_db()

    # ── set env so cloud.py doesn't complain ──
    cfg.SUPABASE_URL = "https://fake.local"
    cfg.SUPABASE_KEY = "fake-key"

    fake = FakeCloud()
    cloud.set_http(fake)

    # seed
    pid = models.create_product(conn, name="Indomie", price=8, stock=100)
    print("OK: fresh DB, 1 product, cloud is fake")

    # ── 1. Push two unsynced sales ──
    s1 = _make_sale(conn, pid, "Indomie", 8, qty=2)
    s2 = _make_sale(conn, pid, "Indomie", 8, qty=1)
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE synced=0").fetchone()[0] == 2
    r = engine.run_sync(conn)
    assert r["pushed"] == 2, r
    assert r["errors"] == [], r
    assert len(fake.sales) == 2
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE synced=0").fetchone()[0] == 0
    assert conn.execute(
        "SELECT value FROM sync_state WHERE key='last_pushed_sale_id'"
    ).fetchone()[0] == str(s2["sale_id"])
    print("OK: pushed 2 sales, marked synced, cursor advanced")

    # ── 2. Re-run is a no-op ──
    r = engine.run_sync(conn)
    assert r["pushed"] == 0 and r["pulled"] == 0
    assert len(fake.sales) == 2
    print("OK: second sync is a no-op")

    # ── 3. Pull a price change ──
    fake.add_update(type="price", product_id=pid, new_price=9.5)
    r = engine.run_sync(conn)
    assert r["pulled"] == 1 and r["applied"] == 1, r
    assert models.get_product(conn, pid)["price"] == 9.5
    assert conn.execute(
        "SELECT value FROM sync_state WHERE key='last_pulled_update_id'"
    ).fetchone()[0] == "1"
    print("OK: price update pulled and applied")

    # ── 4. Pull does not re-apply old updates ──
    r = engine.run_sync(conn)
    assert r["pulled"] == 0 and r["applied"] == 0
    print("OK: cursor prevents re-applying")

    # ── 5. stock_set, activate, deactivate ──
    fake.add_update(type="stock_set", product_id=pid, new_stock=42)
    fake.add_update(type="deactivate", product_id=pid)
    r = engine.run_sync(conn)
    assert r["applied"] == 2
    assert models.get_product(conn, pid)["stock"] == 42
    assert models.get_product(conn, pid)["is_active"] == 0
    fake.add_update(type="activate", product_id=pid)
    engine.run_sync(conn)
    assert models.get_product(conn, pid)["is_active"] == 1
    print("OK: stock_set / activate / deactivate applied")

    # ── 6. Unknown type: skipped, cursor still advances ──
    fake.add_update(type="mystery_op", product_id=pid)
    r = engine.run_sync(conn)
    assert r["pulled"] == 1 and r["applied"] == 0
    cursor_now = int(conn.execute(
        "SELECT value FROM sync_state WHERE key='last_pulled_update_id'"
    ).fetchone()[0])
    assert cursor_now >= 5, cursor_now
    print("OK: unknown update skipped, cursor advanced (won't wedge queue)")

    # ── 7. Update for a product that doesn't exist locally: skipped, cursor advanced ──
    fake.add_update(type="price", product_id=99999, new_price=1.0)
    r = engine.run_sync(conn)
    assert r["pulled"] == 1 and r["applied"] == 0
    print("OK: missing-product update skipped cleanly")

    # ── 8. Push failure leaves synced=0 (retry next time) ──
    s3 = _make_sale(conn, pid, "Indomie", 9.5, qty=1)
    before_pending = conn.execute("SELECT COUNT(*) FROM sales WHERE synced=0").fetchone()[0]
    assert before_pending == 1
    fake.fail_next = 1                              # next request (push) fails
    r = engine.run_sync(conn)
    assert r["pushed"] == 0
    assert any("500" in e or "failed" in e for e in r["errors"]), r
    after_pending = conn.execute("SELECT COUNT(*) FROM sales WHERE synced=0").fetchone()[0]
    assert after_pending == 1, "sale marked synced on failure!"
    # retry succeeds
    r = engine.run_sync(conn)
    assert r["pushed"] == 1, r
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE synced=0").fetchone()[0] == 0
    print("OK: push failure does not mark sales synced; retry succeeds")

    # ── 9. Upsert semantics: same receipt pushed twice is harmless ──
    before = len(fake.sales)
    fake.sales[s3["receipt_no"]] = {}                # simulate already-on-cloud
    engine.run_sync(conn)                            # no unsynced sales now
    assert len(fake.sales) == before                 # unchanged
    print("OK: cloud upserts by receipt_no (idempotent)")

    # ── 10. Cursor consistency under repeated identical pushes ──
    # Reset last_pushed cursor to 0 → engine will re-push all sales.
    conn.execute("UPDATE sync_state SET value='0' WHERE key='last_pushed_sale_id'")
    conn.execute("UPDATE sales SET synced=0")
    before = dict(fake.sales)
    r = engine.run_sync(conn)
    assert r["pushed"] >= 1
    # Same receipt_no set, since upsert
    assert set(fake.sales.keys()) == set(before.keys())
    print("OK: re-push after cursor reset stays idempotent")

    conn.close()
    print("\n── ALL SYNC DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()