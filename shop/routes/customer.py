"""customer.py - the customer's own area: account, address book, wishlist, returns."""
import re

from flask import render_template, request, redirect, url_for, session, flash, abort

from .. import config, db
from ..security import customer_required
from ..services import notify_return, wishlist_ids
from ..utils import to_int, parse_quantity, safe_next
from .accounts import CUSTOMER_FIELDS, check_profile, clean_profile

MAX_ADDRESSES = 8


def register(app):

    # ------------------------------------------------------ my account
    @app.route("/account", methods=["GET", "POST"])
    @customer_required
    def account():
        cid = session["customer_id"]
        customer = db.query_one(
            "SELECT customer_id, full_name, email, phone, address, city, pincode, created_at "
            "FROM customer WHERE customer_id = %s", (cid,))
        if not customer:
            session.clear()
            return redirect(url_for("login"))
        if request.method == "POST":
            profile = clean_profile(request.form, CUSTOMER_FIELDS)
            problem = check_profile(profile, customer["email"], None, CUSTOMER_FIELDS)
            if not problem:
                try:
                    db.execute(
                        "UPDATE customer SET full_name = %s, phone = %s, address = %s, city = %s, pincode = %s "
                        "WHERE customer_id = %s",
                        (profile["full_name"], profile["phone"], profile["address"], profile["city"],
                         profile["pincode"], cid))
                    session["name"] = profile["full_name"]
                    flash("Your details were saved.", "success")
                    return redirect(url_for("account"))
                except db.DBError as e:
                    problem = db.error_message(e)
            flash(problem, "error")
            return render_template("account.html", c=dict(customer, **profile), stats=_stats(cid), active="account")
        return render_template("account.html", c=customer, stats=_stats(cid), active="account")

    def _stats(cid):
        return db.query_one(
            "SELECT (SELECT COUNT(*) FROM orders WHERE customer_id = %s) AS orders,"
            " (SELECT COUNT(*) FROM wishlist WHERE customer_id = %s) AS wishlist,"
            " (SELECT COUNT(*) FROM return_request WHERE customer_id = %s) AS returns", (cid, cid, cid)) or {}

    # ------------------------------------------------------- addresses
    def read_address(form):
        a = {k: (form.get(k) or "").strip() for k in ("label", "full_name", "phone", "line1", "city", "pincode")}
        a["label"] = a["label"] or "Home"
        if not all(a[k] for k in ("full_name", "phone", "line1", "city", "pincode")):
            return None, "Please fill in every field."
        if len(a["label"]) > 30 or len(a["full_name"]) > 100 or len(a["line1"]) > 200 or len(a["city"]) > 50:
            return None, "One of the fields is too long."
        if not re.fullmatch(r"\d{10}", a["phone"]):
            return None, "Phone number must be exactly 10 digits."
        if not re.fullmatch(r"\d{6}", a["pincode"]):
            return None, "Pincode must be exactly 6 digits."
        return a, None

    @app.route("/addresses")
    @customer_required
    def addresses():
        rows = db.query_all("SELECT * FROM address WHERE customer_id = %s ORDER BY is_default DESC, address_id",
                            (session["customer_id"],))
        profile = db.query_one("SELECT full_name, phone FROM customer WHERE customer_id = %s",
                               (session["customer_id"],)) or {}
        return render_template("addresses.html", addresses=rows, profile=profile, active="addresses",
                               editing=None, max_addresses=MAX_ADDRESSES)

    @app.route("/addresses/save", methods=["POST"])
    @customer_required
    def address_save():
        cid = session["customer_id"]
        address_id = to_int(request.form.get("address_id"))
        values, error = read_address(request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("addresses"))
        make_default = 1 if request.form.get("is_default") else 0
        try:
            if address_id:
                if not db.query_one("SELECT address_id FROM address WHERE address_id = %s AND customer_id = %s",
                                    (address_id, cid)):
                    abort(404)
                db.execute("UPDATE address SET label = %s, full_name = %s, phone = %s, line1 = %s, city = %s, "
                           "pincode = %s WHERE address_id = %s AND customer_id = %s",
                           (values["label"], values["full_name"], values["phone"], values["line1"],
                            values["city"], values["pincode"], address_id, cid))
            else:
                if db.scalar("SELECT COUNT(*) FROM address WHERE customer_id = %s", (cid,)) >= MAX_ADDRESSES:
                    flash("You can save up to %d addresses. Delete one first." % MAX_ADDRESSES, "error")
                    return redirect(url_for("addresses"))
                first = db.scalar("SELECT COUNT(*) FROM address WHERE customer_id = %s", (cid,)) == 0
                address_id = db.execute(
                    "INSERT INTO address (customer_id, label, full_name, phone, line1, city, pincode, is_default) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (cid, values["label"], values["full_name"], values["phone"], values["line1"],
                     values["city"], values["pincode"], 1 if first else 0))
            if make_default:
                _set_default(cid, address_id)
            flash("Address saved.", "success")
        except db.DBError as e:
            flash(db.error_message(e), "error")
        return redirect(safe_next("addresses"))

    def _set_default(cid, address_id):
        db.execute("UPDATE address SET is_default = (address_id = %s) WHERE customer_id = %s", (address_id, cid))

    @app.route("/addresses/<int:address_id>/default", methods=["POST"])
    @customer_required
    def address_default(address_id):
        if db.query_one("SELECT address_id FROM address WHERE address_id = %s AND customer_id = %s",
                        (address_id, session["customer_id"])):
            _set_default(session["customer_id"], address_id)
            flash("Default address changed.", "success")
        return redirect(url_for("addresses"))

    @app.route("/addresses/<int:address_id>/delete", methods=["POST"])
    @customer_required
    def address_delete(address_id):
        cid = session["customer_id"]
        db.execute("DELETE FROM address WHERE address_id = %s AND customer_id = %s", (address_id, cid))
        has_default = db.scalar("SELECT COUNT(*) FROM address WHERE customer_id = %s AND is_default = 1", (cid,))
        if not has_default:                               # the default was deleted: promote another one
            left = db.query_one("SELECT address_id FROM address WHERE customer_id = %s ORDER BY address_id LIMIT 1",
                                (cid,))
            if left:
                _set_default(cid, left["address_id"])
        flash("Address deleted.", "success")
        return redirect(url_for("addresses"))

    # --------------------------------------------------------- wishlist
    @app.route("/wishlist")
    @customer_required
    def wishlist():
        cid = session["customer_id"]
        products = db.query_all(
            "SELECT p.*, w.added_at FROM wishlist w JOIN vw_product_catalog p ON p.product_id = w.product_id "
            "WHERE w.customer_id = %s AND p.is_active = 1 ORDER BY w.added_at DESC", (cid,))
        return render_template("wishlist.html", products=products, active="wishlist",
                               wish_ids={p["product_id"] for p in products})

    @app.route("/wishlist/toggle", methods=["POST"])
    @customer_required
    def wishlist_toggle():
        cid = session["customer_id"]
        product_id = to_int(request.form.get("product_id"))
        if product_id is None:
            abort(404)
        if product_id in wishlist_ids(cid):
            db.execute("DELETE FROM wishlist WHERE customer_id = %s AND product_id = %s", (cid, product_id))
            flash("Removed from your wishlist.", "success")
        else:
            try:
                db.execute("INSERT INTO wishlist (customer_id, product_id) VALUES (%s, %s)", (cid, product_id))
                flash("Saved to your wishlist.", "success")
            except db.DBError as e:
                flash("That product no longer exists." if getattr(e, "errno", None) == 1452
                      else db.error_message(e), "error")
        return redirect(safe_next("wishlist"))

    @app.route("/wishlist/move-to-cart", methods=["POST"])
    @customer_required
    def wishlist_move_to_cart():
        cid = session["customer_id"]
        product_id = to_int(request.form.get("product_id"))
        if product_id is None:
            abort(404)
        try:
            db.call_procedure("sp_add_to_cart", (cid, product_id, 1))
            db.execute("DELETE FROM wishlist WHERE customer_id = %s AND product_id = %s", (cid, product_id))
            flash("Moved to your cart.", "success")
            return redirect(url_for("cart"))
        except db.DBError as e:
            flash(db.error_message(e), "error")
            return redirect(url_for("wishlist"))

    # ----------------------------------------------------------- returns
    @app.route("/returns")
    @customer_required
    def returns():
        rows = db.query_all("SELECT * FROM vw_returns WHERE customer_id = %s ORDER BY return_id DESC",
                            (session["customer_id"],))
        return render_template("returns.html", returns=rows, active="returns", window=config.RETURN_WINDOW_DAYS)

    @app.route("/orders/<int:order_id>/return", methods=["POST"])
    @customer_required
    def return_request(order_id):
        product_id = to_int(request.form.get("product_id"))
        quantity, error = parse_quantity(request.form.get("quantity"), "Quantity", minimum=1)
        reason = request.form.get("reason", "")
        if product_id is None or error:
            flash(error or "Invalid item.", "error")
        elif reason not in config.RETURN_REASONS:
            flash("Please choose a reason for the return.", "error")
        else:
            try:
                result = db.call_procedure("sp_request_return", (
                    session["customer_id"], order_id, product_id, quantity, reason,
                    request.form.get("details", "").strip()[:500], config.RETURN_WINDOW_DAYS, 0))
                notify_return(result[7])
                flash("Return request #%s sent. We will review it soon." % result[7], "success")
                return redirect(url_for("returns"))
            except db.DBError as e:
                flash(db.error_message(e), "error")
        return redirect(url_for("order_detail", order_id=order_id))
