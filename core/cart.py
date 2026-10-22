"""
Server-side cart stored in Flask session. Survives page refresh and
accidental F5. On checkout, core.sales.checkout() reads and clears it.
"""
from flask import session


CART_KEY = "cart"          # { product_id (str): {qty:int, price:float, name:str} }
META_KEY = "cart_meta"     # { lines: int, subtotal: float, updated_at: str }


def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat(timespec="seconds")


def get_cart() -> dict:
    return session.get(CART_KEY, {})


def _recount(cart: dict):
    subtotal = 0.0
    lines = 0
    for item in cart.values():
        subtotal += item["price"] * item["qty"]
        lines += item["qty"]
    session[CART_KEY] = cart
    session[META_KEY] = {
        "lines": lines,
        "subtotal": round(subtotal, 2),
        "updated_at": _now(),
    }
    session.modified = True


def add(product: dict, qty: int = 1) -> dict:
    """
    product = {id, name, price, stock}. Enforces stock from DB truth
    (caller passes fresh stock from DB, not stale UI value).
    """
    if qty <= 0:
        raise ValueError("Qty must be positive")
    cart = get_cart()
    key = str(product["id"])
    current = cart.get(key, {"qty": 0, "price": product["price"], "name": product["name"]})
    new_qty = current["qty"] + qty
    if new_qty > product["stock"]:
        raise ValueError(f"Only {product['stock']} of {product['name']} in stock")
    cart[key] = {"qty": new_qty, "price": float(product["price"]), "name": product["name"]}
    _recount(cart)
    return cart


def set_qty(product_id: int, qty: int, fresh_stock: int) -> dict:
    if qty < 0:
        raise ValueError("Qty cannot be negative")
    cart = get_cart()
    key = str(product_id)
    if qty == 0:
        cart.pop(key, None)
    else:
        if qty > fresh_stock:
            raise ValueError(f"Only {fresh_stock} in stock")
        if key not in cart:
            raise ValueError("Item not in cart")
        cart[key]["qty"] = qty
    _recount(cart)
    return cart


def remove(product_id: int) -> dict:
    cart = get_cart()
    cart.pop(str(product_id), None)
    _recount(cart)
    return cart


def clear() -> None:
    session[CART_KEY] = {}
    session[META_KEY] = {"lines": 0, "subtotal": 0.0, "updated_at": _now()}
    session.modified = True


def snapshot() -> dict:
    cart = get_cart()
    meta = session.get(META_KEY, {"lines": 0, "subtotal": 0.0})
    items = [
        {"product_id": int(k), "name": v["name"], "price": v["price"],
         "qty": v["qty"], "line_total": round(v["price"] * v["qty"], 2)}
        for k, v in cart.items()
    ]
    return {"items": items, **meta}