"""
Receipt helpers. Dict-in, dict-out. HTML rendering lives in
templates/receipt.html; this module just fetches from DB and prepares
printable payloads.
"""
import sqlite3
from typing import Optional, Dict, Any


def fetch_receipt(conn: sqlite3.Connection, receipt_no: str) -> Optional[Dict[str, Any]]:
    head = conn.execute(
        "SELECT * FROM sales WHERE receipt_no=?", (receipt_no,)
    ).fetchone()
    if not head:
        return None

    items = conn.execute(
        """SELECT si.qty, si.unit_price, si.line_total, p.name
           FROM sale_items si JOIN products p ON p.id = si.product_id
           WHERE si.sale_id = ? ORDER BY si.id""", (head["id"],)
    ).fetchall()

    return {
        "receipt_no": head["receipt_no"],
        "created_at": head["created_at"],
        "payment": head["payment"],
        "total": float(head["total"]),
        "items": [
            {
                "name": r["name"],
                "qty": int(r["qty"]),
                "unit_price": float(r["unit_price"]),
                "line_total": float(r["line_total"]),
            }
            for r in items
        ],
    }