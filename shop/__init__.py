"""
GreenCart - e-commerce order processing system (Flask + MySQL + Supabase).

create_app() builds the website. Vercel finds it through app.py in the
project root; on your own computer run:   python app.py
"""
import logging
import os
from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config
from .routes import register_routes
from .security import same_origin_only

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app():
    # Vercel serves the "public" folder itself, so Flask's own static folder is off
    app = Flask(__name__, static_folder=None)
    # Vercel sits behind a proxy: trust its forwarded host / protocol headers
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    logging.basicConfig(level=logging.INFO)

    secret = config.SECRET_KEY
    if not secret:
        if config.ON_VERCEL:
            raise RuntimeError("SECRET_KEY is not set. Add it in Vercel > Settings > Environment Variables.")
        secret = "local-development-key-not-for-production"
        app.logger.warning("SECRET_KEY is not set - using an insecure development key.")

    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.ON_VERCEL,
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,        # 2 MB picture + form fields
        TEMPLATES_AUTO_RELOAD=config.DEBUG,
    )
    app.public_dir = os.path.join(ROOT, "public")

    app.before_request(same_origin_only)
    register_routes(app)
    return app
