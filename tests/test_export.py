"""
PDF + Excel drill. Run from project root:
    python -m tests.test_export
"""
import os, sqlite3, tempfile, io
import config as cfg
from core import migrate as M
from core import models, sales
from export import pdf_export, excel_export


def _fresh():
    tmp = tempfile.mkdtemp(prefix="shoptrack_export_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    v = M.run_migrations(db)
    assert v == cfg.SCHEMA_TARGET
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def _make_sale(conn, pid, name, price, qty=1):
    total = price * qty
    return sales.checkout(conn, {
        "items": [{"product_id": pid, "name": name, "price": price,
                   "qty": qty, "line_total": total}],
        "lines": qty, "subtotal": total,
    }, payment="cash", amount_paid=total + 10)


def run_drill():
    conn, tmp = _fresh()
    pid = models.create_product(conn, name="Indomie 70g", price=8, stock=10,
                                category="Food")
    models.create_product(conn, name="Frytol 1L", price=45, stock=2,
                          category="Oil")

    # 1. Receipt PDF
    rec = _make_sale(conn, pid, "Indomie 70g", 8, qty=2)
    data = pdf_export.receipt_pdf(conn, rec["receipt_no"])
    assert data[:4] == b"%PDF"
    assert len(data) > 500
    print(f"OK: receipt PDF ({len(data)} bytes)")

    # 2. Daily report PDF
    data = pdf_export.daily_pdf(conn)
    assert data[:4] == b"%PDF" and len(data) > 1000
    print(f"OK: daily PDF ({len(data)} bytes)")

    # 3. Daily XLSX
    data = excel_export.daily_xlsx(conn)
    assert data[:2] == b"PK"                # zip magic
    # validate with openpyxl
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    assert "Sales" in wb.sheetnames
    assert "Items" in wb.sheetnames
    assert "Summary" in wb.sheetnames
    ws = wb["Sales"]
    assert ws.max_row >= 2                   # header + at least 1 sale
    print("OK: daily XLSX opens, 3 sheets, contains sales")

    # 4. Stock XLSX
    data = excel_export.stock_xlsx(conn)
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Stock"]
    assert ws.max_row == 3                   # header + 2 products
    # Frytol row highlighted red (low stock)
    from openpyxl.styles.colors import COLOR_INDEX
    print("OK: stock XLSX lists all products")

    # 5. No-sales day PDF does not crash
    data = pdf_export.daily_pdf(conn, for_date="2020-01-01")
    assert data[:4] == b"%PDF"
    print("OK: daily PDF for empty date works")

    # 6. Missing receipt raises cleanly
    try:
        pdf_export.receipt_pdf(conn, "RCPT-NOPE")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    print("OK: missing receipt raises ValueError")

    conn.close()
    print("\n── ALL EXPORT DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()