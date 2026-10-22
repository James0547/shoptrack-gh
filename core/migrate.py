"""
Migration engine — bulletproof against partial retries via
IF NOT EXISTS + OR IGNORE + backup-before-run.

Layers of protection:
  L1: auto-backup of the .db file before ANY migration runs
  L2: ordered migrations, append-only, idempotent
  L3: on failure we do NOT write schema_version → next run retries
  L4: admin backup / export available separately
"""
import os
import shutil
import sqlite3
import logging
from datetime import datetime

from config import DB_PATH, BACKUP_DIR, MIG_LOG_PATH, SCHEMA_TARGET


mig_log = logging.getLogger("shoptrack.migrate")
mig_log.setLevel(logging.INFO)
_fh = logging.FileHandler(MIG_LOG_PATH)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
mig_log.addHandler(_fh)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _get_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not row:
        return 0
    v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return v or 0


def _set_version(conn, version: int, description: str):
    conn.execute(
        "INSERT INTO schema_version (version, applied_at, description) VALUES (?,?,?)",
        (version, _now(), description),
    )


def backup_db(reason: str = "pre-migration"):
    if not os.path.exists(DB_PATH):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"shoptrack_{stamp}_{reason}.db")
    shutil.copy2(DB_PATH, dst)
    mig_log.info(f"Backup created: {dst}")
    return dst


# ── Migrations (append-only, all idempotent) ──────────────────
def migrate_001_initial(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            barcode     TEXT UNIQUE,
            category    TEXT,
            price       REAL NOT NULL CHECK(price >= 0),
            cost        REAL DEFAULT 0,
            stock       INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
            low_stock   INTEGER DEFAULT 5,
            expiry_date TEXT,
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no  TEXT UNIQUE NOT NULL,
            total       REAL NOT NULL,
            payment     TEXT NOT NULL,
            cashier_id  INTEGER,
            created_at  TEXT NOT NULL,
            synced      INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id     INTEGER NOT NULL REFERENCES sales(id),
            product_id  INTEGER NOT NULL REFERENCES products(id),
            qty         INTEGER NOT NULL CHECK(qty > 0),
            unit_price  REAL NOT NULL,
            line_total  REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            direction  TEXT,
            payload    TEXT,
            status     TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_products_name    ON products(name);
        CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);
        CREATE INDEX IF NOT EXISTS idx_sales_created    ON sales(created_at);
        CREATE INDEX IF NOT EXISTS idx_sales_synced     ON sales(synced);
    """)


def migrate_002_settings_seed(conn):
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)",
                 ("shop_name", "My Shop"))
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)",
                 ("currency", "GHS"))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_active_cat "
                 "ON products(is_active, category)")


def migrate_003_users(conn):
    from werkzeug.security import generate_password_hash
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin','cashier')),
            display_name  TEXT NOT NULL,
            must_change_pw INTEGER NOT NULL DEFAULT 1,
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
    """)
    conn.execute(
        """INSERT OR IGNORE INTO users
           (username, password_hash, role, display_name,
            must_change_pw, is_active, created_at, updated_at)
           VALUES (?,?,?,?,1,1,?,?)""",
        ("admin", generate_password_hash("changeme123"),
         "admin", "Administrator", _now(), _now()),
    )


def migrate_004_sync_state(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    for k, v in (
        ("last_pushed_sale_id", "0"),
        ("last_pulled_update_id", "0"),
        ("last_push_at", ""),
        ("last_pull_at", ""),
        ("last_error", ""),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO sync_state(key, value) VALUES (?,?)", (k, v))


def migrate_005_exports_and_daily(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS export_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,
            for_date    TEXT NOT NULL,
            filepath    TEXT,
            status      TEXT NOT NULL,
            detail      TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_export_log_kind_date
            ON export_log(kind, for_date);
    """)
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)",
                 ("daily_report_enabled", "1"))
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)",
                 ("daily_report_to", ""))


MIGRATIONS = [
    (1, "initial schema", migrate_001_initial),
    (2, "settings seed + active/category index", migrate_002_settings_seed),
    (3, "users table + default admin", migrate_003_users),
    (4, "sync state cursors", migrate_004_sync_state),
    (5, "export log + daily report settings", migrate_005_exports_and_daily),
]


def run_migrations(db_path: str = DB_PATH) -> int:
    """
    Runs all pending migrations. Returns final version.
    Autocommit mode — each migration commits its own statements.
    On exception the version row is NOT written, so the next run retries.
    All migrations use IF NOT EXISTS / OR IGNORE → retry-safe.
    """
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    try:
        current = _get_version(conn)
        mig_log.info(f"Migration check: current=v{current}, target=v{SCHEMA_TARGET}")
        if current >= SCHEMA_TARGET:
            mig_log.info("Already up to date.")
            return current

        backup_path = backup_db(f"before_v{current}_to_v{SCHEMA_TARGET}")
        mig_log.info(f"Backup path: {backup_path}")

        for version, desc, fn in MIGRATIONS:
            if version <= current:
                continue
            mig_log.info(f"Applying migration v{version}: {desc}")
            try:
                fn(conn)
                _set_version(conn, version, desc)
                mig_log.info(f"OK v{version}")
            except Exception as e:
                mig_log.error(f"FAILED v{version}: {e}")
                raise RuntimeError(
                    f"Migration v{version} failed: {e}\n"
                    f"Database may be partially migrated. "
                    f"Restore from backup: {backup_path}"
                )

        final = _get_version(conn)
        mig_log.info(f"Migration complete. Now at v{final}.")
        return final
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"Final version: {run_migrations()}")