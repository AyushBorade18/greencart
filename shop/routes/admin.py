"""admin.py - the admin panel: login, orders, products, categories, inventory, customers, reports."""
import time

from flask import render_template, request, redirect, url_for, session, flash, abort

from .. import config, db, supa
from ..catalog import add_images
from ..security import admin_required, verify_password
from ..utils import (to_int, parse_price, parse_quantity, add_pct, safe_next,
                     is_image_link, forget)
from ..services import load_order, notify_order, order_extra


def _categories():
    return db.query_all("SELECT category_id, category_name FROM category ORDER BY category_name")


def register(app):

    # ---------------------------------------------------------- login
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = db.query_one("SELECT admin_id, password_hash FROM admin WHERE username = %s", (username,))
            if admin and verify_password(admin["password_hash"], password):
                session.clear()
                session.permanent = True
                session["is_admin"] = True
                return redirect(url_for("admin_dashboard"))
            time.sleep(0.6)                      # slows down password guessing
            flash("Wrong admin username or password.", "error")
        return render_template("admin_login.html")

    # ------------------------------------------------------ dashboard
    @app.route("/admin/dashboard")
    @admin_required
    def admin_dashboard():
        # one round trip instead of ten
        stats = db.query_one(
            "SELECT "
            " (SELECT COUNT(*) FROM orders) AS orders,"
            " (SELECT COALESCE(SUM(total_amount), 0) FROM orders WHERE order_status <> 'Cancelled') AS revenue,"
            " (SELECT COUNT(*) FROM customer) AS customers,"
            " (SELECT COUNT(*) FROM vw_low_stock) AS low_stock,"
            " (SELECT COUNT(*) FROM seller WHERE status = 'Pending') AS pending_sellers,"
            " (SELECT COUNT(*) FROM purchase_order WHERE status = 'Offered') AS offers,"
            " (SELECT COUNT(*) FROM purchase_order WHERE status = 'Shipped') AS to_receive,"
            " (SELECT COUNT(*) FROM orders WHERE order_status IN ('Confirmed', 'Packed')) AS to_process,"
            " (SELECT COUNT(*) FROM seller_listing WHERE product_id IS NULL AND is_active = 1) AS new_items,"
            " (SELECT COUNT(*) FROM category) AS categories,"
            " (SELECT COUNT(*) FROM product) AS products,"
            " (SELECT COUNT(*) FROM seller WHERE status = 'Approved') AS approved_sellers,"
            " (SELECT COUNT(*) FROM return_request WHERE status IN ('Requested', 'Approved')) AS open_returns")
        recent = db.query_all("SELECT * FROM vw_order_summary ORDER BY order_id DESC LIMIT 5")
        return render_template("admin_dashboard.html", stats=stats, recent=recent)

    # --------------------------------------------------------- orders
    @app.route("/admin/orders")
    @admin_required
    def admin_orders():
        status = request.args.get("status", "").strip()
        if status in config.ORDER_STATUSES:
            rows = db.query_all(
                "SELECT * FROM vw_order_summary WHERE order_status = %s ORDER BY order_id DESC", (status,))
        else:
            status = ""
            rows = db.query_all("SELECT * FROM vw_order_summary ORDER BY order_id DESC")
        return render_template("admin_orders.html", orders=rows, statuses=config.ORDER_STATUSES,
                               selected=status, next_status=config.NEXT_STATUS)

    @app.route("/admin/orders/<int:order_id>")
    @admin_required
    def admin_order_detail(order_id):
        order, items, history = load_order(order_id)
        if not order:
            abort(404)
        return render_template("order_detail.html", order=order, items=items, history=history,
                               reviewed=set(), admin=True, extra=order_extra(order_id, order),
                               returns=db.query_all("SELECT * FROM vw_returns WHERE order_id = %s "
                                                    "ORDER BY return_id DESC", (order_id,)),
                               next_options=config.NEXT_STATUS.get(order["order_status"], []))

    @app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
    @admin_required
    def admin_update_status(order_id):
        new_status = request.form.get("new_status", "")
        if new_status not in config.ORDER_STATUSES:
            flash("Please choose a valid status.", "error")
        else:
            try:
                # The procedure updates the order; triggers then log the change, fill in
                # the shipment, mark cash-on-delivery as paid, or restore stock and refund.
                db.call_procedure("sp_update_order_status", (order_id, new_status))
                if new_status == "Shipped":
                    courier = request.form.get("courier_name", "").strip()[:50]
                    tracking = request.form.get("tracking_number", "").strip()[:30]
                    if courier:
                        db.execute("UPDATE shipment SET courier_name = %s WHERE order_id = %s", (courier, order_id))
                    if tracking:                       # overrides the automatic tracking number
                        db.execute("UPDATE shipment SET tracking_number = %s WHERE order_id = %s",
                                   (tracking, order_id))
                notify_order(order_id, new_status)     # email the customer (if emails are switched on)
                flash("Order #%s is now %s." % (order_id, new_status), "success")
            except db.DBError as e:
                flash(db.error_message(e), "error")
        return redirect(safe_next("admin_orders"))

    # ------------------------------------------------------- products
    @app.route("/admin/products")
    @admin_required
    def admin_products():
        products = db.query_all(
            "SELECT p.product_id, p.product_name, c.category_name, p.price, p.is_active, "
            "       i.quantity_available, i.reorder_level, fn_stock_status(p.product_id) AS stock_status "
            "FROM product p "
            "JOIN category c  ON c.category_id = p.category_id "
            "JOIN inventory i ON i.product_id  = p.product_id "
            "ORDER BY p.product_id")
        return render_template("admin_products.html", products=products)

    def read_product_form(form, is_new, files=None):
        """Returns (values, error). Uploaded pictures and the picture link become product pictures."""
        name = form.get("product_name", "").strip()
        category_id = to_int(form.get("category_id"))
        description = form.get("description", "").strip()
        image_url = form.get("image_url", "").strip()
        if not name or len(name) > 100:
            return None, "Please enter a product name (up to 100 characters)."
        if category_id is None:
            return None, "Please choose a category."
        if len(description) > 500:
            return None, "The description is too long (maximum 500 characters)."
        if image_url and (not is_image_link(image_url) or len(image_url) > 500):
            return None, "The image link must start with http://, https:// or /"
        price, error = parse_price(form.get("price"), "Price")
        if error:
            return None, error
        values = {"name": name, "category_id": category_id, "description": description,
                  "image_url": image_url, "price": price}
        if is_new:
            values["stock"], error = parse_quantity(form.get("stock"), "Opening stock")
            if error:
                return None, error
            values["reorder"], error = parse_quantity(form.get("reorder_level"), "Reorder level")
            if error:
                return None, error
        # pictures are uploaded last, after every other check passed
        urls = []
        try:
            for file in (files.getlist("images") if files else [])[:config.MAX_PRODUCT_IMAGES]:
                url = supa.save_image(file, "store")
                if url:
                    urls.append(url)
        except ValueError as e:
            return None, str(e)
        if image_url:                                     # a picture link is one more picture
            urls.append(image_url)
        values["new_images"] = urls[:config.MAX_PRODUCT_IMAGES]
        values["image_url"] = values["new_images"][0] if values["new_images"] else ""
        return values, None

    @app.route("/admin/products/new", methods=["GET", "POST"])
    @admin_required
    def admin_product_new():
        categories = _categories()
        if request.method == "POST":
            f = request.form
            values, error = read_product_form(f, is_new=True, files=request.files)
            if not error:
                try:
                    # one transaction: product + stock row
                    result = db.call_procedure("sp_add_product", (
                        values["category_id"], values["name"], values["description"], values["price"],
                        values["stock"], values["reorder"], values["image_url"], 0))
                    add_images(result[7], values["new_images"], config.MAX_PRODUCT_IMAGES)
                    flash("Product added (id %s)." % result[7], "success")
                    return redirect(url_for("admin_products"))
                except db.DBError as e:
                    error = db.error_message(e)
            flash(error, "error")
            return render_template("admin_product_form.html", product=f, categories=categories, is_new=True)
        return render_template("admin_product_form.html", product={"reorder_level": 5, "stock": 0},
                               categories=categories, is_new=True)

    @app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_product_edit(product_id):
        product = db.query_one("SELECT * FROM product WHERE product_id = %s", (product_id,))
        if not product:
            abort(404)
        categories = _categories()
        if request.method == "POST":
            f = request.form
            values, error = read_product_form(f, is_new=False, files=request.files)
            if not error:
                try:
                    # a price change is recorded in price_history by a trigger
                    db.execute(
                        "UPDATE product SET category_id = %s, product_name = %s, description = %s, "
                        "price = %s, is_active = %s WHERE product_id = %s",
                        (values["category_id"], values["name"], values["description"], values["price"],
                         1 if f.get("is_active") else 0, product_id))
                    added = add_images(product_id, values["new_images"], config.MAX_PRODUCT_IMAGES)
                    if values["new_images"] and not added:
                        flash("This product already has the maximum of %d pictures." % config.MAX_PRODUCT_IMAGES,
                              "error")
                    flash("Product updated.", "success")
                    return redirect(url_for("admin_products"))
                except db.DBError as e:
                    error = db.error_message(e)
            flash(error, "error")
            product = dict(product)
            product.update(f.to_dict())
            product["is_active"] = 1 if f.get("is_active") else 0
        pictures = db.query_all("SELECT image_id, image_url FROM product_image WHERE product_id = %s "
                                "ORDER BY sort_order, image_id", (product_id,))
        if not pictures and product.get("image_url"):
            pictures = [{"image_id": None, "image_url": product["image_url"]}]
        return render_template("admin_product_form.html", product=product, categories=categories, is_new=False,
                               pictures=pictures, max_images=config.MAX_PRODUCT_IMAGES)

    @app.route("/admin/products/<int:product_id>/toggle", methods=["POST"])
    @admin_required
    def admin_product_toggle(product_id):
        db.execute("UPDATE product SET is_active = NOT is_active WHERE product_id = %s", (product_id,))
        flash("Product visibility changed.", "success")
        return redirect(url_for("admin_products"))

    @app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
    @admin_required
    def admin_product_delete(product_id):
        try:
            db.execute("DELETE FROM product WHERE product_id = %s", (product_id,))
            flash("Product deleted.", "success")
        except db.DBError as e:
            if getattr(e, "errno", None) == 1451:      # foreign key: the product is in past orders
                flash("This product has been ordered before, so it cannot be deleted. Use Hide instead.", "error")
            else:
                flash(db.error_message(e), "error")
        return redirect(url_for("admin_products"))

    # ----------------------------------------------------- categories
    @app.route("/admin/categories")
    @admin_required
    def admin_categories():
        categories = db.query_all(
            "SELECT c.category_id, c.category_name, c.description, COUNT(p.product_id) AS product_count "
            "FROM category c LEFT JOIN product p ON p.category_id = c.category_id "
            "GROUP BY c.category_id, c.category_name, c.description ORDER BY c.category_name")
        return render_template("admin_categories.html", categories=categories)

    def save_category(sql, params_builder):
        name = request.form.get("category_name", "").strip()
        description = request.form.get("description", "").strip()
        if not name or len(name) > 50 or len(description) > 255:
            flash("Category name is required (up to 50 characters).", "error")
            return False
        try:
            db.execute(sql, params_builder(name, description))
            forget("nav_categories")
            return True
        except db.DBError as e:
            flash("A category with this name already exists." if getattr(e, "errno", None) == 1062
                  else db.error_message(e), "error")
            return False

    @app.route("/admin/categories/add", methods=["POST"])
    @admin_required
    def admin_category_add():
        if save_category("INSERT INTO category (category_name, description) VALUES (%s, %s)",
                         lambda n, d: (n, d)):
            flash("Category added.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/categories/<int:category_id>/edit", methods=["POST"])
    @admin_required
    def admin_category_edit(category_id):
        if save_category("UPDATE category SET category_name = %s, description = %s WHERE category_id = %s",
                         lambda n, d: (n, d, category_id)):
            flash("Category updated.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/categories/<int:category_id>/delete", methods=["POST"])
    @admin_required
    def admin_category_delete(category_id):
        try:
            db.execute("DELETE FROM category WHERE category_id = %s", (category_id,))
            forget("nav_categories")
            flash("Category deleted.", "success")
        except db.DBError as e:
            if getattr(e, "errno", None) == 1451:      # products (or seller items) still use it
                flash("This category is still used by products. Move or delete them first.", "error")
            else:
                flash(db.error_message(e), "error")
        return redirect(url_for("admin_categories"))

    # ------------------------------------------------------ inventory
    # Stock can NOT be added by hand: it goes up only when a seller's
    # purchase order is received (see restock.py and trigger trg_po_after_update).
    @app.route("/admin/inventory")
    @admin_required
    def admin_inventory():
        stock = db.query_all(
            "SELECT * FROM vw_product_supply ORDER BY FIELD(stock_status, 'Out of Stock', 'Low Stock', 'In Stock'), "
            "quantity_available, product_name")
        alerts = db.query_all(
            "SELECT a.alert_time, p.product_name, a.quantity_left FROM low_stock_alert a "
            "JOIN product p ON p.product_id = a.product_id ORDER BY a.alert_id DESC LIMIT 10")
        return render_template("admin_inventory.html", stock=stock, alerts=alerts)

    # ------------------------------------------------------ customers
    @app.route("/admin/customers")
    @admin_required
    def admin_customers():
        customers = db.query_all(
            "SELECT t.customer_id, t.full_name, t.email, t.city, t.orders_count, t.total_spent, t.tier, "
            "       c.phone, c.created_at "
            "FROM vw_top_customers t JOIN customer c ON c.customer_id = t.customer_id "
            "ORDER BY t.total_spent DESC, t.full_name")
        return render_template("admin_customers.html", customers=customers)

    # --------------------------------------------------------- reports
    @app.route("/admin/reports")
    @admin_required
    def admin_reports():
        by_category = add_pct(db.query_all("SELECT * FROM vw_sales_by_category ORDER BY revenue DESC"), "revenue")
        top_products = add_pct(db.query_all(
            "SELECT * FROM vw_top_products ORDER BY units_sold DESC, revenue DESC LIMIT 5"), "units_sold")
        monthly = add_pct(db.query_all("SELECT * FROM vw_monthly_sales ORDER BY sales_month"), "revenue")
        payments = add_pct(db.query_all(
            "SELECT pay.payment_method, COUNT(*) AS orders_count, SUM(pay.amount) AS amount "
            "FROM payment pay JOIN orders o ON o.order_id = pay.order_id "
            "WHERE o.order_status <> 'Cancelled' GROUP BY pay.payment_method ORDER BY amount DESC"), "amount")
        statuses = add_pct(db.query_all(
            "SELECT order_status, COUNT(*) AS n FROM orders GROUP BY order_status ORDER BY n DESC"), "n")
        top_customers = db.query_all("SELECT * FROM vw_top_customers ORDER BY total_spent DESC LIMIT 5")
        price_log = db.query_all(
            "SELECT ph.changed_at, p.product_name, ph.old_price, ph.new_price FROM price_history ph "
            "JOIN product p ON p.product_id = ph.product_id ORDER BY ph.history_id DESC LIMIT 8")
        status_log = db.query_all(
            "SELECT changed_at, order_id, old_status, new_status FROM order_status_log "
            "ORDER BY log_id DESC LIMIT 8")
        return render_template("admin_reports.html", by_category=by_category, top_products=top_products,
                               monthly=monthly, payments=payments, statuses=statuses,
                               top_customers=top_customers, price_log=price_log, status_log=status_log)
