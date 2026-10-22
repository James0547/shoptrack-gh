"""
Real auth: hashed passwords, roles, current_user helpers.
Python 3.9-compatible (no `dict | None` syntax).
"""
from functools import wraps
from typing import Optional, Dict
from flask import session, redirect, url_for, request, flash

from core import users
from core.db import get_db


def current_user() -> Optional[Dict]:
    """Session-cached user dict (name, role, id, must_change_pw)."""
    return session.get("user")


def current_user_id() -> Optional[int]:
    u = current_user()
    return u["id"] if u else None


def _load_user(user_id: int) -> Optional[Dict]:
    """Read fresh state from DB — deactivation takes effect immediately."""
    row = users.get_by_id(get_db(), user_id)
    if not row or not row["is_active"]:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "must_change_pw": bool(row["must_change_pw"]),
    }


def do_login(username: str, password: str) -> Dict:
    """Returns the session user dict on success. Raises ValueError otherwise."""
    conn = get_db()
    row = users.verify_login(conn, username, password)
    if not row:
        raise ValueError("Wrong username or password")
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "must_change_pw": bool(row["must_change_pw"]),
    }


def login_required(role: Optional[str] = None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for("login", next=request.path))
            fresh = _load_user(u["id"])
            if not fresh:
                session.clear()
                flash("Your account has been deactivated.", "error")
                return redirect(url_for("login"))
            if fresh != u:
                session["user"] = fresh
            if fresh["must_change_pw"] and request.endpoint not in (
                    "change_password", "logout"):
                return redirect(url_for("change_password"))
            if role and fresh["role"] != role:
                flash("You do not have permission for that page.", "error")
                return redirect(url_for("pos_screen"))
            return fn(*a, **kw)
        return wrapper
    return deco