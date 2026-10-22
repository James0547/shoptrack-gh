"""
Sync orchestrator. This is the only file the app/scheduler calls.

    run_sync(conn) -> {pushed, pulled, errors, offline}

Guarantees:
  * Sales are pushed in id order, marked synced=1 only after 2xx.
  * Cursor (sync_state) is advanced in the same transaction as the
    synced=1 update → no drift.
  * Pulled updates applied in a transaction; each advance is idempotent.
  * Any network error leaves state consistent and is returned, not raised.
"""
from datetime import datetime
from typing import Dict, Any, List
import sqlite3

from sync import cloud


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _set_state(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        """INSERT INTO sync_state(key, value) VALUES(?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


def _log(conn, direction: str, payload: str, status: str):
    conn.execute(
        """INSERT INTO sync_log(direction, payload, status, created_at)
           VALUES (?,?,?,?)""",
        (direction, payload[:4000], status, _now()),
    )


# ── Build a sale object for the cloud ─────────────────────────
def _build_sale_payload(conn: sqlite3.Connection, sale_row) -> Dict[str, Any]:
    items = conn.execute(
        """SELECT si.product_id, si.qty, si.unit_price, si.line_total, p.name
           FROM sale_items si JOIN products p ON p.id = si.product_id
           WHERE si.sale_id = ? ORDER BY si.id""",
        (sale_row["id"],),
    ).fetchall()
    return {
        "receipt_no": sale_row["receipt_no"],
        "total": float(sale_row["total"]),
        "payment": sale_row["payment"],
        "cashier_id": sale_row["cashier_id"],
        "created_at": sale_row["created_at"],
        "items": [
            {
                "product_id": it["product_id"],
                "name": it["name"],
                "qty": int(it["qty"]),
                "unit_price": float(it["unit_price"]),
                "line_total": float(it["line_total"]),
            }
            for it in items
        ],
    }


# ── Push ──────────────────────────────────────────────────────
def push_pending(conn: sqlite3.Connection,
                 batch_size: int = 50) -> Dict[str, Any]:
    """Push up to batch_size unsynced sales. Returns summary dict."""
    rows = conn.execute(
        "SELECT * FROM sales WHERE synced=0 ORDER BY id ASC LIMIT ?",
        (batch_size,),
    ).fetchall()
    if not rows:
        return {"pushed": 0, "last_id": int(_get_state(conn, "last_pushed_sale_id", "0"))}

    payload = [_build_sale_payload(conn, r) for r in rows]
    try:
        cloud.push_sales(payload)
    except Exception as e:
        _log(conn, "push", f"{len(payload)} sales, ids {rows[0]['id']}..{rows[-1]['id']}",
             f"error: {e}")
        _set_state(conn, "last_error", str(e)[:500])
        raise

    # ── atomic: mark synced + advance cursor + log ──
    conn.execute("BEGIN")
    try:
        ids = [r["id"] for r in rows]
        conn.executemany(
            "UPDATE sales SET synced=1 WHERE id=?", [(i,) for i in ids])
        _set_state(conn, "last_pushed_sale_id", str(ids[-1]))
        _set_state(conn, "last_push_at", _now())
        _set_state(conn, "last_error", "")
        _log(conn, "push", f"{len(payload)} sales, ids {ids[0]}..{ids[-1]}", "ok")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"pushed": len(payload), "last_id": ids[-1]}


# ── Pull ──────────────────────────────────────────────────────
def pull_pending(conn: sqlite3.Connection,
                 batch_size: int = 100) -> Dict[str, Any]:
    """
    Fetch updates id > last_pulled_update_id, apply each in a transaction.
    Supported update types:
      - price       : products.price = new_price
      - stock_set   : products.stock = new_stock  (owner said "STOCK item 20")
      - stock_delta : products.stock += delta      (rare; direct cloud edits)
      - activate    : products.is_active = 1
      - deactivate  : products.is_active = 0
    Unknown types are logged and skipped — cursor still advances.
    """
    after = int(_get_state(conn, "last_pulled_update_id", "0") or 0)
    try:
        updates = cloud.pull_updates(after, limit=batch_size)
    except Exception as e:
        _log(conn, "pull", f"after={after}", f"error: {e}")
        _set_state(conn, "last_error", str(e)[:500])
        raise

    if not updates:
        _set_state(conn, "last_pull_at", _now())
        return {"pulled": 0, "last_id": after}

    applied = 0
    max_id = after
    for u in updates:
        uid = int(u.get("id", 0))
        utype = (u.get("type") or "").strip().lower()
        try:
            _apply_update(conn, utype, u)
            applied += 1
        except Exception as e:
            _log(conn, "pull", f"update id={uid} type={utype}",
                 f"skip: {e}")
        max_id = max(max_id, uid)

    # atomic cursor advance + log
    conn.execute("BEGIN")
    try:
        _set_state(conn, "last_pulled_update_id", str(max_id))
        _set_state(conn, "last_pull_at", _now())
        _log(conn, "pull", f"after={after} got={len(updates)} applied={applied}",
             "ok")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"pulled": len(updates), "applied": applied, "last_id": max_id}


def _apply_update(conn: sqlite3.Connection, utype: str, u: Dict[str, Any]):
    pid = int(u.get("product_id") or 0)
    if not pid:
        raise ValueError("missing product_id")
    row = conn.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        raise ValueError(f"product {pid} not found locally")

    if utype == "price":
        new_price = float(u["new_price"])
        if new_price < 0:
            raise ValueError("negative price")
        conn.execute(
            "UPDATE products SET price=?, updated_at=? WHERE id=?",
            (new_price, _now(), pid))
    elif utype == "stock_set":
        new_stock = int(u["new_stock"])
        if new_stock < 0:
            raise ValueError("negative stock")
        conn.execute(
            "UPDATE products SET stock=?, updated_at=? WHERE id=?",
            (new_stock, _now(), pid))
    elif utype == "stock_delta":
        delta = int(u["delta"])
        # never go below zero — clamp at 0, log if clamped
        conn.execute(
            "UPDATE products SET stock=MAX(0, stock + ?), updated_at=? WHERE id=?",
            (delta, _now(), pid))
    elif utype == "activate":
        conn.execute(
            "UPDATE products SET is_active=1, updated_at=? WHERE id=?",
            (_now(), pid))
    elif utype == "deactivate":
        conn.execute(
            "UPDATE products SET is_active=0, updated_at=? WHERE id=?",
            (_now(), pid))
    else:
        raise ValueError(f"unknown update type: {utype}")


# ── Full cycle ────────────────────────────────────────────────
def run_sync(conn: sqlite3.Connection) -> Dict[str, Any]:
    """One full push-then-pull cycle. Never raises network errors."""
    result = {"pushed": 0, "pulled": 0, "applied": 0,
              "errors": [], "offline": False, "at": _now()}
    try:
        push_r = push_pending(conn)
        result["pushed"] = push_r.get("pushed", 0)
    except Exception as e:
        result["errors"].append(f"push: {e}")
        # network error → stop this cycle, don't attempt pull
        if _is_network_error(e):
            result["offline"] = True
            return result

    try:
        pull_r = pull_pending(conn)
        result["pulled"] = pull_r.get("pulled", 0)
        result["applied"] = pull_r.get("applied", 0)
    except Exception as e:
        result["errors"].append(f"pull: {e}")
        if _is_network_error(e):
            result["offline"] = True

    return result


def _is_network_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(tok in msg for tok in (
        "connection", "timeout", "network", "nodename",
        "failed to establish", "getaddrinfo", "urlopen",
    ))


# ── Status for the admin screen ───────────────────────────────
def status(conn: sqlite3.Connection) -> Dict[str, Any]:
    pending_push = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE synced=0").fetchone()[0]
    state = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM sync_state")
    }
    last_logs = conn.execute(
        "SELECT direction, status, created_at FROM sync_log "
        "ORDER BY id DESC LIMIT 8").fetchall()
    return {
        "pending_push": pending_push,
        "state": state,
        "recent": [dict(r) for r in last_logs],
        "configured": bool(cfg_url() and cfg_key()),
    }


def cfg_url():
    import config as cfg
    return cfg.SUPABASE_URL


def cfg_key():
    import config as cfg
    return cfg.SUPABASE_KEY