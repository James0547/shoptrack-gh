"""
ReportLab PDFs. All offline, all local SQLite. No network calls.
"""
import io
from datetime import date
from typing import Dict, Any, List
import sqlite3

from reportlab.lib.pagesizes import A4, mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm as u
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
)
import os

import config as cfg


_RECEIPT_W = 58 * mm
_STYLES = getSampleStyleSheet()
_STYLES.add(ParagraphStyle(
    name="Small", parent=_STYLES["BodyText"], fontSize=8, leading=10))
_STYLES.add(ParagraphStyle(
    name="Tiny", parent=_STYLES["BodyText"], fontSize=7, leading=9))


def _logo_flowable(max_w=30 * mm):
    if not os.path.exists(cfg.LOGO_PATH):
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(cfg.LOGO_PATH) as im:
            w, h = im.size
        ratio = h / w
        return Image(cfg.LOGO_PATH, width=max_w, height=max_w * ratio)
    except Exception:
        return Image(cfg.LOGO_PATH, width=max_w, height=max_w)


# ── Receipt PDF (58 mm) ───────────────────────────────────────
def receipt_pdf(conn, receipt_no: str) -> bytes:
    from core import receipt as rmod
    r = rmod.fetch_receipt(conn, receipt_no)
    if not r:
        raise ValueError(f"Receipt not found: {receipt_no}")
    shop = conn.execute(
        "SELECT value FROM settings WHERE key='shop_name'").fetchone()
    shop_name = shop["value"] if shop else "My Shop"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=(_RECEIPT_W, 200 * mm),
        leftMargin=3 * mm, rightMargin=3 * mm,
        topMargin=3 * mm, bottomMargin=3 * mm,
        title=f"Receipt {receipt_no}")

    story = []
    logo = _logo_flowable(20 * mm)
    if logo:
        story.append(logo)
    story.append(Paragraph(f"<b>{shop_name}</b>", _STYLES["Small"]))
    story.append(Paragraph(receipt_no, _STYLES["Tiny"]))
    story.append(Paragraph(r["created_at"], _STYLES["Tiny"]))
    story.append(Spacer(1, 3 * mm))

    data = [["Item", "Qty", "Amt"]]
    for it in r["items"]:
        data.append([Paragraph(it["name"], _STYLES["Tiny"]),
                     str(it["qty"]),
                     f"{it['line_total']:.2f}"])
    t = Table(data, colWidths=[32 * mm, 8 * mm, 12 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(t)
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(f"<b>TOTAL: GHS {r['total']:.2f}</b>", _STYLES["Small"]))
    story.append(Paragraph(f"Payment: {r['payment'].upper()}", _STYLES["Tiny"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Thank you — come again", _STYLES["Tiny"]))

    doc.build(story)
    return buf.getvalue()


# ── Daily report PDF (A4) ─────────────────────────────────────
def daily_pdf(conn, for_date: str = None) -> bytes:
    d = for_date or date.today().isoformat()
    head = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total
           FROM sales WHERE substr(created_at,1,10)=?""", (d,)).fetchone()

    by_pay = conn.execute(
        """SELECT payment, COALESCE(SUM(total),0) AS t, COUNT(*) AS n
           FROM sales WHERE substr(created_at,1,10)=?
           GROUP BY payment""", (d,)).fetchall()

    items = conn.execute(
        """SELECT p.name, SUM(si.qty) AS qty,
                  SUM(si.line_total) AS revenue
           FROM sale_items si
           JOIN sales s ON s.id = si.sale_id
           JOIN products p ON p.id = si.product_id
           WHERE substr(s.created_at,1,10)=?
           GROUP BY p.id ORDER BY revenue DESC""", (d,)).fetchall()

    low = conn.execute(
        """SELECT name, stock, low_stock FROM products
           WHERE is_active=1 AND stock <= low_stock
           ORDER BY stock ASC""").fetchall()

    exp = conn.execute(
        """SELECT name, expiry_date FROM products
           WHERE is_active=1 AND expiry_date IS NOT NULL
             AND expiry_date <= date('now','+7 day')
           ORDER BY expiry_date ASC""").fetchall()

    shop = conn.execute(
        "SELECT value FROM settings WHERE key='shop_name'").fetchone()
    shop_name = shop["value"] if shop else "My Shop"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title=f"Daily Report {d}",
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm)

    story = []
    logo = _logo_flowable(25 * mm)
    if logo:
        story.append(logo)
    story.append(Paragraph(f"<b>{shop_name} — Daily Report {d}</b>",
                           _STYLES["Heading2"]))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        f"Total: <b>GHS {float(head['total']):,.2f}</b> "
        f"({head['n']} sales)", _STYLES["BodyText"]))
    story.append(Spacer(1, 3 * mm))

    # payment breakdown
    pay_rows = [["Payment", "Count", "Total (GHS)"]]
    for r in by_pay:
        pay_rows.append([r["payment"].upper(), str(r["n"]),
                         f"{float(r['t']):,.2f}"])
    story.append(_table(pay_rows, widths=[60 * mm, 30 * mm, 60 * mm]))
    story.append(Spacer(1, 5 * mm))

    # item table
    story.append(Paragraph("<b>Sales by product</b>", _STYLES["Heading3"]))
    rows = [["Product", "Qty", "Revenue (GHS)"]]
    for r in items:
        rows.append([r["name"], str(r["qty"]), f"{float(r['revenue']):,.2f}"])
    if len(rows) == 1:
        story.append(Paragraph("No sales today.", _STYLES["Small"]))
    else:
        story.append(_table(rows, widths=[90 * mm, 25 * mm, 45 * mm]))
    story.append(Spacer(1, 5 * mm))

    # alerts
    if low:
        story.append(Paragraph("<b>Low stock</b>", _STYLES["Heading3"]))
        rows = [["Product", "Stock", "Alert at"]]
        for r in low:
            rows.append([r["name"], str(r["stock"]), str(r["low_stock"])])
        story.append(_table(rows, widths=[100 * mm, 30 * mm, 30 * mm]))
        story.append(Spacer(1, 4 * mm))
    if exp:
        story.append(Paragraph("<b>Expiring ≤ 7 days</b>", _STYLES["Heading3"]))
        rows = [["Product", "Expiry date"]]
        for r in exp:
            rows.append([r["name"], r["expiry_date"]])
        story.append(_table(rows, widths=[120 * mm, 40 * mm]))

    doc.build(story)
    return buf.getvalue()


def _table(rows: List[List[str]], widths=None) -> Table:
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    return t