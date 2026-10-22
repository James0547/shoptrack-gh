"""
MIGRATION DRILL — proves no data is lost across updates.
Run:  python -m tests.test_migration

Scenario:
  1. Fresh DB → migrate → seed 3 products + 1 sale
  2. Simulate "already up to date" → re-run → no data changed
  3. Simulate a v1.1 migration (adds a column) → data intact
  4. Simulate a BROKEN migration → must rollback, backup exists, data intact
"""
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

import config as config
from core import migrate as M


def _fresh_db_path():
    tmp = tempfile.mkdtemp(prefix="shoptrack_test_")
    return os.path.join(tmp, "shoptrack.db"), tmp


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _seed(conn):
    now = datetime.utcnow().isoformat(timespec="seconds")
    for name, price, stock in [("Indomie Onion 70g", 8.0, 12),
                               ("Frytol 1L", 45.0, 2),
                               ("Milo 400g", 60.0, 30)]:
        conn.execute(
            "INSERT INTO products(name,price,stock,created_at,updated_at) "
            "VALUES (?,?,?,?,?)", (name, price, stock, now, now))
    conn.execute(
        "INSERT INTO sales(receipt_no,total,payment,created_at) VALUES (?,?,?,?)",
        ("RCPT-001", 8.0, "cash", now))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO sale_items(sale_id,product_id,qty,unit_price,line_total) "
        "VALUES (?,?,?,?,?)", (sid, 1, 1, 8.0, 8.0))


def run_drill():
    db_path, tmpdir = _fresh_db_path()
    # monkey-patch config paths so we don't touch the real DB
    M.DB_PATH = db_path
    M.BACKUP_DIR = os.path.join(tmpdir, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)

    print("── Scenario 1: fresh install ──")
    v = M.run_migrations(db_path)
    assert v == 1, f"Expected v1, got {v}"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _seed(conn)
    assert _count(conn, "products") == 3
    assert _count(conn, "sales") == 1
    print("OK: seeded 3 products, 1 sale\n")

    print("── Scenario 2: re-run, no-op ──")
    v = M.run_migrations(db_path)
    assert v == 1
    assert _count(conn, "products") == 3
    print("OK: idempotent, data unchanged\n")

    print("── Scenario 3: real upgrade adds a column ──")
    def migrate_002_add_note(conn):
        conn.execute("ALTER TABLE products ADD COLUMN note TEXT DEFAULT ''")

    M.MIGRATIONS = M.MIGRATIONS + [(2, "add note column", migrate_002_add_note)]
    v = M.run_migrations(db_path)
    assert v == 2
    assert _count(conn, "products") == 3
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(products)")]
    assert "note" in cols
    print("OK: upgrade kept data, added column\n")

    print("── Scenario 4: BROKEN migration must rollback ──")
    def migrate_003_broken(conn):
        conn.execute("ALTER TABLE products ADD COLUMN will_rollback INTEGER")
        conn.execute("THIS IS NOT VALID SQL")

    M.MIGRATIONS = M.MIGRATIONS + [(3, "broken migration", migrate_003_broken)]
    try:
        M.run_migrations(db_path)
        raise AssertionError("Expected RuntimeError from broken migration")
    except RuntimeError as e:
        assert "failed" in str(e).lower()
    # prove rollback: column not present, row counts intact
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(products)")]
    assert "will_rollback" not in cols, "ROLLBACK FAILED"
    assert _count(conn, "products") == 3
    assert _count(conn, "sales") == 1
    # prove a backup exists for the failed attempt
    backups = os.listdir(M.BACKUP_DIR)
    assert any("before_v2_to_v3" in b for b in backups)
    print(f"OK: rollback clean, backup present ({len(backups)} backups)\n")

    conn.close()
    print("── ALL MIGRATION DRILLS PASSED ✅ ──")
    print(f"Temp dir: {tmpdir}  (safe to delete)")


if __name__ == "__main__":
    run_drill()