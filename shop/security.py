"""
security.py - who may open which page.

  customer_required / seller_required / admin_required  go above a route.
  same_origin_only  blocks form posts that come from another website (CSRF).
"""
from functools import wraps
from urllib.parse import urlparse

from flask import session, flash, redirect, url_for, request, g, abort
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_hash, password):
    return bool(stored_hash) and check_password_hash(stored_hash, password)


# ---------------------------------------------------------------------
#  Login guards
# ---------------------------------------------------------------------
def customer_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "customer_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin login required.", "error")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


def seller_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "seller_id" not in session:
            flash("Please log in as a seller to continue.", "error")
            return redirect(url_for("seller_login"))
        # Read the seller again on every page, so an admin's approval or
        # suspension takes effect immediately.
        seller = db.query_one(
            "SELECT seller_id, business_name, contact_name, email, phone, address, city, "
            "pincode, gst_number, status FROM seller WHERE seller_id = %s", (session["seller_id"],))
        if not seller:
            session.clear()
            flash("Seller account not found.", "error")
            return redirect(url_for("seller_login"))
        g.seller = seller
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------
#  CSRF protection without changing a single form:
#  a browser always tells us which site a POST came from. If it is not
#  this site, the request is refused.
# ---------------------------------------------------------------------
def same_origin_only():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return                      # old browsers / tools: SameSite cookies still protect us
    allowed = {request.host.lower()}
    forwarded = request.headers.get("X-Forwarded-Host")
    if forwarded:
        allowed.add(forwarded.split(",")[0].strip().lower())
    if config.SITE_URL:
        allowed.add(urlparse(config.SITE_URL).netloc.lower())
    if urlparse(source).netloc.lower() not in allowed:
        abort(403)
