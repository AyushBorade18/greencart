"""store.py - the customer side: shop, product page, cart, coupon, checkout, orders, invoice, reviews."""
from flask import render_template, request, redirect, url_for, session, flash, abort

from .. import config, db
from ..security import customer_required
from ..services import (cart_summary, estimated_delivery, load_order, notify_order, order_extra,
                        recent_products, remember_viewed, return_deadline, return_window_open,
                        tracker, wishlist_ids, address_text)
from ..utils import cached, to_int, to_decimal, parse_quantity, safe_next

# Only these exact texts can reach the SQL (safe against injection).
SORTS = {
    "featured": "product_id ASC",
    "best":     "units_sold DESC, product_name ASC",
    "rating":   "avg_rating DESC, review_count DESC, product_name ASC",
    "az":       "product_name ASC",
    "za":       "product_name DESC",
    "low":      "price ASC",
    "high":     "price DESC",
    "new":      "created_at DESC, product_id DESC",
}


def register(app):

    # ---------------------------------------------------------- shop
    @app.route("/shop")
    @customer_required
    def shop():
        search = request.args.get("search", "").strip()[:100]
        category_id = to_int(request.args.get("category"))
        in_stock = request.args.get("in_stock") == "1"
        raw_min, raw_max = request.args.get("min_price", ""), request.args.get("max_price", "")
        min_price = to_decimal(raw_min) if raw_min else None
        max_price = to_decimal(raw_max) if raw_max else None
        min_rating = to_int(request.args.get("rating"))
        if min_rating not in (1, 2, 3, 4):
            min_rating = None
        sort = request.args.get("sort", "featured")
        if sort not in SORTS:
            sort = "featured"

        sql = "SELECT * FROM vw_product_catalog WHERE is_active = 1"
        params = []
        if search:
            sql += " AND (product_name LIKE %s OR description LIKE %s)"
            params += ["%" + search + "%", "%" + search + "%"]
        if category_id:
            sql += " AND category_id = %s"
            params.append(category_id)
        if in_stock:
            sql += " AND quantity_available > 0"
        if min_price is not None:
            sql += " AND price >= %s"
            params.append(min_price)
        if max_price is not None:
            sql += " AND price <= %s"
            params.append(max_price)
        if min_rating:
            sql += " AND avg_rating >= %s AND review_count > 0"
            params.append(min_rating)
        sql += " ORDER BY " + SORTS[sort]
        products = db.query_all(sql, tuple(params))

        def load_meta():
            cats = db.query_all(
                "SELECT c.category_id, c.category_name, c.description, COUNT(p.product_id) AS product_count "
                "FROM category c LEFT JOIN product p ON p.category_id = c.category_id AND p.is_active = 1 "
                "GROUP BY c.category_id, c.category_name, c.description ORDER BY c.category_name")
            rng = db.query_one("SELECT MIN(price) AS lo, MAX(price) AS hi FROM product WHERE is_active = 1")
            return cats, rng
        categories, price_range = cached("shop_meta", 20, load_meta)
        current = next((c for c in categories if c["category_id"] == category_id), None)
        filtered = bool(search or category_id or in_stock or min_price is not None or max_price is not None
                        or min_rating)
        recent = [] if filtered else recent_products()

        return render_template("shop.html", products=products, categories=categories, current=current,
                               price_range=price_range, sort=sort, search=search, in_stock=in_stock,
                               category_id=category_id, min_price=raw_min, max_price=raw_max,
                               min_rating=min_rating, wish_ids=wishlist_ids(session["customer_id"]),
                               recent=recent)

    # ------------------------------------------------- product page
    @app.route("/product/<int:product_id>")
    @customer_required
    def product_page(product_id):
        cid = session["customer_id"]
        product = db.query_one(
            "SELECT * FROM vw_product_catalog WHERE product_id = %s AND is_active = 1", (product_id,))
        if not product:
            abort(404)

        images = db.query_all("SELECT image_id, image_url FROM product_image WHERE product_id = %s "
                              "ORDER BY sort_order, image_id", (product_id,))
        if not images and product.get("image_url"):
            images = [{"image_id": None, "image_url": product["image_url"]}]
        reviews = db.query_all(
            "SELECT * FROM vw_review_details WHERE product_id = %s ORDER BY review_date DESC", (product_id,))
        state = db.query_one(
            "SELECT (SELECT COALESCE(SUM(ci.quantity), 0) FROM cart c JOIN cart_item ci ON ci.cart_id = c.cart_id "
            "         WHERE c.customer_id = %s AND ci.product_id = %s) AS in_cart, "
            "       (SELECT COUNT(*) FROM orders o JOIN order_item oi ON oi.order_id = o.order_id "
            "         WHERE o.customer_id = %s AND oi.product_id = %s AND o.order_status = 'Delivered') AS delivered, "
            "       (SELECT COUNT(*) FROM review WHERE customer_id = %s AND product_id = %s) AS reviewed",
            (cid, product_id, cid, product_id, cid, product_id)) or {}
        in_cart = int(state.get("in_cart") or 0)
        delivered = int(state.get("delivered") or 0) > 0     # a review is allowed only after delivery (a trigger enforces it too)
        reviewed = int(state.get("reviewed") or 0) > 0
        related = db.query_all(
            "SELECT * FROM vw_product_catalog WHERE category_id = %s AND product_id <> %s AND is_active = 1 "
            "ORDER BY units_sold DESC, product_name LIMIT 4", (product["category_id"], product_id))
        wished = wishlist_ids(cid)
        recent = recent_products(exclude=product_id, limit=4)
        remember_viewed(product_id)

        return render_template("product.html", p=product, images=images, reviews=reviews, in_cart=in_cart,
                               can_review=delivered and not reviewed, reviewed=reviewed, related=related,
                               wish_ids=wished, recent=recent, eta=estimated_delivery(),
                               free_above=config.FREE_DELIVERY_ABOVE, window=config.RETURN_WINDOW_DAYS)

    # ------------------------------------------------------- cart
    @app.route("/cart")
    @customer_required
    def cart():
        cid = session["customer_id"]
        summary = cart_summary(cid, session.get("coupon"))
        if summary["coupon_error"]:                       # the saved coupon stopped being valid
            flash(summary["coupon_error"], "error")
            session.pop("coupon", None)
        customer = db.query_one(
            "SELECT customer_id, full_name, email, phone, address, city, pincode FROM customer "
            "WHERE customer_id = %s", (cid,))
        addresses = db.query_all("SELECT * FROM address WHERE customer_id = %s "
                                 "ORDER BY is_default DESC, address_id", (cid,))
        default_address = "%s, %s %s" % (customer["address"], customer["city"], customer["pincode"])
        return render_template("cart.html", items=summary["items"], s=summary, total=summary["total"],
                               default_address=default_address, addresses=addresses,
                               methods=config.PAYMENT_METHODS, has_problem=summary["has_problem"],
                               eta=estimated_delivery(), free_above=config.FREE_DELIVERY_ABOVE)

    @app.route("/cart/add", methods=["POST"])
    @customer_required
    def cart_add():
        quantity, error = parse_quantity(request.form.get("quantity", 1), "Quantity", minimum=1)
        product_id = to_int(request.form.get("product_id"))
        if error or product_id is None:
            flash(error or "Invalid product.", "error")
            return redirect(safe_next("shop"))
        try:
            db.call_procedure("sp_add_to_cart", (session["customer_id"], product_id, quantity))
            flash("Added to your cart.", "success")
        except db.DBError as e:
            flash(db.error_message(e), "error")          # e.g. "more than the available stock"
            return redirect(safe_next("cart"))
        if request.form.get("buy_now"):                   # "Buy now" goes straight to the cart
            return redirect(url_for("cart"))
        return redirect(safe_next("cart"))

    @app.route("/cart/update", methods=["POST"])
    @customer_required
    def cart_update():
        quantity, error = parse_quantity(request.form.get("quantity"), "Quantity", minimum=0)
        item_id = to_int(request.form.get("cart_item_id"))
        if error or item_id is None:
            flash(error or "Invalid item.", "error")
            return redirect(url_for("cart"))
        owner = "AND cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)"
        try:
            if quantity < 1:
                db.execute("DELETE FROM cart_item WHERE cart_item_id = %s " + owner,
                           (item_id, session["customer_id"]))
                flash("Item removed.", "success")
            else:
                # the trigger refuses a quantity above the stock
                db.execute("UPDATE cart_item SET quantity = %s WHERE cart_item_id = %s " + owner,
                           (quantity, item_id, session["customer_id"]))
                flash("Cart updated.", "success")
        except db.DBError as e:
            flash(db.error_message(e), "error")
        return redirect(url_for("cart"))

    @app.route("/cart/remove", methods=["POST"])
    @customer_required
    def cart_remove():
        item_id = to_int(request.form.get("cart_item_id"))
        if item_id is not None:
            db.execute("DELETE FROM cart_item WHERE cart_item_id = %s "
                       "AND cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)",
                       (item_id, session["customer_id"]))
            flash("Item removed from your cart.", "success")
        return redirect(url_for("cart"))

    # --------------------------------------------------- coupons
    @app.route("/cart/coupon", methods=["POST"])
    @customer_required
    def cart_coupon():
        code = "".join(request.form.get("code", "").split()).upper()[:30]
        if not code:
            flash("Please enter a coupon code.", "error")
            return redirect(url_for("cart"))
        summary = cart_summary(session["customer_id"], code)
        if not summary["items"]:
            flash("Add something to your cart first.", "error")
        elif summary["coupon_error"]:
            flash(summary["coupon_error"], "error")
        else:
            session["coupon"] = code
            flash("Coupon %s applied: you save Rs %s." % (code, "{:,.2f}".format(summary["discount"])), "success")
        return redirect(url_for("cart"))

    @app.route("/cart/coupon/remove", methods=["POST"])
    @customer_required
    def cart_coupon_remove():
        session.pop("coupon", None)
        flash("Coupon removed.", "success")
        return redirect(url_for("cart"))

    # -------------------------------------------------- checkout
    # sp_place_order is ONE transaction: order + items + stock + coupon + payment, or nothing at all
    @app.route("/checkout", methods=["POST"])
    @customer_required
    def checkout():
        cid = session["customer_id"]
        method = request.form.get("payment_method", "")
        address = request.form.get("shipping_address", "").strip()
        address_id = to_int(request.form.get("address_id"))
        if method not in config.PAYMENT_METHODS:
            flash("Please choose a payment method.", "error")
            return redirect(url_for("cart"))
        if address_id:                                    # a saved address from the address book
            saved = db.query_one("SELECT * FROM address WHERE address_id = %s AND customer_id = %s",
                                 (address_id, cid))
            if not saved:
                flash("That address was not found.", "error")
                return redirect(url_for("cart"))
            address = address_text(saved)
        if len(address) > 300:
            flash("The delivery address is too long (maximum 300 characters).", "error")
            return redirect(url_for("cart"))
        try:
            result = db.call_procedure("sp_place_order", (
                cid, method, address, session.get("coupon") or "",
                config.DELIVERY_FEE, config.FREE_DELIVERY_ABOVE, 0))
            order_id = result[6]                           # the OUT parameter
            session.pop("coupon", None)
            notify_order(order_id, "placed")
            flash("Order placed successfully! Your order number is #%s." % order_id, "success")
            return redirect(url_for("order_detail", order_id=order_id))
        except db.DBError as e:
            flash(db.error_message(e), "error")            # rolled back: nothing was saved
            return redirect(url_for("cart"))

    # ----------------------------------------------------- orders
    @app.route("/orders")
    @customer_required
    def orders():
        rows = db.query_all(
            "SELECT * FROM vw_order_summary WHERE customer_id = %s ORDER BY order_id DESC",
            (session["customer_id"],))
        return render_template("orders.html", orders=rows)

    @app.route("/orders/<int:order_id>")
    @customer_required
    def order_detail(order_id):
        cid = session["customer_id"]
        order, items, history = load_order(order_id, cid)
        if not order:
            abort(404)
        reviewed = {r["product_id"] for r in db.query_all(
            "SELECT product_id FROM review WHERE customer_id = %s", (cid,))}
        returned = {r["product_id"]: int(r["qty"]) for r in db.query_all(
            "SELECT product_id, SUM(quantity) AS qty FROM return_request "
            "WHERE order_id = %s AND customer_id = %s AND status <> 'Rejected' GROUP BY product_id",
            (order_id, cid))}
        my_returns = db.query_all("SELECT * FROM vw_returns WHERE order_id = %s AND customer_id = %s "
                                  "ORDER BY return_id DESC", (order_id, cid))
        return render_template("store_order.html", order=order, items=items, history=history,
                               reviewed=reviewed, track=tracker(order, history), extra=order_extra(order_id, order),
                               returned=returned, my_returns=my_returns, can_return=return_window_open(order),
                               return_until=return_deadline(order), reasons=config.RETURN_REASONS,
                               window=config.RETURN_WINDOW_DAYS)

    @app.route("/orders/<int:order_id>/invoice")
    @customer_required
    def order_invoice(order_id):
        cid = session["customer_id"]
        order, items, _ = load_order(order_id, cid)
        if not order:
            abort(404)
        customer = db.query_one("SELECT full_name, email, phone FROM customer WHERE customer_id = %s", (cid,))
        refunds = db.query_all("SELECT * FROM vw_returns WHERE order_id = %s AND customer_id = %s "
                               "AND status = 'Refunded'", (order_id, cid))
        return render_template("invoice.html", order=order, items=items, extra=order_extra(order_id, order),
                               customer=customer, refunds=refunds, store=config.STORE_NAME,
                               back=url_for("order_detail", order_id=order_id))

    @app.route("/orders/<int:order_id>/cancel", methods=["POST"])
    @customer_required
    def order_cancel(order_id):
        try:
            db.call_procedure("sp_cancel_order", (order_id, session["customer_id"]))
            notify_order(order_id, "Cancelled")
            flash("Order cancelled. Stock was restored and any payment refunded.", "success")
        except db.DBError as e:
            flash(db.error_message(e), "error")
            mine = db.query_one("SELECT order_id FROM orders WHERE order_id = %s AND customer_id = %s",
                                (order_id, session["customer_id"]))
            if not mine:                                # not this customer's order
                return redirect(url_for("orders"))
        return redirect(url_for("order_detail", order_id=order_id))

    # ----------------------------------------------------- review
    @app.route("/review", methods=["POST"])
    @customer_required
    def review():
        product_id = to_int(request.form.get("product_id"))
        rating = to_int(request.form.get("rating"))
        if product_id is None:
            abort(404)
        if rating is None or not 1 <= rating <= 5:
            flash("Please choose a rating from 1 to 5.", "error")
            return redirect(url_for("product_page", product_id=product_id))
        try:
            db.execute("INSERT INTO review (product_id, customer_id, rating, comment) VALUES (%s, %s, %s, %s)",
                       (product_id, session["customer_id"], rating, request.form.get("comment", "").strip()[:500]))
            flash("Thank you for your review!", "success")
        except db.DBError as e:
            if getattr(e, "errno", None) == 1062:
                flash("You have already reviewed this product.", "error")
            else:
                flash(db.error_message(e), "error")
        return redirect(url_for("product_page", product_id=product_id))
