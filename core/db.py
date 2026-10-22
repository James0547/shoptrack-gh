"""
SQLite connection manager.
- WAL mode for crash safety + concurrent read while writing.
- Foreign keys ON.
- One connection per request thread (Flask g pattern).
"""
import sqlite3
import logging
from flask import g

from config import DB_PATH, LOG_PATH


logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("shoptrack.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(e=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def standalone_connect() -> sqlite3.Connection:
    return _connect()


def init_app(app):
    app.teardown_appcontext(close_db)