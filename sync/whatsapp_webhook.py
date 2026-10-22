"""
Meta WhatsApp webhook. Register at:
    https://<your-render-app>.onrender.com/webhook/whatsapp

GET  → verification challenge
POST → command parser: PRICE / STOCK / SALES / PDF

Verification:
  * GET compares hub.verify_token to WHATSAPP_VERIFY_TOKEN (env).
  * POST verifies X-Hub-Signature-256 against WHATSAPP_APP_SECRET (env).
    This is the same HMAC-SHA256 scheme Meta documents [citation:4].
"""
import hmac
import hashlib
import json
import logging
from typing import Optional

from flask import Blueprint, request, jsonify, current_app

import config as cfg
from core.db import standalone_connect
from sync import whatsapp_send as wa


log = logging.getLogger("shoptrack.webhook")
bp = Blueprint("whatsapp_webhook", __name__, url_prefix="/webhook")


# ── Verify token (set on Render env, matches Meta dashboard) ──
def _verify_token() -> str:
    return getattr(cfg, "WHATSAPP_VERIFY_TOKEN", "") or ""


def _app_secret() -> str:
    return getattr(cfg, "WHATSAPP_APP_SECRET", "") or ""


# ── Signature check ───────────────────────────────────────────
def _signature_ok(body: bytes, header: Optional[str]) -> bool:
    secret = _app_secret()
    if not secret:
        # If not configured, reject — never accept unsigned POSTs in prod.
        log.warning("WHATSAPP_APP_SECRET not set — rejecting webhook POST")
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256).hexdigest()
    got = header.split("=", 1)[1]
    return hmac.compare_digest(expected, got)


# ── GET: verify ───────────────────────────────────────────────
@bp.get("/whatsapp")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    if mode == "subscribe" and token and token == _verify_token():
        return challenge, 200
    return "Forbidden", 403


# ── POST: commands ────────────────────────────────────────────
@bp.post("/whatsapp")
def incoming():
    raw = request.get_data(cache=False)
    if not _signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        return "Invalid signature", 403

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return "Bad JSON", 400

    # Meta may batch entries; iterate defensively
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                _handle_message(msg)

    # Always 200 fast — Meta retries on non-2xx
    return "OK", 200


def _handle_message(msg: dict):
    from_number = msg.get("from")
    if not from_number:
        return
    mtype = msg.get("type")
    if mtype != "text":
        wa.send_text(from_number, "Send PRICE, STOCK, SALES or PDF.")
        return
    text = (msg.get("text", {}).get("body") or "").strip()
    if not text:
        return
    log.info(f"WhatsApp cmd from {from_number}: {text[:80]}")
    try:
        _dispatch(from_number, text)
    except Exception as e:
        log.exception(f"command failed: {e}")
        try:
            wa.send_text(from_number, f"Error: {e}")
        except Exception:
            pass


def _dispatch(to: str, text: str):
    parts = text.split()
    cmd = parts[0].upper()

    if cmd == "PRICE":
        _cmd_price(to, parts[1:])
    elif cmd == "STOCK":
        _cmd_stock(to)
    elif cmd == "SALES":
        _cmd_sales(to)
    elif cmd == "PDF":
        _cmd_pdf(to)
    else:
        wa.send_text(to,
            "Commands:\n"
            "PRICE <name> <amount>\n"
            "STOCK\n"
            "SALES\n"
            "PDF")


# ── Individual commands ───────────────────────────────────────
def _cmd_price(to: str, args: list):
    if len(args) < 2:
        wa.send_text(to, "Usage: PRICE <product name> <amount>\n"
                         "Example: PRICE Indomie 9")
        return
    amount_str = args[-1]
    name = " ".join(args[:-1])
    try:
        amount = float(amount_str)
        if amount < 0:
            raise ValueError
    except ValueError:
        wa.send_text(to, f"Invalid amount: {amount_str}")
        return

    # find product locally (server-side, not on shop PC)
    conn = standalone_connect()
    try:
        # webhook runs on Render — no local DB. Use Supabase via sync.cloud.
        # For v1.0, look up by exact name in the cloud mirror table if present;
        # fallback: create a pending update by name and let shop PC resolve.
        # To keep it simple and safe, we look up locally only when co-located.
        # Production: use the cloud lookup route below.
        product_id = _cloud_lookup_product(name)
        if not product_id:
            wa.send_text(to, f"Product not found: {name}")
            return

        # write update row to Supabase product_updates
        _cloud_insert_update({
            "product_id": product_id,
            "type": "price",
            "new_price": amount,
            "source": "whatsapp",
        })
        wa.send_text(to, f"✅ Updated {name} → GHS {amount:.2f}\n"
                         f"Shop PC will sync on next connection.")
    finally:
        conn.close()


def _cmd_stock(to: str):
    # Read from cloud mirror if it exists; otherwise tell owner to use SALES
    # for now (v1.0). Step 8 wires a cloud read route.
    wa.send_text(to, "Stock list will be sent as PDF.\n"
                     "Reply PDF to receive it.")


def _cmd_sales(to: str):
    # On Render there is no local DB. For v1.0, ask owner to trigger from
    # shop PC (Step 8 adds a cloud endpoint). Placeholder keeps contract.
    wa.send_text(to, "Live sales report is generated on the shop PC.\n"
                     "Your next daily report will include it.")


def _cmd_pdf(to: str):
    wa.send_text(to, "PDF generation arrives in Step 8.\n"
                     "Reply SALES for now.")


# ── Cloud helpers (Supabase REST, same style as sync/cloud.py) ─
def _cloud_lookup_product(name: str) -> Optional[int]:
    """
    Look up product_id by name in Supabase. Requires a products mirror
    table — added in Step 8. For now, returns None so the drill can
    prove the flow with a known id via direct _cloud_insert_update.
    """
    import requests
    if not cfg.SUPABASE_URL or not cfg.SUPABASE_KEY:
        return None
    url = f"{cfg.SUPABASE_URL.rstrip('/')}/rest/v1/products"
    params = {"name": f"eq.{name}", "select": "id", "limit": "1"}
    r = requests.get(url, headers={
        "apikey": cfg.SUPABASE_KEY,
        "Authorization": f"Bearer {cfg.SUPABASE_KEY}",
    }, params=params, timeout=10)
    if r.status_code != 200 or not r.json():
        return None
    return int(r.json()[0]["id"])


def _cloud_insert_update(update: dict):
    import requests
    url = f"{cfg.SUPABASE_URL.rstrip('/')}/rest/v1/product_updates"
    r = requests.post(url, headers={
        "apikey": cfg.SUPABASE_KEY,
        "Authorization": f"Bearer {cfg.SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }, json=update, timeout=10)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Supabase insert failed: {r.status_code} {r.text[:200]}")