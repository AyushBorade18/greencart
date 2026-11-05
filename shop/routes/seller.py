"""seller.py - everything a wholesale seller can do (login/registration are in accounts.py)."""
import re

from flask import render_template, request, redirect, url_for, flash, session, g, abort

from .. import db, supa
from ..catalog import get_or_create_category, clean_category_name
from ..security import seller_required
from ..utils import to_int, parse_price, parse_quantity, is_local_path


def register(app):

    # ------------------------------------------------------------ dashboard
    @app.route("/seller")
    @seller_required
    def seller_dashboard():
        sid = g.seller["seller_id"]
        stats = db.query_one(
            "SELECT"
            " (SELECT COUNT(*) FROM seller_listing WHERE seller_id = %s AND is_active = 1) AS listings,"
            " (SELECT COALESCE(SUM(quantity_available), 0) FROM seller_listing"
            "    WHERE seller_id = %s AND is_active = 1) AS units,"
            " (SELECT COUNT(*) FROM purchase_order WHERE status = 'Open') AS open_requests,"
            " (SELECT COUNT(*) FROM purchase_order WHERE seller_id = %s AND status = 'Requested') AS new_orders,"
            " (SELECT COUNT(*) FROM purchase_order WHERE seller_id = %s AND status = 'Accepted') AS to_ship,"
            " fn_seller_earnings(%s) AS earned", (sid, sid, sid, sid, sid))
        needs = db.query_all(
            "SELECT product_id, product_name, category_name, image_url, quantity_available, stock_status "
            "FROM vw_product_supply WHERE is_active = 1 AND stock_status IN ('Low Stock', 'Out of Stock') "
            "ORDER BY quantity_available, product_name LIMIT 6")
        recent = db.query_all(
            "SELECT * FROM vw_purchase_orders WHERE seller_id = %s ORDER BY po_id DESC LIMIT 5", (sid,))
        return render_template("seller_dashboard.html", stats=stats, needs=needs, recent=recent)

    # ------------------------------------------------------------- listings
    def categories_and_products():
        return (db.query_all("SELECT category_id, category_name FROM category ORDER BY category_name"),
                db.query_all("SELECT product_id, product_name, category_id FROM product ORDER BY product_name"))

    def read_listing_form(form, files, seller_id, listing=None):
        """Checks the whole form FIRST, uploads the picture LAST. Returns (values, error)."""
        name = (form.get("item_name") or "").strip()
        if not name or len(name) > 100:
            return None, "Please enter the item name (up to 100 characters)."
        price, error = parse_price(form.get("unit_price"), "Wholesale price")
        if error:
            return None, error
        qty, error = parse_quantity(form.get("quantity_available"), "Quantity")
        if error:
            return None, error
        min_qty, error = parse_quantity(form.get("min_order_qty"), "Minimum order quantity", minimum=1)
        if error:
            return None, error

        product_id = to_int(form.get("product_id"))
        category_id = to_int(form.get("category_id"))
        new_category = clean_category_name(form.get("new_category"))
        if product_id:                                   # linked to a store product
            product = db.query_one("SELECT category_id FROM product WHERE product_id = %s", (product_id,))
            if not product:
                return None, "That store product does not exist."
            category_id = product["category_id"]         # keep the category consistent
            clash = db.query_one(
                "SELECT listing_id FROM seller_listing WHERE seller_id = %s AND product_id = %s "
                "AND listing_id <> %s", (seller_id, product_id, listing["listing_id"] if listing else 0))
            if clash:
                return None, "You already have a listing for that store product. Edit it instead."
        if not product_id:
            if new_category:                              # typed a new category: it is created at the very end
                category_id = None
            elif not category_id or not db.query_one(
                    "SELECT category_id FROM category WHERE category_id = %s", (category_id,)):
                return None, "Please choose a category, or type a new one."

        image_url = listing["image_url"] if listing else None
        if form.get("remove_image"):
            image_url = None
        try:
            uploaded = supa.save_image(files.get("image"), "seller-%d" % seller_id)
        except ValueError as e:
            return None, str(e)
        if uploaded:
            image_url = uploaded

        if not product_id and new_category:               # all checks passed: now create the category
            try:
                category_id = get_or_create_category(new_category)
            except db.DBError as e:
                return None, db.error_message(e)

        return {
            "product_id": product_id or None, "item_name": name, "category_id": category_id,
            "description": (form.get("description") or "").strip()[:500], "image_url": image_url,
            "unit_price": price, "quantity_available": qty, "min_order_qty": min_qty,
            "is_active": 1 if (listing is None or form.get("is_active")) else 0,
        }, None

    @app.route("/seller/products")
    @seller_required
    def seller_products():
        items = db.query_all(
            "SELECT l.*, l.item_name AS product_name FROM vw_seller_listings l "
            "WHERE l.seller_id = %s ORDER BY l.listing_id DESC", (g.seller["seller_id"],))
        return render_template("seller_products.html", items=items)

    @app.route("/seller/products/new", methods=["GET", "POST"])
    @seller_required
    def seller_product_new():
        sid = g.seller["seller_id"]
        categories, products = categories_and_products()
        if request.method == "POST":
            if g.seller["status"] != "Approved":
                flash("Your seller account is not approved yet. The admin must approve it first.", "error")
                return render_template("seller_product_form.html", item=request.form, categories=categories,
                                       products=products, is_new=True)
            values, error = read_listing_form(request.form, request.files, sid)
            if not error:
                try:
                    db.execute(
                        "INSERT INTO seller_listing (seller_id, product_id, item_name, category_id, description, "
                        "image_url, unit_price, quantity_available, min_order_qty) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (sid, values["product_id"], values["item_name"], values["category_id"],
                         values["description"], values["image_url"], values["unit_price"],
                         values["quantity_available"], values["min_order_qty"]))
                    flash("Item listed. The store admin can see it now.", "success")
                    return redirect(url_for("seller_products"))
                except db.DBError as e:
                    error = db.error_message(e)
            flash(error, "error")
            return render_template("seller_product_form.html", item=request.form, categories=categories,
                                   products=products, is_new=True)
        # opened from "the store needs this": pre-fill the product
        item = {"min_order_qty": 1, "quantity_available": 0}
        wanted = to_int(request.args.get("product"))
        if wanted:
            product = db.query_one(
                "SELECT product_id, product_name, category_id FROM product WHERE product_id = %s", (wanted,))
            if product:
                item.update(product_id=product["product_id"], item_name=product["product_name"],
                            category_id=product["category_id"])
        return render_template("seller_product_form.html", item=item, categories=categories,
                               products=products, is_new=True)

    @app.route("/seller/products/<int:listing_id>/edit", methods=["GET", "POST"])
    @seller_required
    def seller_product_edit(listing_id):
        sid = g.seller["seller_id"]
        listing = db.query_one("SELECT * FROM seller_listing WHERE listing_id = %s AND seller_id = %s",
                               (listing_id, sid))
        if not listing:
            abort(404)
        categories, products = categories_and_products()
        if request.method == "POST":
            values, error = read_listing_form(request.form, request.files, sid, listing)
            if not error:
                try:
                    db.execute(
                        "UPDATE seller_listing SET product_id = %s, item_name = %s, category_id = %s, "
                        "description = %s, image_url = %s, unit_price = %s, quantity_available = %s, "
                        "min_order_qty = %s, is_active = %s WHERE listing_id = %s AND seller_id = %s",
                        (values["product_id"], values["item_name"], values["category_id"], values["description"],
                         values["image_url"], values["unit_price"], values["quantity_available"],
                         values["min_order_qty"], values["is_active"], listing_id, sid))
                    flash("Listing updated.", "success")
                    return redirect(url_for("seller_products"))
                except db.DBError as e:
                    error = db.error_message(e)
            flash(error, "error")
            item = dict(listing)
            item.update(request.form.to_dict())
            item["image_url"] = listing["image_url"]
            return render_template("seller_product_form.html", item=item, categories=categories,
                                   products=products, is_new=False, listing_id=listing_id)
        return render_template("seller_product_form.html", item=listing, categories=categories,
                               products=products, is_new=False, listing_id=listing_id)

    @app.route("/seller/products/<int:listing_id>/toggle", methods=["POST"])
    @seller_required
    def seller_product_toggle(listing_id):
        db.execute("UPDATE seller_listing SET is_active = NOT is_active WHERE listing_id = %s AND seller_id = %s",
                   (listing_id, g.seller["seller_id"]))
        flash("Listing visibility changed.", "success")
        return redirect(url_for("seller_products"))

    @app.route("/seller/products/<int:listing_id>/delete", methods=["POST"])
    @seller_required
    def seller_product_delete(listing_id):
        db.execute("DELETE FROM seller_listing WHERE listing_id = %s AND seller_id = %s",
                   (listing_id, g.seller["seller_id"]))
        flash("Listing deleted.", "success")
        return redirect(url_for("seller_products"))

    # ------------------------------------------------------------- requests
    @app.route("/seller/requests")
    @seller_required
    def seller_requests():
        sid = g.seller["seller_id"]
        open_requests = db.query_all("SELECT * FROM vw_purchase_orders WHERE status = 'Open' ORDER BY po_id DESC")
        new_orders = db.query_all(
            "SELECT * FROM vw_purchase_orders WHERE seller_id = %s AND status = 'Requested' ORDER BY po_id DESC", (sid,))
        my_offers = db.query_all(
            "SELECT * FROM vw_purchase_orders WHERE seller_id = %s AND status = 'Offered' ORDER BY po_id DESC", (sid,))
        return render_template("seller_requests.html", open_requests=open_requests,
                               new_orders=new_orders, my_offers=my_offers)

    @app.route("/seller/requests/<int:po_id>/offer", methods=["POST"])
    @seller_required
    def seller_offer(po_id):
        qty, error = parse_quantity(request.form.get("quantity"), "Quantity", minimum=1)
        price, price_error = parse_price(request.form.get("unit_price"), "Price per item")
        if error or price_error:
            flash(error or price_error, "error")
        else:
            try:
                db.call_procedure("sp_offer_purchase_order",
                                  (po_id, g.seller["seller_id"], qty, price, request.form.get("note", "")[:300]))
                flash("Offer sent. The admin will review it.", "success")
            except db.DBError as e:
                flash(db.error_message(e), "error")
        return redirect(url_for("seller_requests"))

    # --------------------------------------------------------------- orders
    @app.route("/seller/orders")
    @seller_required
    def seller_orders():
        orders = db.query_all(
            "SELECT * FROM vw_purchase_orders WHERE seller_id = %s ORDER BY po_id DESC", (g.seller["seller_id"],))
        return render_template("seller_orders.html", orders=orders)

    @app.route("/seller/orders/<int:po_id>")
    @seller_required
    def seller_order_detail(po_id):
        order = db.query_one("SELECT * FROM vw_purchase_orders WHERE po_id = %s AND seller_id = %s",
                             (po_id, g.seller["seller_id"]))
        if not order:
            abort(404)
        history = db.query_all("SELECT old_status, new_status, changed_at FROM purchase_order_log "
                               "WHERE po_id = %s ORDER BY log_id", (po_id,))
        return render_template("seller_order_detail.html", o=order, history=history)

    @app.route("/seller/orders/<int:po_id>/action", methods=["POST"])
    @seller_required
    def seller_order_action(po_id):
        action = request.form.get("action", "")
        if action not in ("accept", "decline", "ship"):
            flash("Unknown action.", "error")
        else:
            try:
                db.call_procedure("sp_seller_po_action",
                                  (po_id, g.seller["seller_id"], action, request.form.get("note", "")[:300]))
                flash({"accept": "Order accepted.", "decline": "Order declined.",
                       "ship": "Marked as shipped. The store will confirm when it arrives."}[action], "success")
            except db.DBError as e:
                flash(db.error_message(e), "error")
        target = request.form.get("next", "")
        return redirect(target if is_local_path(target) else url_for("seller_orders"))

    # ------------------------------------------------------------- earnings
    @app.route("/seller/earnings")
    @seller_required
    def seller_earnings():
        sid = g.seller["seller_id"]
        totals = db.query_one(
            "SELECT COALESCE(SUM(CASE WHEN status = 'Received' THEN total_cost END), 0) AS earned,"
            " COALESCE(SUM(CASE WHEN status IN ('Accepted', 'Shipped') THEN total_cost END), 0) AS pending,"
            " COUNT(CASE WHEN status = 'Received' THEN 1 END) AS completed "
            "FROM purchase_order WHERE seller_id = %s", (sid,)) or {}
        monthly = db.query_all(
            "SELECT DATE_FORMAT(received_at, '%%Y-%%m') AS month, COUNT(*) AS orders_count, "
            "SUM(total_cost) AS amount FROM purchase_order "
            "WHERE seller_id = %s AND status = 'Received' GROUP BY month ORDER BY month DESC LIMIT 12", (sid,))
        paid = db.query_all(
            "SELECT * FROM vw_purchase_orders WHERE seller_id = %s AND status = 'Received' "
            "ORDER BY received_at DESC LIMIT 20", (sid,))
        return render_template("seller_earnings.html", totals=totals, monthly=monthly, paid=paid)

    # -------------------------------------------------------------- profile
    @app.route("/seller/profile", methods=["GET", "POST"])
    @seller_required
    def seller_profile():
        sid = g.seller["seller_id"]
        if request.method == "POST":
            f = request.form
            vals = {k: (f.get(k) or "").strip() for k in
                    ("business_name", "contact_name", "phone", "address", "city", "pincode", "gst_number")}
            if not all(vals[k] for k in ("business_name", "contact_name", "phone", "address", "city", "pincode")):
                flash("Please fill in every field except the GST number.", "error")
            elif not re.fullmatch(r"\d{10}", vals["phone"]):
                flash("Phone number must be exactly 10 digits.", "error")
            elif not re.fullmatch(r"\d{6}", vals["pincode"]):
                flash("Pincode must be exactly 6 digits.", "error")
            else:
                db.execute(
                    "UPDATE seller SET business_name = %s, contact_name = %s, phone = %s, address = %s, "
                    "city = %s, pincode = %s, gst_number = %s WHERE seller_id = %s",
                    (vals["business_name"][:120], vals["contact_name"][:100], vals["phone"], vals["address"][:255],
                     vals["city"][:50], vals["pincode"], vals["gst_number"][:20] or None, sid))
                session["seller_name"] = vals["business_name"]
                flash("Profile updated.", "success")
                return redirect(url_for("seller_profile"))
            return render_template("seller_profile.html", s=dict(g.seller, **vals))
        return render_template("seller_profile.html", s=g.seller)
