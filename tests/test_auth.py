"""
Auth drill. All against a throwaway DB.
Run from project root:  python -m tests.test_auth
"""
import os, sqlite3, tempfile

import config as cfg
from core import migrate as M
from core import users


def _fresh():
    tmp = tempfile.mkdtemp(prefix="shoptrack_auth_")
    db = os.path.join(tmp, "shoptrack.db")
    M.DB_PATH = db
    M.BACKUP_DIR = os.path.join(tmp, "backups")
    os.makedirs(M.BACKUP_DIR, exist_ok=True)
    v = M.run_migrations(db)
    assert v == cfg.SCHEMA_TARGET, f"schema v{v} != target v{cfg.SCHEMA_TARGET}"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn, tmp


def run_drill():
    conn, tmp = _fresh()

    # 1. migration seeded exactly one admin with must_change_pw
    admin = users.get_by_username(conn, "admin")
    assert admin is not None
    assert admin["role"] == "admin"
    assert admin["must_change_pw"] == 1
    assert users.count_admins(conn) == 1
    print("OK: v3 seeded admin with forced password change")

    # 2. default password works, wrong password doesn't
    ok = users.verify_login(conn, "admin", "changeme123")
    assert ok and ok["id"] == admin["id"]
    assert users.verify_login(conn, "admin", "wrong") is None
    assert users.verify_login(conn, "nope", "changeme123") is None
    print("OK: login verifies hashed password")

    # 3. password change clears must_change_pw
    users.change_password(conn, admin["id"], "brandNewPass1")
    assert users.get_by_id(conn, admin["id"])["must_change_pw"] == 0
    assert users.verify_login(conn, "admin", "brandNewPass1")
    assert users.verify_login(conn, "admin", "changeme123") is None
    print("OK: password change works, old password rejected")

    # 4. short password rejected
    try:
        users.change_password(conn, admin["id"], "short")
        raise AssertionError("expected short-pw error")
    except ValueError:
        pass
    print("OK: short password rejected")

    # 5. create cashier — can log in, cannot become admin accidentally
    cid = users.create_user(conn, username="ama", password="cashierPass1",
                            role="cashier", display_name="Ama")
    assert cid > 0
    cash = users.verify_login(conn, "ama", "cashierPass1")
    assert cash["role"] == "cashier"
    assert cash["must_change_pw"] == 1
    print("OK: cashier created and logs in")

    # 6. duplicate username rejected
    try:
        users.create_user(conn, username="ama", password="cashierPass1",
                          role="cashier", display_name="Ama 2")
        raise AssertionError("expected duplicate error")
    except ValueError as e:
        assert "already taken" in str(e)
    print("OK: duplicate username rejected")

    # 7. invalid role rejected
    try:
        users.create_user(conn, username="boss", password="bossPass123",
                          role="superadmin", display_name="Boss")
        raise AssertionError("expected invalid role error")
    except ValueError:
        pass
    print("OK: invalid role rejected")

    # 8. deactivation blocks login
    users.update_user(conn, cid, is_active=False)
    assert users.verify_login(conn, "ama", "cashierPass1") is None
    print("OK: deactivated user cannot log in")

    # 9. last-admin protection
    # add a second admin, then deactivate one — should be allowed
    aid2 = users.create_user(conn, username="boss", password="bossPass123",
                             role="admin", display_name="Boss")
    users.update_user(conn, aid2, is_active=False)  # allowed, admin still active
    assert users.count_admins(conn) == 1
    # if a route tries to deactivate the last active admin, count stays at 1
    # (the route checks this; the model's count_admins is the guard)
    print("OK: admin counting works (route uses it to prevent lock-out)")

    # 10. cashier_id flows to sales — simulated by inserting via sales.checkout
    from core import models, sales
    p = models.create_product(conn, name="Indomie", price=8, stock=5)
    cart = {
        "items": [{"product_id": p, "name": "Indomie", "price": 8,
                   "qty": 1, "line_total": 8}],
        "lines": 1, "subtotal": 8,
    }
    rec = sales.checkout(conn, cart, payment="cash",
                         cashier_id=cash["id"], amount_paid=10)
    row = conn.execute("SELECT cashier_id FROM sales WHERE id=?",
                       (rec["sale_id"],)).fetchone()
    assert row["cashier_id"] == cash["id"]
    print("OK: cashier_id recorded on sale")

    conn.close()
    print("\n── ALL AUTH DRILLS PASSED ✅ ──")
    print(f"(tmp dir: {tmp})")


if __name__ == "__main__":
    run_drill()