"""
Sale checkout + receipt numbering.
"""
from datetime import datetime
from typing import Dict, Any
import sqlite3


PAYMENTS = {"cash", "momo", "card"}


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _next_receipt_no_in_tx(conn):
    day = datetime.utcnow().strftime("%Y%m%d")
    row = conn.execute(
        "SELECT receipt_no FROM sales WHERE receipt_no LIKE ? "
        "ORDER BY id DESC LIMIT 1", (f"RCPT-{day}-%",)).fetchone()
    n = 1
    if row:
        try:
            n = int(row["receipt_no"].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            n = 1
    return f"RCPT-{day}-{n:04d}"


def next_receipt_no(conn):
    return _next_receipt_no_in_tx(conn)


# NEW:
from typing import Optional
def checkout(conn, cart_snapshot, payment,
             cashier_id: Optional[int] = None,
             amount_paid: Optional[float] = None):
    payment = (payment or "").lower().strip()
    if payment not in PAYMENTS:
        raise ValueError(f"Invalid payment: {payment}")

    items = cart_snapshot.get("items") or []
    if not items:
        raise ValueError("Cart is empty")

    for it in items:
        row = conn.execute(
            "SELECT id, name, stock, price FROM products "
            "WHERE id=? AND is_active=1", (int(it["product_id"]),)
        ).fetchone()
        if not row:
            raise ValueError(f"Product {it['product_id']} not found")
        if int(row["stock"]) < int(it["qty"]):
            raise ValueError(
                f"Insufficient stock for {row['name']}: "
                f"have {row['stock']}, need {it['qty']}")
        if abs(float(row["price"]) - float(it["price"])) > 0.001:
            raise ValueError(
                f"Price changed for {row['name']} "
                f"(was {it['price']:.2f}, now {row['price']:.2f}). "
                f"Remove and re-add the item.")

    total = round(sum(float(it["line_total"]) for it in items), 2)
    paid = float(amount_paid) if amount_paid is not None else total
    if paid < total - 0.001:
        raise ValueError(f"Amount paid ({paid:.2f}) is less than total ({total:.2f})")
    change = round(paid - total, 2)

    conn.execute("BEGIN")
    try:
        receipt_no = _next_receipt_no_in_tx(conn)
        created_at = _now()

        cur = conn.execute(
            """INSERT INTO sales(receipt_no, total, payment, cashier_id,
                                 created_at, synced)
               VALUES (?,?,?,?,?,0)""",
            (receipt_no, total, payment, cashier_id, created_at))
        sale_id = cur.lastrowid

        for it in items:
            pid = int(it["product_id"])
            qty = int(it["qty"])
            unit_price = float(it["price"])
            line_total = round(unit_price * qty, 2)

            conn.execute(
                """INSERT INTO sale_items
                   (sale_id, product_id, qty, unit_price, line_total)
                   VALUES (?,?,?,?,?)""",
                (sale_id, pid, qty, unit_price, line_total))

            upd = conn.execute(
                """UPDATE products
                   SET stock = stock - ?, updated_at = ?
                   WHERE id = ? AND stock >= ?""",
                (qty, created_at, pid, qty))
            if upd.rowcount != 1:
                raise ValueError(
                    f"Stock race on product {pid}; please retry the sale.")

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "sale_id": sale_id,
        "receipt_no": receipt_no,
        "total": total,
        "payment": payment,
        "amount_paid": paid,
        "change": change,
        "created_at": created_at,
        "cashier_id": cashier_id,
        "items": [
            {"name": it["name"], "qty": int(it["qty"]),
             "unit_price": round(float(it["price"]), 2),
             "line_total": round(float(it["price"]) * int(it["qty"]), 2)}
            for it in items
        ],
    }