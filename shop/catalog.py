"""catalog.py - small helpers about categories, shared by the seller and admin pages."""
from . import db
from .utils import forget


def clean_category_name(text):
    """One-line, single-spaced, max 50 characters ('' when nothing useful)."""
    return " ".join((text or "").split())[:50]


def get_or_create_category(name):
    """
    Returns the id of the category with this name, creating it if it does not exist.
    MySQL compares names without caring about capital letters, so 'footwear' and
    'Footwear' are the same category (no duplicates).
    """
    name = clean_category_name(name)
    if not name:
        return None
    row = db.query_one("SELECT category_id FROM category WHERE category_name = %s", (name,))
    if row:
        return row["category_id"]
    try:
        new_id = db.execute("INSERT INTO category (category_name) VALUES (%s)", (name,))
    except db.DBError as e:
        if getattr(e, "errno", None) != 1062:          # 1062 = someone created it a moment ago
            raise
        row = db.query_one("SELECT category_id FROM category WHERE category_name = %s", (name,))
        return row["category_id"] if row else None
    forget("nav_categories")                            # the shop menu shows the new category
    return new_id


# ---------------------------------------------------------------------
#  Several pictures per product
#  product_image holds every picture; product.image_url always mirrors the FIRST one
#  (the cover), so the shop cards keep working without changes.
# ---------------------------------------------------------------------
def sync_cover(product_id):
    """Make product.image_url equal to the first picture (or empty when none are left)."""
    first = db.query_one("SELECT image_url FROM product_image WHERE product_id = %s "
                         "ORDER BY sort_order, image_id LIMIT 1", (product_id,))
    db.execute("UPDATE product SET image_url = %s WHERE product_id = %s",
               (first["image_url"] if first else None, product_id))


def add_images(product_id, urls, limit):
    """Add pictures (a list of web addresses). Returns how many were added."""
    urls = [u for u in urls if u]
    if not urls:
        return 0
    existing = db.query_all("SELECT image_url FROM product_image WHERE product_id = %s "
                            "ORDER BY sort_order, image_id", (product_id,))
    if not existing:                                       # an older single cover picture becomes picture no. 1
        cover = db.query_one("SELECT image_url FROM product WHERE product_id = %s", (product_id,))
        if cover and cover.get("image_url") and cover["image_url"] not in urls:
            db.execute("INSERT INTO product_image (product_id, image_url, sort_order) VALUES (%s, %s, 0)",
                       (product_id, cover["image_url"]))
            existing = [cover]
    room = max(0, limit - len(existing))
    urls = urls[:room]
    if not urls:
        return 0
    start = db.scalar("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM product_image WHERE product_id = %s",
                      (product_id,))
    for offset, url in enumerate(urls):
        db.execute("INSERT INTO product_image (product_id, image_url, sort_order) VALUES (%s, %s, %s)",
                   (product_id, url, int(start) + offset))
    sync_cover(product_id)
    return len(urls)
