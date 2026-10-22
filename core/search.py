"""
Offline ranked search. Single source of truth used by:
  - /api/search (POS typeahead)
  - barcode scanner Enter handler (via exact barcode path)
  - future mobile lookup

Ranks:
  1. exact barcode match      (highest)
  2. name starts-with query
  3. name contains query
  4. category/barcode contains query
  5. tie-breaker: units sold in last 30 days DESC, then name ASC
"""
import sqlite3
from typing import List, Dict, Any


def _like(q: str) -> str:
    # escape LIKE metacharacters so a cashier typing "%" doesn't match everything
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q}%"


def search_products(conn: sqlite3.Connection, query: str,
                    limit: int = 12) -> List[Dict[str, Any]]:
    """
    Returns ranked list of dicts ready for JSON. Empty query → [].
    Never raises on weird input.
    """
    q = (query or "").strip()
    if not q:
        return []

    like = _like(q)
    prefix = f"{_like_prefix(q)}"
    sql = """
        SELECT p.id, p.name, p.barcode, p.category, p.price, p.stock,
               p.low_stock, p.expiry_date,
               COALESCE((
                   SELECT SUM(si.qty) FROM sale_items si
                   JOIN sales s ON s.id = si.sale_id
                   WHERE si.product_id = p.id
                     AND s.created_at >= date('now','-30 day')
               ), 0) AS sold_30d,
               CASE
                 WHEN p.barcode = :exact                     THEN 0
                 WHEN p.name LIKE :prefix ESCAPE '\\'        THEN 1
                 WHEN p.name LIKE :like    ESCAPE '\\'       THEN 2
                 WHEN p.category LIKE :like ESCAPE '\\'      THEN 3
                 WHEN p.barcode LIKE :like  ESCAPE '\\'      THEN 4
                 ELSE 9
               END AS rank
        FROM products p
        WHERE p.is_active = 1
          AND (
                p.barcode = :exact
             OR p.name     LIKE :like   ESCAPE '\\'
             OR p.category LIKE :like   ESCAPE '\\'
             OR p.barcode  LIKE :like   ESCAPE '\\'
          )
        ORDER BY rank ASC, sold_30d DESC, p.name COLLATE NOCASE ASC
        LIMIT :limit
    """
    rows = conn.execute(sql, {
        "exact": q,
        "prefix": prefix,
        "like": like,
        "limit": limit,
    }).fetchall()

    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "name": r["name"],
            "barcode": r["barcode"],
            "category": r["category"],
            "price": float(r["price"]),
            "stock": int(r["stock"]),
            "low": r["stock"] <= (r["low_stock"] or 5),
            "expiry_date": r["expiry_date"],
            "sold_30d": int(r["sold_30d"] or 0),
        })
    return out


def _like_prefix(q: str) -> str:
    # same escaping, plus % appended — used for name prefix rank
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{q}%"


def barcode_exact(conn: sqlite3.Connection, barcode: str):
    """Fast path used when scanner hits Enter."""
    b = (barcode or "").strip()
    if not b:
        return None
    row = conn.execute(
        "SELECT id, name, price, stock, low_stock, expiry_date "
        "FROM products WHERE barcode=? AND is_active=1", (b,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "name": row["name"], "price": float(row["price"]),
        "stock": int(row["stock"]), "low": row["stock"] <= (row["low_stock"] or 5),
        "expiry_date": row["expiry_date"],
    }


def top_sellers(conn: sqlite3.Connection, days: int = 30, limit: int = 12):
    """Fast-button tiles on POS. Falls back to active products if no sales yet."""
    rows = conn.execute("""
        SELECT p.id, p.name, p.price, p.stock, p.low_stock, p.expiry_date,
               COALESCE(SUM(si.qty),0) AS qty
        FROM products p
        LEFT JOIN sale_items si ON si.product_id = p.id
        LEFT JOIN sales s ON s.id = si.sale_id
             AND s.created_at >= date('now', :win)
        WHERE p.is_active = 1
        GROUP BY p.id
        ORDER BY qty DESC, p.name COLLATE NOCASE ASC
        LIMIT :lim
    """, {"win": f"-{days} day", "lim": limit}).fetchall()

    out = []
    for r in rows:
        out.append({
            "id": r["id"], "name": r["name"], "price": float(r["price"]),
            "stock": int(r["stock"]), "low": r["stock"] <= (r["low_stock"] or 5),
            "expiry_date": r["expiry_date"],
        })
    return out