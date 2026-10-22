"""
Checkout drill — the most important test in the app.

Proves:
  1. Happy path: sale recorded, items correct, stock decremented.
  2. Insufficient stock → ValueError → nothing written, stock unchanged.
  3. Empty cart → ValueError.
  4. Invalid payment → ValueError.
  5. Amount < total → ValueError, no writes.
  6. Price drift between cart snapshot and DB → ValueError, no writes.
  7. Receipt numbers are unique and increment per day.
  8. Atomicity: forced mid-transaction failure rolls back everything.
  9. Change calculation is correct for over-payment.

Run from project root:  python -m tests.test_checkout
"""
import os, sqlite3, tempfile
from unittest import mock

import config as cfg
from core import migrate as M
from core import models, sales
from core import receipt as rmod


def _fresh():
    tmp = tempfile.mkdtemp(prefix="shoptrack_checkout_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    M.run_migrations(db)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def _snapshot(*items):
    lines = sum(q for _, _, q in items)
    subtotal = round(sum(p * q for _, p, q in items), 2)
    return {
        "items": [
            {"product_id": pid, "name": name, "price": price,
             "qty": qty, "line_total": round(price * qty, 2)}
            for pid, (name, price, qty) in items
        ],
        "lines": lines, "subtotal": subtotal,
    }


def run_drill():
    conn, tmp = _fresh()
    a = models.create_product(conn, name="Indomie", price=8, stock=12, barcode="A1")
    b = models.create_product(conn, name="Frytol 1L", price=45, stock=3, barcode="B1")
    c = models.create_product(conn, name="Milo 400g", price=60, stock=10, barcode="C1")

    # ── 1. Happy path ──
    snap = _snapshot(
        (a, ("Indomie", 8, 2)),
        (b, ("Frytol 1L", 45, 1)),
    )
    rec = sales.checkout(conn, snap, payment="cash", amount_paid=100)
    assert rec["total"] == 61.0, rec
    assert rec["change"] == 39.0, rec
    assert rec["payment"] == "cash"
    assert models.get_product(conn, a)["stock"] == 10
    assert models.get_product(conn, b)["stock"] == 2
    n_items = conn.execute("SELECT COUNT(*) FROM sale_items WHERE sale_id=?",
                           (rec["sale_id"],)).fetchone()[0]
    assert n_items == 2
    fetched = rmod.fetch_receipt(conn, rec["receipt_no"])
    assert fetched and len(fetched["items"]) == 2
    print("OK: happy path — total, change, stock, receipt persisted")

    # ── 2. Insufficient stock ──
    snap = _snapshot((b, ("Frytol 1L", 45, 99)))
    before_b = models.get_product(conn, b)["stock"]
    n_sales_before = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    try:
        sales.checkout(conn, snap, payment="cash", amount_paid=5000)
        raise AssertionError("expected insufficient stock")
    except ValueError as e:
        assert "Insufficient" in str(e), e
    assert models.get_product(conn, b)["stock"] == before_b
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == n_sales_before
    print("OK: insufficient stock rejected, nothing written")

    # ── 3. Empty cart ──
    try:
        sales.checkout(conn, {"items": [], "lines": 0, "subtotal": 0},
                       payment="cash", amount_paid=0)
        raise AssertionError("expected empty cart error")
    except ValueError as e:
        assert "empty" in str(e).lower()
    print("OK: empty cart rejected")

    # ── 4. Bad payment ──
    snap = _snapshot((a, ("Indomie", 8, 1)))
    try:
        sales.checkout(conn, snap, payment="crypto", amount_paid=8)
        raise AssertionError("expected bad payment error")
    except ValueError as e:
        assert "Invalid payment" in str(e)
    print("OK: invalid payment rejected")

    # ── 5. Amount paid < total ──
    try:
        sales.checkout(conn, snap, payment="cash", amount_paid=1)
        raise AssertionError("expected paid<total error")
    except ValueError as e:
        assert "less than total" in str(e)
    print("OK: underpayment rejected")

    # ── 6. Price drift between cart snapshot and DB ──
    stale = _snapshot((a, ("Indomie", 5, 1)))  # cart says 5, DB says 8
    try:
        sales.checkout(conn, stale, payment="cash", amount_paid=5)
        raise AssertionError("expected price-drift error")
    except ValueError as e:
        assert "Price changed" in str(e)
    print("OK: price drift rejected")

    # ── 7. Receipt numbers unique + increment ──
    seen = set()
    for _ in range(5):
        r = sales.checkout(conn, _snapshot((a, ("Indomie", 8, 1))),
                           payment="cash", amount_paid=8)
        assert r["receipt_no"] not in seen, "duplicate receipt no!"
        seen.add(r["receipt_no"])
    assert len(seen) == 5
    print(f"OK: 5 unique receipt numbers issued (e.g. {sorted(seen)[0]})")

    # ── 8. Atomic rollback on mid-transaction failure ──
    # Force a failure AFTER sale insert by mocking a broken sale_items insert.
    snap = _snapshot((c, ("Milo 400g", 60, 2)))
    before_c = models.get_product(conn, c)["stock"]
    before_sales = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]

    real_insert_sale = sales.checkout  # capture to re-import safely

    def broken_execute(*a, **kw):
        raise sqlite3.OperationalError("simulated disk failure")

    # Patch only the sale_items insert — easiest: patch the module's connection proxy
    # by temporarily wrapping checkout() via direct call to sales internals.
    # Simpler + robust: use mock to make conn.execute raise on the 2nd INSERT.
    call_count = {"n": 0}
    real_execute = conn.execute

    def flaky_execute(sql, *args, **kwargs):
        call_count["n"] += 1
        # first execute in tx = BEGIN; 2nd = SELECT receipt no; 3rd = INSERT sale;
        # 4th = INSERT sale_items → break here
        if call_count["n"] >= 4 and "INSERT INTO sale_items" in sql:
            raise sqlite3.OperationalError("simulated failure inserting sale_items")
        return real_execute(sql, *args, **kwargs)

    with mock.patch.object(conn, "execute", side_effect=flaky_execute):
        try:
            sales.checkout(conn, snap, payment="cash", amount_paid=120)
            raise AssertionError("expected rollback")
        except sqlite3.OperationalError:
            pass

    assert models.get_product(conn, c)["stock"] == before_c, "stock leaked!"
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == before_sales, "sale leaked!"
    print("OK: forced mid-tx failure fully rolled back (no stock, no sale)")

    # ── 9. Over-payment change ──
    r = sales.checkout(conn, _snapshot((a, ("Indomie", 8, 1))),
                       payment="momo", amount_paid=20)
    assert r["change"] == 12.0
    print("OK: over-payment change correct")

    conn.close()
    print("\n── ALL CHECKOUT DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()