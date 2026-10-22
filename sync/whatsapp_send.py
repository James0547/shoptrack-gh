"""
WhatsApp Cloud API sender. Injectable http transport — same pattern as
sync/cloud.py so tests can substitute a fake.

Responsibilities:
  * send_text(to, body)          — plain text reply
  * send_daily_report(conn, to)  — formatted report from local DB
  * send_document(to, filename, bytes, caption) — PDF (Step 8 will produce)
"""
import json
from datetime import datetime, date
from typing import Optional, Dict, Any

import config as cfg


try:
    import requests as _default_http
except ImportError:
    _default_http = None

_http = _default_http


def set_http(http_module):
    """Test seam."""
    global _http
    _http = http_module


# ── Low-level send ────────────────────────────────────────────
def _api_url() -> str:
    if not cfg.WHATSAPP_PHONE_ID:
        raise RuntimeError("WHATSAPP_PHONE_ID not configured")
    return f"https://graph.facebook.com/v22.0/{cfg.WHATSAPP_PHONE_ID}/messages"


def _headers() -> Dict[str, str]:
    if not cfg.WHATSAPP_TOKEN:
        raise RuntimeError("WHATSAPP_TOKEN not configured")
    return {
        "Authorization": f"Bearer {cfg.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }


def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = _http.request(
        "POST", _api_url(),
        headers=_headers(),
        data=json.dumps(payload),
        timeout=15,
    )
    code = getattr(resp, "status_code", None)
    if code is None or code < 200 or code >= 300:
        body = getattr(resp, "text", "")[:500]
        raise RuntimeError(f"WhatsApp send failed: HTTP {code}: {body}")
    return resp.json() if hasattr(resp, "json") else {}


def send_text(to_number: str, body: str) -> Dict[str, Any]:
    """to_number = international format without '+' e.g. '233241234567'."""
    return _post({
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    })


def send_document(to_number: str, filename: str,
                  content: bytes, caption: str = "") -> Dict[str, Any]:
    """
    Meta requires media to be uploaded first OR supplied via public URL.
    For v1.0 we use the simple 'link' form later in Step 8; here we
    upload via the media endpoint then send. Kept minimal for the drill.
    """
    # 1. upload
    up_url = f"https://graph.facebook.com/v22.0/{cfg.WHATSAPP_PHONE_ID}/media"
    resp = _http.request(
        "POST", up_url,
        headers={"Authorization": f"Bearer {cfg.WHATSAPP_TOKEN}"},
        files={"file": (filename, content, "application/pdf")},
        data={"messaging_product": "whatsapp"},
        timeout=30,
    )
    if not (200 <= getattr(resp, "status_code", 0) < 300):
        raise RuntimeError(f"Media upload failed: HTTP {resp.status_code}")
    media_id = resp.json().get("id")
    if not media_id:
        raise RuntimeError("Media upload returned no id")
    return _post({
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "document",
        "document": {"id": media_id, "filename": filename,
                     "caption": caption[:1024]},
    })


# ── Daily report builder ──────────────────────────────────────
def build_daily_report(conn, shop_name: str = None) -> str:
    """
    Query today's sales from local SQLite and format the message.
    Called by the scheduler in Step 8's daily job and by the webhook SALES cmd.
    """
    today = date.today().isoformat()
    head = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total
           FROM sales WHERE substr(created_at,1,10) = ?""",
        (today,)).fetchone()

    by_pay = conn.execute(
        """SELECT payment, COALESCE(SUM(total),0) AS t
           FROM sales WHERE substr(created_at,1,10) = ?
           GROUP BY payment""", (today,)).fetchall()

    top = conn.execute(
        """SELECT p.name, SUM(si.qty) AS q
           FROM sale_items si
           JOIN sales s ON s.id = si.sale_id
           JOIN products p ON p.id = si.product_id
           WHERE substr(s.created_at,1,10) = ?
           GROUP BY p.id ORDER BY q DESC LIMIT 1""", (today,)).fetchone()

    low = conn.execute(
        """SELECT name, stock FROM products
           WHERE is_active=1 AND stock <= low_stock
           ORDER BY stock ASC LIMIT 3""").fetchall()

    exp = conn.execute(
        """SELECT name, expiry_date FROM products
           WHERE is_active=1 AND expiry_date IS NOT NULL
             AND expiry_date <= date('now','+7 day')
           ORDER BY expiry_date ASC LIMIT 3""").fetchall()

    name = shop_name or "My Shop"
    lines = [
        f"*SHOPTRACK GH - {today}*",
        f"Shop: {name}",
        f"Total: GHS {float(head['total']):,.2f} ({head['n']} sales)",
    ]
    cash = next((float(r["t"]) for r in by_pay if r["payment"] == "cash"), 0)
    momo = next((float(r["t"]) for r in by_pay if r["payment"] == "momo"), 0)
    card = next((float(r["t"]) for r in by_pay if r["payment"] == "card"), 0)
    lines.append(f"Cash: GHS {cash:,.2f} | MoMo: GHS {momo:,.2f} | Card: GHS {card:,.2f}")
    if top:
        lines.append(f"Top: {top['name']} ({top['q']})")

    if low:
        lines.append("")
        for r in low:
            lines.append(f"⚠️ LOW: {r['name']} ({r['stock']} left)")
    if exp:
        lines.append("")
        for r in exp:
            lines.append(f"⚠️ EXPIRY: {r['name']} ({r['expiry_date']})")

    lines += [
        "",
        "Reply:",
        "PRICE [name] [amount] to change price",
        "STOCK to get full stock",
        "SALES to get report now",
        "PDF to download report",
    ]
    return "\n".join(lines)