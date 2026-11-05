"""
services.py - logic shared by several pages: cart totals, order bundles,
tracking steps, order emails, small session helpers.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import g, request, session, url_for

from . import config, db, mailer

TRACK_STEPS = ["Confirmed", "Packed", "Shipped", "Delivered"]


def site_url():
    return config.SITE_URL or request.host_url.rstrip("/")


# ---------------------------------------------------------------------
#  Delivery fee and cart totals
# ---------------------------------------------------------------------
def delivery_fee(amount):
    """Free from FREE_DELIVERY_ABOVE, otherwise DELIVERY_FEE (never for an empty cart)."""
    if amount <= 0:
        return Decimal("0")
    if config.FREE_DELIVERY_ABOVE and amount >= config.FREE_DELIVERY_ABOVE:
        return Decimal("0")
    return Decimal(config.DELIVERY_FEE)


def estimated_delivery(days=5):
    return date.today() + timedelta(days=days)


def cart_summary(customer_id, coupon_code=None):
    """
    Everything the cart page and checkout need: items, subtotal, coupon discount,
    delivery fee and total. The coupon is checked by the database procedure, the
    same one that is used when the order is really placed.
    """
    items = db.query_all(
        "SELECT ci.cart_item_id, p.product_id, p.product_name, p.price, p.image_url, cat.category_name, "
        "       ci.quantity, (p.price * ci.quantity) AS line_total, i.quantity_available "
        "FROM cart c "
        "JOIN cart_item ci ON ci.cart_id = c.cart_id "
        "JOIN product p    ON p.product_id = ci.product_id "
        "JOIN category cat ON cat.category_id = p.category_id "
        "JOIN inventory i  ON i.product_id = p.product_id "
        "WHERE c.customer_id = %s ORDER BY ci.added_at", (customer_id,))
    subtotal = sum((i["line_total"] for i in items), Decimal("0"))
    discount, coupon_error = Decimal("0"), None
    if coupon_code and items:
        try:
            result = db.call_procedure("sp_validate_coupon", (coupon_code, customer_id, subtotal, 0, 0))
            discount = Decimal(result[4] or 0)
        except db.DBError as e:
            coupon_error = db.error_message(e)
    fee = delivery_fee(subtotal - discount)
    return {
        "items": items, "subtotal": subtotal, "discount": discount, "fee": fee,
        "total": subtotal - discount + fee, "coupon_error": coupon_error,
        "coupon": coupon_code if discount > 0 else None,
        "has_problem": any(i["quantity"] > i["quantity_available"] for i in items),
        "free_left": (Decimal(config.FREE_DELIVERY_ABOVE) - (subtotal - discount))
                     if config.FREE_DELIVERY_ABOVE and fee > 0 and items else Decimal("0"),
    }


# ---------------------------------------------------------------------
#  Orders
# ---------------------------------------------------------------------
def load_order(order_id, customer_id=None):
    """One order with its items and history. Customers only see their own."""
    sql = "SELECT * FROM vw_order_summary WHERE order_id = %s"
    params = [order_id]
    if customer_id is not None:
        sql += " AND customer_id = %s"
        params.append(customer_id)
    order = db.query_one(sql, tuple(params))
    if not order:
        return None, [], []
    items = db.query_all("SELECT * FROM vw_order_details WHERE order_id = %s", (order_id,))
    history = db.query_all(
        "SELECT old_status, new_status, changed_at FROM order_status_log "
        "WHERE order_id = %s ORDER BY log_id", (order_id,))
    return order, items, history


def order_extra(order_id, order=None):
    """Coupon / delivery details of an order. Orders without a row get sensible zeros."""
    row = db.query_one(
        "SELECT subtotal, coupon_code, discount_amount, delivery_fee FROM order_extra WHERE order_id = %s",
        (order_id,))
    if row:
        return row
    total = order["total_amount"] if order else Decimal("0")
    return {"subtotal": total, "coupon_code": None, "discount_amount": Decimal("0"), "delivery_fee": Decimal("0")}


def tracker(order, history):
    """
    Steps for the progress bar: Confirmed > Packed > Shipped > Delivered, each with the
    time it happened. A cancelled order shows where it stopped.
    """
    times = {}
    for h in history:
        times.setdefault(h["new_status"], h["changed_at"])
    cancelled = order["order_status"] == "Cancelled"
    steps = []
    for name in TRACK_STEPS:
        steps.append({"name": name, "done": name in times, "time": times.get(name),
                      "current": (not cancelled) and order["order_status"] == name})
    return {"steps": steps, "cancelled": cancelled, "cancelled_at": times.get("Cancelled")}


def return_window_open(order):
    """True while a delivered order can still be returned."""
    if order["order_status"] != "Delivered" or not order.get("delivered_date"):
        return False
    return (date.today() - order["delivered_date"].date()).days < config.RETURN_WINDOW_DAYS


def return_deadline(order):
    if not order.get("delivered_date"):
        return None
    return order["delivered_date"].date() + timedelta(days=config.RETURN_WINDOW_DAYS)


# ---------------------------------------------------------------------
#  Emails
# ---------------------------------------------------------------------
_ORDER_EMAILS = {
    "placed":    ("Order #{id} confirmed", "Your order is confirmed",
                  "Thank you for shopping with us! We have received your order and will pack it shortly."),
    "Packed":    ("Order #{id} is packed", "Your order is packed",
                  "Good news: your order has been packed and will be handed to the courier soon."),
    "Shipped":   ("Order #{id} has shipped", "Your order is on its way",
                  "Your order has been handed to the courier. You can follow it with the details below."),
    "Delivered": ("Order #{id} delivered", "Your order was delivered",
                  "Your order has been delivered. We hope you love it! You can return items within %d days."
                  % config.RETURN_WINDOW_DAYS),
    "Cancelled": ("Order #{id} cancelled", "Your order was cancelled",
                  "Your order has been cancelled. Any online payment will be refunded."),
}


def notify_order(order_id, event):
    """Email the customer about an order event ('placed', 'Shipped', ...). Never raises."""
    if not config.EMAILS_READY or event not in _ORDER_EMAILS:
        return False
    try:
        order, items, _ = load_order(order_id)
        who = db.query_one("SELECT c.email, c.full_name FROM orders o JOIN customer c "
                           "ON c.customer_id = o.customer_id WHERE o.order_id = %s", (order_id,))
        if not order or not who:
            return False
        subject, heading, intro = _ORDER_EMAILS[event]
        rows = [("Order number", "#%s" % order_id),
                ("Total", "Rs %s" % "{:,.2f}".format(order["total_amount"])),
                ("Payment", "%s (%s)" % (order["payment_method"], order["payment_status"])),
                ("Delivery address", order["shipping_address"])]
        if event == "Shipped":
            rows += [("Tracking number", order.get("tracking_number") or "-"),
                     ("Expected delivery", order["expected_delivery"].strftime("%d %b %Y")
                      if order.get("expected_delivery") else "-")]
        return mailer.send(who["email"], subject.format(id=order_id), "message.html",
                           heading=heading, name=who["full_name"], intro=intro, rows=rows, items=items,
                           link=site_url() + url_for("order_detail", order_id=order_id))
    except Exception:
        return False


def notify_return(return_id):
    """Email the customer when a return request changes. Never raises."""
    if not config.EMAILS_READY:
        return False
    try:
        r = db.query_one("SELECT * FROM vw_returns WHERE return_id = %s", (return_id,))
        if not r:
            return False
        texts = {
            "Requested": "We have received your return request. We will review it shortly.",
            "Approved": "Your return request was approved. Please keep the item ready for pickup or send it back as instructed.",
            "Rejected": "Sorry, your return request was not approved." + (" Note: %s" % r["admin_note"] if r.get("admin_note") else ""),
            "Refunded": "Your refund has been processed. It will reach your original payment method soon.",
        }
        rows = [("Item", "%s x %s" % (r["product_name"], r["quantity"])),
                ("Order number", "#%s" % r["order_id"]),
                ("Refund amount", "Rs %s" % "{:,.2f}".format(r["refund_amount"])),
                ("Status", r["status"])]
        return mailer.send(r["customer_email"], "Return request #%s: %s" % (return_id, r["status"]), "message.html",
                           heading="Return %s" % r["status"].lower(), name=r["customer_name"],
                           intro=texts.get(r["status"], ""), rows=rows, items=None,
                           link=site_url() + url_for("returns"), link_text="View my returns")
    except Exception:
        return False


# ---------------------------------------------------------------------
#  Small session / customer helpers
# ---------------------------------------------------------------------
def remember_viewed(product_id, limit=8):
    seen = [p for p in session.get("recent", []) if p != product_id]
    seen.insert(0, product_id)
    session["recent"] = seen[:limit]


def recent_products(exclude=None, limit=6):
    ids = [p for p in session.get("recent", []) if p != exclude][:limit]
    if not ids:
        return []
    marks = ", ".join(["%s"] * len(ids))
    rows = db.query_all("SELECT * FROM vw_product_catalog WHERE is_active = 1 AND product_id IN (%s)" % marks,
                        tuple(ids))
    order = {pid: n for n, pid in enumerate(ids)}
    return sorted(rows, key=lambda r: order.get(r["product_id"], 99))


def customer_counts(customer_id):
    """
    Cart count, wishlist count and the wishlist's product ids in ONE query, remembered for the
    rest of the request (the header and the product cards both need them).
    """
    cached = getattr(g, "_counts", None)
    if cached is not None:
        return cached
    row = db.query_one(
        "SELECT (SELECT COALESCE(SUM(ci.quantity), 0) FROM cart c "
        "        JOIN cart_item ci ON ci.cart_id = c.cart_id WHERE c.customer_id = %s) AS cart_count, "
        "       (SELECT COUNT(*) FROM wishlist WHERE customer_id = %s) AS wish_count, "
        "       (SELECT GROUP_CONCAT(product_id) FROM wishlist WHERE customer_id = %s) AS wish_ids",
        (customer_id, customer_id, customer_id)) or {}
    g._counts = {
        "cart_count": int(row.get("cart_count") or 0),
        "wish_count": int(row.get("wish_count") or 0),
        "wish_ids": {int(x) for x in str(row.get("wish_ids") or "").split(",") if x.strip().isdigit()},
    }
    return g._counts


def wishlist_ids(customer_id):
    return customer_counts(customer_id)["wish_ids"]


def address_text(address):
    """One printable line for orders.shipping_address (a saved snapshot)."""
    return ("%s, %s, %s %s (Phone: %s)" % (address["full_name"], address["line1"], address["city"],
                                           address["pincode"], address["phone"]))[:300]
