"""
Model layer drill. Runs against a THROWAWAY db. No network. No Flask.
Run from project root:  python -m tests.test_models
"""
import os, shutil, sqlite3, tempfile

import config as cfg
from core import migrate as M
from core import models


def run_drill():
    tmp = tempfile.mkdtemp(prefix="shoptrack_models_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)

    # migrate to latest
    v = M.run_migrations(db)
    assert v == cfg.SCHEMA_TARGET, f"expected v{cfg.SCHEMA_TARGET}, got {v}"
    print(f"OK: migrated to v{v}")

    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row

    # ── CRUD ──
    pid = models.create_product(conn, name="Indomie 70g", price=8.0, stock=12,
                                barcode="6001", category="Food")
    assert pid > 0
    assert models.get_product(conn, pid)["name"] == "Indomie 70g"
    assert models.get_by_barcode(conn, "6001")["id"] == pid
    print("OK: create / get / get_by_barcode")

    models.update_product(conn, pid, price=9.5, stock=15)
    p = models.get_product(conn, pid)
    assert p["price"] == 9.5 and p["stock"] == 15
    print("OK: update")

    # ── stock rules ──
    models.adjust_stock(conn, pid, -5)
    assert models.get_product(conn, pid)["stock"] == 10
    try:
        models.adjust_stock(conn, pid, -999)
        raise AssertionError("expected insufficient-stock error")
    except ValueError as e:
        assert "Insufficient" in str(e)
    print("OK: adjust_stock guards negative")

    # ── validation ──
    try:
        models.create_product(conn, name="", price=1)
        raise AssertionError("expected empty-name error")
    except ValueError:
        pass
    try:
        models.create_product(conn, name="Bad", price=-1)
        raise AssertionError("expected negative-price error")
    except ValueError:
        pass
    print("OK: validation rejects bad input")

    # ── low stock / expiry / categories ──
    models.create_product(conn, name="Frytol 1L", price=45, stock=2, category="Oil")
    models.create_product(conn, name="Yoghurt", price=6, stock=30,
                          category="Dairy", expiry_date="2026-10-23")
    low = models.low_stock_products(conn)
    assert any(r["name"] == "Frytol 1L" for r in low)
    exp = models.expiring_products(conn, days=30)
    assert any(r["name"] == "Yoghurt" for r in exp)
    cats = models.list_categories(conn)
    assert set(cats) >= {"Food", "Oil", "Dairy"}
    print("OK: alerts + categories")

    # ── soft delete ──
    models.soft_delete_product(conn, pid)
    assert models.get_product(conn, pid)["is_active"] == 0
    assert all(r["id"] != pid for r in models.list_products(conn))
    print("OK: soft delete hides from active list")

    # ── settings seed (migration v2) ──
    assert models.get_setting(conn, "shop_name") == "My Shop"
    models.set_setting(conn, "shop_name", "Anaji Mart")
    assert models.get_setting(conn, "shop_name") == "Anaji Mart"
    print("OK: settings seeded + updatable")

    conn.close()
    print("\n── ALL MODEL DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()