"""
config.py - every setting of the website, read in ONE place.

Values come from environment variables. On your own computer they are
read from a file called  .env  (copy .env.example). On Vercel you type
them in  Project > Settings > Environment Variables.
"""
import os

try:                                    # .env is optional (only for local work)
    from dotenv import load_dotenv
    load_dotenv(override=True)      # the .env file always wins over old Windows settings
except ImportError:                     # pragma: no cover
    pass


def _get(name, default=""):
    value = os.environ.get(name)
    return default if value is None else value.strip()


def _flag(name, default):
    return _get(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


ON_VERCEL = bool(os.environ.get("VERCEL"))

# ---- MySQL --------------------------------------------------------------
DB = {
    "host": _get("DB_HOST", "localhost"),
    "port": int(_get("DB_PORT", "3306") or 3306),
    "user": _get("DB_USER", "root"),
    "password": _get("DB_PASSWORD", ""),
    "database": _get("DB_NAME", "greencart"),
}
DB_SSL = _flag("DB_SSL", False)          # cloud MySQL needs DB_SSL=true

# ---- Supabase (login + product pictures) --------------------------------
SUPABASE_URL = _get("SUPABASE_URL").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = _get("SUPABASE_PUBLISHABLE_KEY") or _get("SUPABASE_ANON_KEY")
SUPABASE_SECRET_KEY = _get("SUPABASE_SECRET_KEY") or _get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = _get("SUPABASE_BUCKET", "product-images")
SUPABASE_READY = bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)

# true  = a new person must click the link in the confirmation email
# false = no email step (handy while testing; Supabase also has to allow it)
REQUIRE_EMAIL_CONFIRMATION = _flag("REQUIRE_EMAIL_CONFIRMATION", True)

# The public address of the website (used inside the confirmation email)
SITE_URL = _get("SITE_URL").rstrip("/")
if not SITE_URL and os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
    SITE_URL = "https://" + os.environ["VERCEL_PROJECT_PRODUCTION_URL"]

# ---- Website ------------------------------------------------------------
SECRET_KEY = _get("SECRET_KEY")
DEBUG = _flag("FLASK_DEBUG", False)

# ---- Shop rules ---------------------------------------------------------
STORE_NAME = _get("STORE_NAME", "GreenCart")
DELIVERY_FEE = int(_get("DELIVERY_FEE", "40") or 0)                 # rupees, for small orders
FREE_DELIVERY_ABOVE = int(_get("FREE_DELIVERY_ABOVE", "499") or 0)  # free delivery from this amount (0 = never free)
RETURN_WINDOW_DAYS = int(_get("RETURN_WINDOW_DAYS", "7") or 7)
MAX_PRODUCT_IMAGES = 6
RETURN_REASONS = ["Damaged or defective", "Wrong item received", "Not as described", "Size or fit issue",
                  "Quality not as expected", "No longer needed", "Other"]

# ---- Order emails (optional: leave SMTP_HOST empty to switch emails off) --
SMTP_HOST = _get("SMTP_HOST")
SMTP_PORT = int(_get("SMTP_PORT", "587") or 587)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
MAIL_FROM = _get("MAIL_FROM") or SMTP_USER
EMAILS_READY = bool(SMTP_HOST and MAIL_FROM)

PAYMENT_METHODS = ["UPI", "Credit Card", "Debit Card", "Net Banking", "Cash on Delivery"]
ORDER_STATUSES = ["Confirmed", "Packed", "Shipped", "Delivered", "Cancelled"]

# Which statuses an order may move to next (the database trigger enforces
# the same rule; this only keeps the drop-down clean)
NEXT_STATUS = {
    "Confirmed": ["Packed", "Cancelled"],
    "Packed":    ["Shipped", "Cancelled"],
    "Shipped":   ["Delivered"],
    "Delivered": [],
    "Cancelled": [],
}

# Limits that keep typing mistakes (and abuse) out of the database
MAX_PRICE = 9_999_999          # rupees
MAX_QUANTITY = 100_000
