"""
Product & settings data-access layer.
"""
from datetime import datetime, timedelta
from typing import Optional, List
import sqlite3

from config import DEFAULT_LOW_STOCK, EXPIRY_WARN_DAYS


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def create_product(conn, *, name, price, stock=0, barcode=None,
                   category=None, cost=0.0, low_stock=DEFAULT_LOW_STOCK,
                   expiry_date=None) -> int:
    if not name or not name.strip():
        raise ValueError("Product name is required")
    if price < 0:
        raise ValueError("Price cannot be negative")
    if stock < 0:
        raise ValueError("Stock cannot be negative")
    now = _now()
    cur = conn.execute(
        """INSERT INTO products
           (name, barcode, category, price, cost, stock, low_stock,
            expiry_date, is_active, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
        (name.strip(), barcode or None, category or None,
         float(price), float(cost), int(stock), int(low_stock),
         expiry_date or None, now, now),
    )
    return cur.lastrowid


def get_product(conn, product_id):
    return conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()


def get_by_barcode(conn, barcode):
    return conn.execute("SELECT * FROM products WHERE barcode=?", (barcode,)).fetchone()


def list_products(conn, *, include_inactive=False, category=None,
                  search=None, limit=500):
    sql = "SELECT * FROM products WHERE 1=1"
    args = []
    if not include_inactive:
        sql += " AND is_active=1"
    if category:
        sql += " AND category=?"
        args.append(category)
    if search:
        sql += " AND (name LIKE ? OR barcode LIKE ? OR category LIKE ?)"
        like = f"%{search}%"
        args += [like, like, like]
    sql += " ORDER BY name COLLATE NOCASE LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def update_product(conn, product_id, **fields):
    allowed = {"name", "barcode", "category", "price", "cost",
               "stock", "low_stock", "expiry_date", "is_active"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"Cannot update field: {k}")
        if k == "price" and v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        if k == "stock" and v is not None and v < 0:
            raise ValueError("Stock cannot be negative")
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    args.append(_now())
    args.append(product_id)
    cur = conn.execute(f"UPDATE products SET {', '.join(sets)} WHERE id=?", args)
    return cur.rowcount > 0


def soft_delete_product(conn, product_id):
    cur = conn.execute(
        "UPDATE products SET is_active=0, updated_at=? WHERE id=?",
        (_now(), product_id))
    return cur.rowcount > 0


def adjust_stock(conn, product_id, delta):
    row = get_product(conn, product_id)
    if not row:
        raise ValueError("Product not found")
    new_stock = row["stock"] + delta
    if new_stock < 0:
        raise ValueError(f"Insufficient stock: have {row['stock']}, need {-delta}")
    update_product(conn, product_id, stock=new_stock)
    return new_stock


def low_stock_products(conn):
    return conn.execute(
        """SELECT * FROM products
           WHERE is_active=1 AND stock <= low_stock
           ORDER BY stock ASC""").fetchall()


def expiring_products(conn, days=EXPIRY_WARN_DAYS):
    cutoff = (datetime.utcnow() + timedelta(days=days)).date().isoformat()
    return conn.execute(
        """SELECT * FROM products
           WHERE is_active=1 AND expiry_date IS NOT NULL
             AND expiry_date <= ?
           ORDER BY expiry_date ASC""", (cutoff,)).fetchall()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        """INSERT INTO settings(key, value) VALUES(?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value))


def list_categories(conn):
    rows = conn.execute(
        """SELECT DISTINCT category FROM products
           WHERE category IS NOT NULL AND category != '' AND is_active=1
           ORDER BY category COLLATE NOCASE""").fetchall()
    return [r["category"] for r in rows]