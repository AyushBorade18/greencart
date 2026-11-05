"""
accounts.py - register, log in and log out (customers and sellers).

Supabase checks the password and the confirmed email. MySQL keeps the
person's details. A customer / seller row is created in MySQL the first
time a CONFIRMED person logs in, and Supabase's user id is saved in
auth_id so the two systems stay linked.
"""
import re

from flask import render_template, request, redirect, url_for, session, flash

from .. import config, db, supa
from ..supa import AuthError

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CUSTOMER_FIELDS = ["full_name", "phone", "address", "city", "pincode"]
SELLER_FIELDS = ["business_name", "contact_name", "phone", "address", "city", "pincode", "gst_number"]
MAX_LENGTH = {"full_name": 100, "business_name": 120, "contact_name": 100, "address": 255,
              "city": 50, "gst_number": 20}


def clean_profile(form, fields):
    return {name: (form.get(name) or "").strip() for name in fields}


def check_profile(profile, email, password, required):
    """Returns an error text, or None when everything looks fine."""
    for name in required:
        if not profile.get(name):
            return "Please fill in every field."
    for name, limit in MAX_LENGTH.items():
        if len(profile.get(name, "")) > limit:
            return "One of the fields is too long."
    if not EMAIL_PATTERN.match(email) or len(email) > 100:
        return "Please enter a valid email address."
    if password is not None and len(password) < 6:
        return "Password must be at least 6 characters."
    if not re.fullmatch(r"\d{10}", profile["phone"]):
        return "Phone number must be exactly 10 digits."
    if not re.fullmatch(r"\d{6}", profile["pincode"]):
        return "Pincode must be exactly 6 digits."
    return None


# ---------------------------------------------------------------------
#  MySQL side
# ---------------------------------------------------------------------
def find_account(role, email=None, auth_id=None):
    if role == "customer":
        sql = "SELECT customer_id, full_name, auth_id FROM customer WHERE "
    else:
        sql = "SELECT seller_id, business_name, auth_id FROM seller WHERE "
    if auth_id:
        row = db.query_one(sql + "auth_id = %s", (auth_id,))
        if row:
            return row
    if email:
        return db.query_one(sql + "email = %s", (email,))
    return None


def create_account(role, email, auth_id, profile):
    if role == "customer":
        db.execute(
            "INSERT INTO customer (auth_id, full_name, email, phone, address, city, pincode) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (auth_id or None, profile["full_name"], email, profile["phone"], profile["address"],
             profile["city"], profile["pincode"]))
    else:
        db.execute(
            "INSERT INTO seller (auth_id, business_name, contact_name, email, phone, address, "
            "city, pincode, gst_number) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (auth_id or None, profile["business_name"], profile["contact_name"], email, profile["phone"],
             profile["address"], profile["city"], profile["pincode"], profile["gst_number"] or None))


def start_session(role, account):
    session.clear()
    session.permanent = True
    if role == "customer":
        session["customer_id"] = account["customer_id"]
        session["name"] = account["full_name"]
    else:
        session["seller_id"] = account["seller_id"]
        session["seller_name"] = account["business_name"]


def account_from_supabase(role, user):
    """
    After Supabase confirmed the person: find their MySQL row (or create it).
    Returns (account, error_text).
    """
    email, auth_id, meta = user["email"], user["id"], user["metadata"]
    other = "seller" if role == "customer" else "customer"
    id_column = "customer_id" if role == "customer" else "seller_id"
    table = role

    account = find_account(role, email, auth_id)
    if account:
        if auth_id and not account.get("auth_id"):          # first login of an older row: link it
            db.execute("UPDATE %s SET auth_id = %%s WHERE %s = %%s" % (table, id_column),
                       (auth_id, account[id_column]))
        return account, None

    if find_account(other, email, auth_id):
        return None, "This email belongs to a %s account. Please use the %s login." % (other, other)
    if meta.get("role") != role:
        return None, "This email was not registered as a %s. Please register first." % role

    fields = CUSTOMER_FIELDS if role == "customer" else SELLER_FIELDS
    profile = {name: str(meta.get(name) or "").strip() for name in fields}
    try:
        create_account(role, email, auth_id, profile)
    except db.DBError as e:
        if getattr(e, "errno", None) != 1062:                # 1062 = created a moment ago by another request
            return None, db.error_message(e)
    return find_account(role, email, auth_id), None


# ---------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------
def register(app):

    def not_ready():
        if not config.SUPABASE_READY:
            flash("Sign-in is not set up yet: add SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY "
                  "to the website settings.", "error")
            return True
        return False

    @app.route("/register", methods=["GET", "POST"], endpoint="register")
    def register_customer():
        return register_page("customer")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        return login_page("customer")

    @app.route("/seller/register", methods=["GET", "POST"])
    def seller_register():
        return register_page("seller")

    @app.route("/seller/login", methods=["GET", "POST"])
    def seller_login():
        return login_page("seller")

    def register_page(role):
        template = "register.html" if role == "customer" else "seller_register.html"
        fields = CUSTOMER_FIELDS if role == "customer" else SELLER_FIELDS
        required = [f for f in fields if f != "gst_number"]
        if request.method == "GET":
            not_ready()
            return render_template(template, form={})

        form = request.form
        email = (form.get("email") or "").strip().lower()
        password = form.get("password") or ""
        profile = clean_profile(form, fields)

        problem = check_profile(profile, email, password, required)
        if not problem and (find_account("customer", email) or find_account("seller", email)):
            problem = "This email is already registered. Please log in."
        if problem:
            flash(problem, "error")
            return render_template(template, form=form)
        if not_ready():
            return render_template(template, form=form)

        try:
            result = supa.sign_up(email, password, dict(profile, role=role),
                                  _site_url() + url_for("auth_confirmed"))
        except AuthError as e:
            flash(e.message, "error")
            return render_template(template, form=form)

        login_endpoint = "login" if role == "customer" else "seller_login"
        if result["already_registered"]:
            flash("This email is already registered. Please log in.", "error")
            return redirect(url_for(login_endpoint))
        if not config.REQUIRE_EMAIL_CONFIRMATION:
            flash("Account created. You can log in now." + (
                " The admin must approve your seller account before you can list items."
                if role == "seller" else ""), "success")
            return redirect(url_for(login_endpoint))
        return render_template("check_email.html", email=email, role=role)

    def login_page(role):
        template = "login.html" if role == "customer" else "seller_login.html"
        if request.method == "GET":
            not_ready()
            return render_template(template, need_confirm=False, email="")

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not_ready():
            return render_template(template, need_confirm=False, email=email)
        if not email or not password:
            flash("Please enter your email and password.", "error")
            return render_template(template, need_confirm=False, email=email)

        try:
            user = supa.sign_in(email, password)
        except AuthError as e:
            flash(e.message, "error")
            return render_template(template, need_confirm=e.needs_confirmation, email=email)

        account, problem = account_from_supabase(role, user)
        if problem:
            flash(problem, "error")
            return render_template(template, need_confirm=False, email=email)

        start_session(role, account)
        return redirect(url_for("shop" if role == "customer" else "seller_dashboard"))

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("home"))

    @app.route("/auth/confirmed")
    def auth_confirmed():
        # Supabase sends people here after they click the link in the email
        return render_template("auth_confirmed.html")

    @app.route("/auth/resend", methods=["POST"])
    def auth_resend():
        email = (request.form.get("email") or "").strip().lower()
        back = request.form.get("role", "customer")
        if config.SUPABASE_READY and email:
            try:
                supa.resend_confirmation(email, _site_url() + url_for("auth_confirmed"))
                flash("A new confirmation email has been sent. Please check your inbox.", "success")
            except AuthError as e:
                flash(e.message, "error")
        return redirect(url_for("seller_login" if back == "seller" else "login"))


def _site_url():
    return config.SITE_URL or request.host_url.rstrip("/")
