"""
User data-access. All hashing happens here, never in routes.
"""
import sqlite3
from datetime import datetime
from typing import Optional, List

from werkzeug.security import generate_password_hash, check_password_hash


VALID_ROLES = {"admin", "cashier"}
MIN_PW_LEN = 8


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _validate_pw(pw: str):
    if not isinstance(pw, str) or len(pw) < MIN_PW_LEN:
        raise ValueError(f"Password must be at least {MIN_PW_LEN} characters")


def create_user(conn, *, username: str, password: str, role: str,
                display_name: str) -> int:
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required")
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of {sorted(VALID_ROLES)}")
    _validate_pw(password)
    if not display_name or not display_name.strip():
        raise ValueError("Display name is required")
    try:
        cur = conn.execute(
            """INSERT INTO users
               (username, password_hash, role, display_name,
                must_change_pw, is_active, created_at, updated_at)
               VALUES (?,?,?,?,1,1,?,?)""",
            (username, generate_password_hash(password), role,
             display_name.strip(), _now(), _now()),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' is already taken")


def get_by_username(conn, username: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
        ((username or "").strip(),)).fetchone()


def get_by_id(conn, user_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def verify_login(conn, username: str, password: str) -> Optional[sqlite3.Row]:
    """Returns the user row on success, None on any failure."""
    row = get_by_username(conn, username)
    if not row or not row["is_active"]:
        return None
    if not check_password_hash(row["password_hash"], password or ""):
        return None
    return row


def change_password(conn, user_id: int, new_password: str,
                    clear_must_change: bool = True) -> bool:
    _validate_pw(new_password)
    cur = conn.execute(
        """UPDATE users
           SET password_hash=?, must_change_pw=?, updated_at=?
           WHERE id=?""",
        (generate_password_hash(new_password),
         0 if clear_must_change else 1, _now(), user_id),
    )
    return cur.rowcount > 0


def update_user(conn, user_id: int, *, role: Optional[str] = None,
                display_name: Optional[str] = None,
                is_active: Optional[bool] = None) -> bool:
    sets, args = [], []
    if role is not None:
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of {sorted(VALID_ROLES)}")
        sets.append("role=?"); args.append(role)
    if display_name is not None:
        if not display_name.strip():
            raise ValueError("Display name cannot be empty")
        sets.append("display_name=?"); args.append(display_name.strip())
    if is_active is not None:
        sets.append("is_active=?"); args.append(1 if is_active else 0)
    if not sets:
        return False
    sets.append("updated_at=?"); args.append(_now())
    args.append(user_id)
    cur = conn.execute(
        f"UPDATE users SET {', '.join(sets)} WHERE id=?", args)
    return cur.rowcount > 0


def list_users(conn) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users ORDER BY role, username COLLATE NOCASE").fetchall()


def count_admins(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1"
    ).fetchone()[0]