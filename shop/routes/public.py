"""public.py - files, home page, error pages and the data every page needs."""
import os

from flask import (render_template, redirect, url_for, session, send_from_directory,
                   jsonify, make_response)

from .. import config, db
from ..services import customer_counts
from ..utils import cached


def _categories():
    return db.query_all("SELECT category_id, category_name FROM category ORDER BY category_name")


def register(app):
    public = app.public_dir

    # ---- Files. On Vercel the "public" folder is served directly; these
    # ---- routes do the same job when you run the site on your own computer.
    @app.route("/style.css")
    def stylesheet():
        return send_from_directory(public, "style.css")

    @app.route("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(os.path.join(public, "assets"), filename)

    @app.route("/sw.js")
    def service_worker():
        response = make_response(send_from_directory(public, "sw.js", mimetype="application/javascript"))
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/manifest.webmanifest")
    def manifest():
        return send_from_directory(public, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.route("/offline")
    def offline():
        return render_template("offline.html")

    @app.route("/health")
    def health():
        """Open /health after deploying to see if the database connection works."""
        try:
            db.scalar("SELECT 1")
            return jsonify(ok=True, database=True)
        except db.DBError as e:
            app.logger.warning("health check failed: %s", e)
            return jsonify(ok=False, database=False), 503

    # ---- Data every storefront page needs (cart badge + category menu)
    @app.context_processor
    def inject_store_data():
        data = {"cart_count": 0, "wish_count": 0, "nav_categories": [],
                "free_above": config.FREE_DELIVERY_ABOVE, "return_days": config.RETURN_WINDOW_DAYS,
                "auth_mode": ("supabase" if config.REQUIRE_EMAIL_CONFIRMATION else "open")
                             if config.SUPABASE_READY else "unconfigured"}
        if "customer_id" in session:
            try:
                counts = customer_counts(session["customer_id"])
                data["cart_count"] = counts["cart_count"]
                data["wish_count"] = counts["wish_count"]
                data["nav_categories"] = cached("nav_categories", 60, _categories)
            except db.DBError:                   # the page must still open if the DB hiccups
                pass
        return data

    # ---- Home page
    @app.route("/")
    def home():
        if "customer_id" in session:
            return redirect(url_for("shop"))
        if session.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        if "seller_id" in session:
            return redirect(url_for("seller_dashboard"))
        return render_template("landing.html")

    # ---- Error pages (they never touch the database)
    def error_page(code, title, text):
        return render_template("error.html", code=code, title=title, text=text), code

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, "Request blocked", "This request did not come from our website. Please go back and try again.")

    @app.errorhandler(404)
    def page_not_found(error):
        return error_page(404, "Page not found", "The page you are looking for does not exist or has moved.")

    @app.errorhandler(413)
    def too_large(error):
        return error_page(413, "File too large", "The picture is too large. Please choose one under 2 MB.")

    @app.errorhandler(500)
    def server_error(error):
        return error_page(500, "Something went wrong", "We could not finish that request. Please try again in a moment.")

    @app.errorhandler(db.DBError)
    def database_error(error):
        app.logger.error("Database error: %s", error)
        return error_page(503, "Service busy", db.error_message(error))
