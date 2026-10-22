"""
ShopTrack GH — Flask entry point.
"""
import sys, os
# Ensure local config.py wins over any installed 'config' package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io
from datetime import date

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, flash, send_file,
)

import config as cfg
from core.db import get_db, init_app
from core.migrate import run_migrations
from core import models, users
from core import search as search_mod
from core import cart as cart_mod
from core import sales as sales_mod
from core import receipt as receipt_mod
from core.auth import login_required, do_login, current_user, current_user_id

from export import pdf_export, excel_export
from sync import engine as sync_engine
from sync import daily_job
from sync import scheduler
from sync.whatsapp_webhook import bp as wa_bp


def create_app():
    run_migrations()

    app = Flask(
        __name__,
        static_folder=cfg.STATIC_DIR,
        template_folder="templates",
    )
    app.secret_key = cfg.SECRET_KEY
    init_app(app)

    app.register_blueprint(wa_bp)

    try:
        scheduler.start(app)
    except Exception as e:
        print(f"[warn] scheduler not started: {e}")
        

    # ── Disable browser caching for static files during development ──
    @app.after_request
    def add_header(resp):
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    # ---------- auth ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            return redirect(url_for("pos_screen"))
        if request.method == "POST":
            username = request.form.get("user", "").strip()
            pw = request.form.get("pw", "")
            try:
                session["user"] = do_login(username, pw)
                nxt = request.args.get("next") or url_for("pos_screen")
                return redirect(nxt)
            except ValueError as e:
                flash(str(e), "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required()
    def change_password():
        u = current_user()
        if request.method == "POST":
            cur_pw = request.form.get("current", "")
            new_pw = request.form.get("new", "")
            confirm = request.form.get("confirm", "")
            if new_pw != confirm:
                flash("New passwords do not match", "error")
            elif len(new_pw) < users.MIN_PW_LEN:
                flash(f"New password must be at least {users.MIN_PW_LEN} chars", "error")
            else:
                conn = get_db()
                row = users.verify_login(conn, u["username"], cur_pw)
                if not row:
                    flash("Current password is wrong", "error")
                else:
                    try:
                        users.change_password(conn, u["id"], new_pw)
                        fresh = users.get_by_id(conn, u["id"])
                        session["user"] = {
                            "id": fresh["id"],
                            "username": fresh["username"],
                            "display_name": fresh["display_name"],
                            "role": fresh["role"],
                            "must_change_pw": False,
                        }
                        flash("Password changed", "ok")
                        return redirect(url_for("pos_screen"))
                    except ValueError as e:
                        flash(str(e), "error")
        return render_template("change_password.html")

    # ---------- admin: products ----------
    @app.route("/")
    @app.route("/admin")
    @login_required("admin")
    def admin_dashboard():
        conn = get_db()
        search = request.args.get("q", "").strip() or None
        category = request.args.get("cat") or None
        return render_template(
            "admin.html",
            products=models.list_products(conn, search=search, category=category),
            low=models.low_stock_products(conn),
            exp=models.expiring_products(conn),
            categories=models.list_categories(conn),
            search=search or "",
            active_cat=category or "",
        )

    @app.post("/admin/product/new")
    @login_required("admin")
    def admin_product_new():
        conn = get_db()
        try:
            models.create_product(
                conn,
                name=request.form["name"],
                price=float(request.form.get("price") or 0),
                stock=int(request.form.get("stock") or 0),
                barcode=request.form.get("barcode") or None,
                category=request.form.get("category") or None,
                cost=float(request.form.get("cost") or 0),
                low_stock=int(request.form.get("low_stock") or cfg.DEFAULT_LOW_STOCK),
                expiry_date=request.form.get("expiry_date") or None,
            )
            flash("Product added", "ok")
        except Exception as e:
            flash(f"Could not add: {e}", "error")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/product/<int:pid>/edit")
    @login_required("admin")
    def admin_product_edit(pid):
        conn = get_db()
        try:
            models.update_product(
                conn, pid,
                name=request.form["name"],
                price=float(request.form.get("price") or 0),
                stock=int(request.form.get("stock") or 0),
                barcode=request.form.get("barcode") or None,
                category=request.form.get("category") or None,
                cost=float(request.form.get("cost") or 0),
                low_stock=int(request.form.get("low_stock") or cfg.DEFAULT_LOW_STOCK),
                expiry_date=request.form.get("expiry_date") or None,
            )
            flash("Product updated", "ok")
        except Exception as e:
            flash(f"Could not update: {e}", "error")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/product/<int:pid>/delete")
    @login_required("admin")
    def admin_product_delete(pid):
        conn = get_db()
        try:
            models.soft_delete_product(conn, pid)
            flash("Product deactivated", "ok")
        except Exception as e:
            flash(f"Could not deactivate: {e}", "error")
        return redirect(url_for("admin_dashboard"))

    # ---------- admin: users ----------
    @app.get("/admin/users")
    @login_required("admin")
    def admin_users():
        conn = get_db()
        return render_template("admin_users.html", users=users.list_users(conn))

    @app.post("/admin/users/new")
    @login_required("admin")
    def admin_users_new():
        conn = get_db()
        try:
            users.create_user(
                conn,
                username=request.form["username"],
                password=request.form["password"],
                role=request.form["role"],
                display_name=request.form["display_name"],
            )
            flash("User created. They must change password on first login.", "ok")
        except Exception as e:
            flash(str(e), "error")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:uid>/reset")
    @login_required("admin")
    def admin_users_reset(uid):
        conn = get_db()
        target = users.get_by_id(conn, uid)
        if not target:
            flash("User not found", "error")
            return redirect(url_for("admin_users"))
        try:
            users.change_password(conn, uid, request.form["password"],
                                  clear_must_change=False)
            flash(f"Password reset for {target['username']}.", "ok")
        except Exception as e:
            flash(str(e), "error")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:uid>/toggle")
    @login_required("admin")
    def admin_users_toggle(uid):
        conn = get_db()
        target = users.get_by_id(conn, uid)
        if not target:
            flash("User not found", "error")
            return redirect(url_for("admin_users"))
        if target["role"] == "admin" and target["is_active"]:
            if users.count_admins(conn) <= 1:
                flash("Cannot deactivate the last admin.", "error")
                return redirect(url_for("admin_users"))
        users.update_user(conn, uid, is_active=not bool(target["is_active"]))
        flash(f"{target['username']} updated", "ok")
        return redirect(url_for("admin_users"))

    # ---------- POS + search ----------
    @app.get("/api/search")
    @login_required()
    def api_search():
        return jsonify(results=search_mod.search_products(
            get_db(), request.args.get("q", ""), limit=12))

    @app.get("/api/barcode/<path:barcode>")
    @login_required()
    def api_barcode(barcode):
        hit = search_mod.barcode_exact(get_db(), barcode)
        if not hit:
            return jsonify(found=False), 404
        return jsonify(found=True, product=hit)

    @app.get("/api/top-sellers")
    @login_required()
    def api_top_sellers():
        return jsonify(results=search_mod.top_sellers(get_db(), limit=12))

    @app.get("/pos")
    @login_required()
    def pos_screen():
        conn = get_db()
        return render_template(
            "pos.html",
            top=search_mod.top_sellers(conn, limit=12),
            receipt_preview=sales_mod.next_receipt_no(conn),
        )

    # ---------- cart ----------
    @app.get("/api/cart")
    @login_required()
    def api_cart_get():
        return jsonify(cart=cart_mod.snapshot())

    @app.post("/api/cart/add")
    @login_required()
    def api_cart_add():
        body = request.get_json(silent=True) or {}
        pid = int(body.get("product_id") or 0)
        qty = int(body.get("qty") or 1)
        conn = get_db()
        row = models.get_product(conn, pid)
        if not row or not row["is_active"]:
            return jsonify(ok=False, error="Product not found"), 404
        try:
            cart_mod.add(
                {"id": row["id"], "name": row["name"],
                 "price": float(row["price"]), "stock": int(row["stock"])},
                qty=qty,
            )
            return jsonify(ok=True, cart=cart_mod.snapshot())
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400

    @app.post("/api/cart/set")
    @login_required()
    def api_cart_set():
        body = request.get_json(silent=True) or {}
        pid = int(body.get("product_id") or 0)
        qty = int(body.get("qty") or 0)
        conn = get_db()
        row = models.get_product(conn, pid)
        if not row:
            return jsonify(ok=False, error="Product not found"), 404
        try:
            cart_mod.set_qty(pid, qty, fresh_stock=int(row["stock"]))
            return jsonify(ok=True, cart=cart_mod.snapshot())
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400

    @app.post("/api/cart/remove")
    @login_required()
    def api_cart_remove():
        body = request.get_json(silent=True) or {}
        cart_mod.remove(int(body.get("product_id") or 0))
        return jsonify(ok=True, cart=cart_mod.snapshot())

    @app.post("/api/cart/clear")
    @login_required()
    def api_cart_clear():
        cart_mod.clear()
        return jsonify(ok=True, cart=cart_mod.snapshot())

    # ---------- checkout + receipt ----------
    @app.post("/api/checkout")
    @login_required()
    def api_checkout():
        body = request.get_json(silent=True) or {}
        payment = (body.get("payment") or "").strip().lower()
        try:
            amount_paid = float(body["amount_paid"]) if "amount_paid" in body else None
        except (TypeError, ValueError):
            return jsonify(ok=False, error="amount_paid must be a number"), 400

        conn = get_db()
        cart = cart_mod.snapshot()
        try:
            result = sales_mod.checkout(
                conn, cart, payment=payment,
                cashier_id=current_user_id(), amount_paid=amount_paid)
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            return jsonify(ok=False, error=f"Checkout failed: {e}"), 500

        cart_mod.clear()
        return jsonify(
            ok=True,
            receipt_no=result["receipt_no"],
            total=result["total"],
            change=result["change"],
            url=url_for("receipt_view", receipt_no=result["receipt_no"]),
        )

    @app.get("/receipt/<receipt_no>")
    @login_required()
    def receipt_view(receipt_no):
        conn = get_db()
        r = receipt_mod.fetch_receipt(conn, receipt_no)
        if not r:
            return "Receipt not found", 404
        shop = conn.execute(
            "SELECT value FROM settings WHERE key='shop_name'").fetchone()
        return render_template("receipt.html", r=r,
                               shop_name=(shop["value"] if shop else "My Shop"))

    # ---------- sync ----------
    @app.get("/admin/sync")
    @login_required("admin")
    def admin_sync():
        return render_template("admin_sync.html", s=sync_engine.status(get_db()))

    @app.post("/admin/sync/run")
    @login_required("admin")
    def admin_sync_run():
        try:
            r = sync_engine.run_sync(get_db())
            if r["offline"]:
                flash("Offline — will retry automatically.", "error")
            elif r["errors"]:
                flash("Sync ran with errors: " + "; ".join(r["errors"]), "error")
            else:
                flash(f"Synced: pushed {r['pushed']}, pulled {r['pulled']} "
                      f"(applied {r['applied']})", "ok")
        except Exception as e:
            flash(f"Sync failed: {e}", "error")
        return redirect(url_for("admin_sync"))

    @app.get("/api/sync/status")
    @login_required("admin")
    def api_sync_status():
        return jsonify(sync_engine.status(get_db()))

    # ---------- reports + exports ----------
    def _settings_to(conn):
        row = conn.execute(
            "SELECT value FROM settings WHERE key='daily_report_to'").fetchone()
        return (row["value"] if row else "") or ""

    def _setting(conn, key, default=""):
        row = conn.execute("SELECT value FROM settings WHERE key=?",
                           (key,)).fetchone()
        return row["value"] if row else default

    def _set_setting(conn, key, value):
        conn.execute(
            """INSERT INTO settings(key, value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value))

    @app.get("/admin/reports")
    @login_required("admin")
    def admin_reports():
        conn = get_db()
        recent = conn.execute(
            "SELECT * FROM export_log ORDER BY id DESC LIMIT 15").fetchall()
        return render_template(
            "admin_reports.html", recent=recent,
            to_number=_settings_to(conn),
            enabled=_setting(conn, "daily_report_enabled", "1"),
            today=date.today().isoformat())

    @app.post("/admin/reports/settings")
    @login_required("admin")
    def admin_reports_settings():
        conn = get_db()
        _set_setting(conn, "daily_report_to", request.form.get("to", "").strip())
        _set_setting(conn, "daily_report_enabled",
                     "1" if request.form.get("enabled") else "0")
        flash("Report settings saved.", "ok")
        return redirect(url_for("admin_reports"))

    @app.post("/admin/reports/send-now")
    @login_required("admin")
    def admin_reports_send_now():
        result = daily_job.run_daily_report(force=True)
        if result["sent"]:
            flash("Report sent via WhatsApp.", "ok")
        else:
            flash(f"Report not sent: {result['reason']}", "error")
        return redirect(url_for("admin_reports"))

    @app.get("/export/daily.pdf")
    @login_required("admin")
    def export_daily_pdf():
        d = request.args.get("date") or date.today().isoformat()
        data = pdf_export.daily_pdf(get_db(), d)
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=True, download_name=f"daily-{d}.pdf")

    @app.get("/export/daily.xlsx")
    @login_required("admin")
    def export_daily_xlsx():
        d = request.args.get("date") or date.today().isoformat()
        data = excel_export.daily_xlsx(get_db(), d)
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=f"daily-{d}.xlsx")

    @app.get("/export/stock.xlsx")
    @login_required("admin")
    def export_stock_xlsx():
        data = excel_export.stock_xlsx(get_db())
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"stock-{date.today().isoformat()}.xlsx")

    @app.get("/export/receipt/<receipt_no>.pdf")
    @login_required()
    def export_receipt_pdf(receipt_no):
        data = pdf_export.receipt_pdf(get_db(), receipt_no)
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=True,
                         download_name=f"receipt-{receipt_no}.pdf")

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", version=cfg.APP_VERSION,
                       schema=cfg.SCHEMA_TARGET)

    return app


if __name__ == "__main__":
    import webbrowser, threading, time
    app = create_app()

    def _open():
        time.sleep(1.5)
        try:
            webbrowser.open("http://127.0.0.1:5000")
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()
    print("")
    print("=" * 60)
    print("  ShopTrack GH is running")
    print("  Open in your browser:  http://127.0.0.1:5000")
    print("  Login:  admin / changeme123")
    print("=" * 60)
    print("")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)