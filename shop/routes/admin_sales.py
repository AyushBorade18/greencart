"""admin_sales.py - admin pages for coupons, returns/refunds, invoices and product pictures."""
import re
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort

from .. import config, db
from ..catalog import sync_cover
from ..security import admin_required
from ..services import load_order, notify_return, order_extra
from ..utils import to_decimal, parse_price, parse_quantity

RETURN_STATUSES = ["Requested", "Approved", "Refunded", "Rejected"]


def _date(text):
    """'2026-11-30T23:59' (from a datetime-local box) -> datetime, '' -> None, bad -> ValueError."""
    text = (text or "").strip()
    return datetime.strptime(text, "%Y-%m-%dT%H:%M") if text else None


def read_coupon_form(form):
    """Returns (values, error)."""
    code = "".join((form.get("code") or "").split()).upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", code):
        return None, "The code must be 3 to 20 letters or numbers (no spaces), for example SAVE10."
    kind = form.get("discount_type")
    if kind not in ("percent", "flat"):
        return None, "Please choose percentage or flat amount."
    value, error = parse_price(form.get("discount_value"), "Discount")
    if error:
        return None, error
    if kind == "percent" and value > 100:
        return None, "A percentage discount cannot be more than 100."

    minimum = to_decimal(form.get("min_order_amount") or "0")
    if minimum is None or minimum < 0 or minimum > config.MAX_PRICE:
        return None, "Minimum order amount must be 0 or more."
    max_discount = None
    if (form.get("max_discount") or "").strip():
        max_discount, error = parse_price(form.get("max_discount"), "Maximum discount")
        if error:
            return None, error
    usage_limit = None
    if (form.get("usage_limit") or "").strip():
        usage_limit, error = parse_quantity(form.get("usage_limit"), "Total uses", minimum=1)
        if error:
            return None, error
    per_customer, error = parse_quantity(form.get("per_customer_limit") or "1", "Uses per customer", minimum=1)
    if error:
        return None, error
    try:
        valid_from, valid_to = _date(form.get("valid_from")), _date(form.get("valid_to"))
    except ValueError:
        return None, "Please enter valid dates."
    if valid_from and valid_to and valid_to <= valid_from:
        return None, "The end date must be after the start date."
    description = (form.get("description") or "").strip()[:200]
    return {"code": code, "description": description or None, "type": kind, "value": value,
            "minimum": minimum, "max_discount": max_discount, "usage_limit": usage_limit,
            "per_customer": per_customer, "valid_from": valid_from, "valid_to": valid_to}, None


def register(app):

    # --------------------------------------------------------- coupons
    @app.route("/admin/coupons")
    @admin_required
    def admin_coupons():
        coupons = db.query_all(
            "SELECT c.*, (SELECT COUNT(*) FROM coupon_redemption r WHERE r.coupon_id = c.coupon_id) AS used, "
            "(SELECT COALESCE(SUM(r.discount_amount), 0) FROM coupon_redemption r "
            " WHERE r.coupon_id = c.coupon_id) AS given "
            "FROM coupon c ORDER BY c.coupon_id DESC")
        return render_template("admin_coupons.html", coupons=coupons, now=datetime.now(), form={})

    @app.route("/admin/coupons/add", methods=["POST"])
    @admin_required
    def admin_coupon_add():
        v, error = read_coupon_form(request.form)
        if not error:
            try:
                db.execute(
                    "INSERT INTO coupon (code, description, discount_type, discount_value, min_order_amount, "
                    "max_discount, usage_limit, per_customer_limit, valid_from, valid_to) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (v["code"], v["description"], v["type"], v["value"], v["minimum"], v["max_discount"],
                     v["usage_limit"], v["per_customer"], v["valid_from"], v["valid_to"]))
                flash("Coupon %s created." % v["code"], "success")
                return redirect(url_for("admin_coupons"))
            except db.DBError as e:
                error = "A coupon with this code already exists." if getattr(e, "errno", None) == 1062 \
                    else db.error_message(e)
        flash(error, "error")
        coupons = db.query_all("SELECT c.*, 0 AS used, 0 AS given FROM coupon c ORDER BY c.coupon_id DESC")
        return render_template("admin_coupons.html", coupons=coupons, now=datetime.now(), form=request.form)

    @app.route("/admin/coupons/<int:coupon_id>/toggle", methods=["POST"])
    @admin_required
    def admin_coupon_toggle(coupon_id):
        db.execute("UPDATE coupon SET is_active = NOT is_active WHERE coupon_id = %s", (coupon_id,))
        flash("Coupon updated.", "success")
        return redirect(url_for("admin_coupons"))

    @app.route("/admin/coupons/<int:coupon_id>/delete", methods=["POST"])
    @admin_required
    def admin_coupon_delete(coupon_id):
        try:
            db.execute("DELETE FROM coupon WHERE coupon_id = %s", (coupon_id,))
            flash("Coupon deleted.", "success")
        except db.DBError as e:
            flash("This coupon was already used, so it cannot be deleted. Switch it off instead."
                  if getattr(e, "errno", None) == 1451 else db.error_message(e), "error")
        return redirect(url_for("admin_coupons"))

    # --------------------------------------------------------- returns
    @app.route("/admin/returns")
    @admin_required
    def admin_returns():
        status = request.args.get("status", "")
        if status in RETURN_STATUSES:
            rows = db.query_all("SELECT * FROM vw_returns WHERE status = %s ORDER BY return_id DESC", (status,))
        else:
            status = ""
            rows = db.query_all("SELECT * FROM vw_returns ORDER BY FIELD(status, 'Requested', 'Approved', "
                                "'Refunded', 'Rejected'), return_id DESC")
        return render_template("admin_returns.html", returns=rows, statuses=RETURN_STATUSES, selected=status)

    @app.route("/admin/returns/<int:return_id>/action", methods=["POST"])
    @admin_required
    def admin_return_action(return_id):
        action = request.form.get("action", "")
        if action not in ("approve", "reject", "refund"):
            flash("Unknown action.", "error")
        else:
            try:
                db.call_procedure("sp_update_return", (return_id, action, request.form.get("note", "")[:300],
                                                       0 if request.form.get("no_restock") else 1))
                notify_return(return_id)
                flash({"approve": "Return approved.", "reject": "Return rejected.",
                       "refund": "Refund recorded. Stock was updated and the customer was notified."}[action],
                      "success")
            except db.DBError as e:
                flash(db.error_message(e), "error")
        return redirect(url_for("admin_returns", status=request.form.get("back_status", "")))

    # --------------------------------------------------------- invoice
    @app.route("/admin/orders/<int:order_id>/invoice")
    @admin_required
    def admin_order_invoice(order_id):
        order, items, _ = load_order(order_id)
        if not order:
            abort(404)
        customer = db.query_one("SELECT c.full_name, c.email, c.phone FROM orders o JOIN customer c "
                                "ON c.customer_id = o.customer_id WHERE o.order_id = %s", (order_id,))
        refunds = db.query_all("SELECT * FROM vw_returns WHERE order_id = %s AND status = 'Refunded'", (order_id,))
        return render_template("invoice.html", order=order, items=items, extra=order_extra(order_id, order),
                               customer=customer, refunds=refunds, store=config.STORE_NAME,
                               back=url_for("admin_order_detail", order_id=order_id))

    # ----------------------------------------------- product pictures
    @app.route("/admin/products/<int:product_id>/images/<int:image_id>/delete", methods=["POST"])
    @admin_required
    def admin_image_delete(product_id, image_id):
        db.execute("DELETE FROM product_image WHERE image_id = %s AND product_id = %s", (image_id, product_id))
        sync_cover(product_id)
        flash("Picture removed.", "success")
        return redirect(url_for("admin_product_edit", product_id=product_id))

    @app.route("/admin/products/<int:product_id>/images/<int:image_id>/cover", methods=["POST"])
    @admin_required
    def admin_image_cover(product_id, image_id):
        lowest = db.scalar("SELECT COALESCE(MIN(sort_order), 0) FROM product_image WHERE product_id = %s",
                           (product_id,))
        db.execute("UPDATE product_image SET sort_order = %s WHERE image_id = %s AND product_id = %s",
                   (int(lowest) - 1, image_id, product_id))
        sync_cover(product_id)
        flash("Cover picture changed.", "success")
        return redirect(url_for("admin_product_edit", product_id=product_id))
