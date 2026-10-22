"""
Search + cart drill. No Flask, no network.
Run from project root:  python -m tests.test_search
"""
import os, sqlite3, tempfile, time

import config as cfg
from core import migrate as M
from core import models, search


def _mk():
    tmp = tempfile.mkdtemp(prefix="shoptrack_search_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    v = M.run_migrations(db)
    assert v == cfg.SCHEMA_TARGET
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def run_drill():
    conn, tmp = _mk()

    # Seed
    models.create_product(conn, name="Indomie Onion 70g", price=8.0,
                          stock=12, barcode="6001", category="Food")
    models.create_product(conn, name="Indomie Chicken 70g", price=8.0,
                          stock=40, barcode="6002", category="Food")
    models.create_product(conn, name="Indomie Instant Cup", price=12.0,
                          stock=5, category="Food")
    models.create_product(conn, name="Frytol 1L", price=45.0,
                          stock=2, category="Oil")
    models.create_product(conn, name="Milo 400g", price=60.0,
                          stock=30, category="Drink")
    print("OK: seeded 5 products")

    # Exact barcode wins
    r = search.search_products(conn, "6001")
    assert r and r[0]["barcode"] == "6001", r
    print("OK: exact barcode ranks first")

    # Prefix match beats contains
    r = search.search_products(conn, "indomie")
    assert r[0]["name"].lower().startswith("indomie"), r
    print("OK: prefix/contains ranking")

    # Category search
    r = search.search_products(conn, "oil")
    assert any(x["name"] == "Frytol 1L" for x in r)
    print("OK: category search")

    # LOW badge
    r = search.search_products(conn, "frytol")
    assert r[0]["low"] is True
    print("OK: low badge")

    # LIKE-wildcard safety — '%' must NOT match all
    r = search.search_products(conn, "%")
    assert r == [], f"% should not match everything, got {len(r)}"
    print("OK: LIKE wildcards escaped")

    # barcode_exact
    b = search.barcode_exact(conn, "6002")
    assert b and b["id"] > 0
    assert search.barcode_exact(conn, "no-such") is None
    print("OK: barcode_exact fast path")

    # top_sellers fallback (no sales yet) returns up to 12 active products
    ts = search.top_sellers(conn, limit=12)
    assert len(ts) == 5
    print("OK: top_sellers fallback")

    # Performance: 5,000 products, query < 100ms
    for i in range(5000):
        models.create_product(conn, name=f"BulkItem {i:04d}", price=1 + i % 100,
                              stock=10, barcode=f"BLK{i:05d}", category="Bulk")
    t0 = time.perf_counter()
    r = search.search_products(conn, "BulkItem 04")
    dt = (time.perf_counter() - t0) * 1000
    assert r, "expected matches"
    assert dt < 100, f"search too slow: {dt:.1f} ms"
    print(f"OK: 5,005 products searched in {dt:.1f} ms (<100ms)")

    conn.close()
    print("\n── ALL SEARCH DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()