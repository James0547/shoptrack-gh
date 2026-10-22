"""
Transport to Supabase REST API.

Two responsibilities only:
  1. push_sales(batch)     -> POST sale objects, expects 2xx
  2. pull_updates(after)   -> GET update objects with id > after

Transport is injectable via `http` module so tests can substitute a fake.
Prod uses `requests`. Nothing else in the codebase talks to the network
for sync.
"""
from typing import List, Dict, Any
import json

import config as cfg


# ── Transport injection point ─────────────────────────────────
try:
    import requests as _default_http   # prod
except ImportError:                     # extremely unlikely — Flask depends on it
    _default_http = None

_http = _default_http


def set_http(http_module):
    """Test seam. Pass any module exposing request(method, url, ...)."""
    global _http
    _http = http_module


# ── Endpoint helpers ──────────────────────────────────────────
def _base() -> str:
    base = (cfg.SUPABASE_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("SUPABASE_URL not configured")
    return base


def _headers() -> Dict[str, str]:
    key = cfg.SUPABASE_KEY or ""
    if not key:
        raise RuntimeError("SUPABASE_KEY not configured")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",  # upsert semantics
    }


# ── Push ──────────────────────────────────────────────────────
def push_sales(sales_batch: List[Dict[str, Any]]) -> None:
    """
    POST a batch of sale objects. Supabase upserts by receipt_no.
    Raises RuntimeError on non-2xx — caller must NOT mark any sale synced.
    """
    if not sales_batch:
        return
    url = f"{_base()}/rest/v1/sales"
    resp = _http.request(
        "POST", url,
        headers=_headers(),
        data=json.dumps(sales_batch),
        timeout=15,
    )
    code = getattr(resp, "status_code", None)
    if code is None or code < 200 or code >= 300:
        body = getattr(resp, "text", "")[:500]
        raise RuntimeError(f"push_sales failed: HTTP {code}: {body}")


# ── Pull ──────────────────────────────────────────────────────
def pull_updates(after_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """
    GET pending updates (price/stock/product/deactivation) with id > after_id.
    Returns a list (possibly empty).
    """
    url = f"{_base()}/rest/v1/product_updates"
    params = {
        "id": f"gt.{int(after_id)}",
        "order": "id.asc",
        "limit": str(int(limit)),
    }
    resp = _http.request(
        "GET", url, headers=_headers(), params=params, timeout=15,
    )
    code = getattr(resp, "status_code", None)
    if code is None or code < 200 or code >= 300:
        body = getattr(resp, "text", "")[:500]
        raise RuntimeError(f"pull_updates failed: HTTP {code}: {body}")
    return resp.json() or []