"""utils.py - small tools shared by all the page files (no database here)."""
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import request, url_for

from . import config


# ---------------------------------------------------------------------
#  Reading numbers from forms (never trust what the browser sends)
# ---------------------------------------------------------------------
def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def to_decimal(value):
    """A finite number, or None. 'NaN', 'Infinity' and 'abc' all give None."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def parse_price(value, label="Price"):
    """
    Returns (price, error). The price is rounded to 2 decimals and must be
    above zero, so negative and zero prices can never get in.
    """
    number = to_decimal(value)
    if number is None:
        return None, "%s must be a number." % label
    if number.copy_abs() > config.MAX_PRICE:           # check the size BEFORE rounding (1e99 would crash)
        return None, "%s is too large." % label if number > 0 else "%s must be greater than 0." % label
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if number <= 0:
        return None, "%s must be greater than 0." % label
    return number, None


def parse_quantity(value, label="Quantity", minimum=0):
    """Returns (whole number, error). Negative numbers are refused."""
    number = to_int(value)
    if number is None:
        return None, "%s must be a whole number." % label
    if number < minimum:
        if minimum <= 0:
            return None, "%s cannot be negative." % label
        return None, "%s must be at least %d." % (label, minimum)
    if number > config.MAX_QUANTITY:
        return None, "%s is too large." % label
    return number, None


# ---------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------
def add_pct(rows, key):
    """Adds a 'pct' (0-100) to every row so the report pages can draw bars."""
    top = max([float(r[key]) for r in rows] or [0]) or 1
    for r in rows:
        r["pct"] = round(float(r[key]) / top * 100)
    return rows


def is_local_path(target):
    return bool(target) and target.startswith("/") and not target.startswith("//") and "\\" not in target


def safe_next(default_endpoint):
    """Where to go after a form: the 'next' address if it is on our own site."""
    target = request.form.get("next", "")
    return target if is_local_path(target) else url_for(default_endpoint)


def is_image_link(link):
    return link.startswith(("http://", "https://", "/"))


# ---------------------------------------------------------------------
#  A tiny cache for data that hardly changes (the category menu)
# ---------------------------------------------------------------------
_cache = {}


def cached(key, seconds, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < seconds:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def forget(key=None):
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)
