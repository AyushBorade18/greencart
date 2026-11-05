"""
fakedb.py - a pretend database for testing the website WITHOUT MySQL.

It answers every query the website makes with a small, realistic data set,
and records every write, so tests can check what the website tried to save.
(The real MySQL rules are checked by scripts/selftest_db.py.)
"""
import re
from datetime import datetime, date
from decimal import Decimal as D

NOW = datetime(2026, 10, 27, 12, 0, 0)

PRODUCT = dict(product_id=1, product_name="Test Headphones", description="Nice", image_url=None, price=D("1999.00"),
               is_active=1, created_at=NOW, category_id=1, category_name="Electronics", quantity_available=50,
               reorder_level=5, stock_status="In Stock", avg_rating=D("4.50"), review_count=2, units_sold=7)
ORDER = dict(order_id=11, customer_id=1, customer_name="Test Customer", city="Pune", order_date=NOW,
             total_amount=D("3998.00"), order_status="Confirmed", shipping_address="1 Road, Pune 411001",
             payment_method="UPI", payment_status="Paid", product_lines=1, total_units=2,
             tracking_number=None, expected_delivery=None, delivered_date=None)
LISTING = dict(listing_id=5, seller_id=2, business_name="Traders", seller_status="Approved", product_id=None,
               store_product=None, item_name="New Gadget", category_id=1, category_name="Electronics",
               description="d", image_url=None, unit_price=D("500.00"), quantity_available=100, min_order_qty=5,
               is_active=1, created_at=NOW, product_name="New Gadget")
PO = dict(po_id=9, product_id=1, product_name="Test Headphones", product_image=None, category_name="Electronics",
          stock_now=3, seller_id=2, seller_name="Traders", listing_id=5, quantity_requested=10,
          quantity_offered=10, unit_price=D("500.00"), max_unit_price=D("600.00"), total_cost=D("5000.00"),
          status="Requested", admin_note=None, seller_note=None, created_at=NOW, updated_at=NOW,
          shipped_at=None, received_at=None)
SUPPLY = dict(product_id=1, product_name="Test Headphones", is_active=1, image_url=None, category_name="Electronics",
              quantity_available=3, reorder_level=5, stock_status="Low Stock", seller_offers=1, seller_supply=100,
              best_price=D("500.00"), active_po_id=None, active_po_status=None)
SELLER_SUMMARY = dict(seller_id=2, business_name="Traders", contact_name="Sam", email="s@x.com", phone="9111111111",
                      city="Pune", gst_number=None, status="Pending", created_at=NOW, listings=1, orders_count=0,
                      earned=D("0.00"))

TABLES = {
    "vw_product_catalog": [PRODUCT],
    "vw_order_summary": [ORDER],
    "vw_order_details": [dict(order_id=11, product_id=1, product_name="Test Headphones", category_name="Electronics",
                              quantity=2, unit_price=D("1999.00"), subtotal=D("3998.00"))],
    "order_status_log": [dict(old_status=None, new_status="Confirmed", changed_at=NOW,
                              order_id=11)],
    "vw_review_details": [dict(review_id=1, product_id=1, product_name="Test Headphones", customer_name="Ann",
                               rating=5, comment="Great", review_date=NOW)],
    "category": [dict(category_id=1, category_name="Electronics", description="Gadgets", product_count=1)],
    "vw_product_supply": [SUPPLY],
    "vw_seller_listings": [LISTING],
    "vw_purchase_orders": [PO],
    "vw_seller_summary": [SELLER_SUMMARY],
    "purchase_order_log": [dict(old_status=None, new_status="Requested", changed_at=NOW)],
    "vw_top_customers": [dict(customer_id=1, full_name="Test Customer", email="t@x.com", city="Pune", orders_count=1,
                              total_spent=D("3998.00"), tier="Bronze", phone="9000000000", created_at=NOW)],
    "vw_sales_by_category": [dict(category_id=1, category_name="Electronics", units_sold=2, revenue=D("3998.00"))],
    "vw_top_products": [dict(product_id=1, product_name="Test Headphones", category_name="Electronics",
                             units_sold=2, revenue=D("3998.00"))],
    "vw_monthly_sales": [dict(sales_month="2026-10", orders_count=1, revenue=D("3998.00"))],
    "payment": [dict(payment_method="UPI", orders_count=1, amount=D("3998.00"))],
    "orders": [dict(order_status="Confirmed", n=1)],
    "price_history": [dict(changed_at=NOW, product_name="Test Headphones", old_price=D("1999"), new_price=D("2199"))],
    "low_stock_alert": [dict(alert_time=NOW, product_name="Test Headphones", quantity_left=3)],
    "product": [dict(product_id=1, category_id=1, product_name="Test Headphones", description="Nice", image_url=None,
                     price=D("1999.00"), is_active=1, category_name="Electronics", quantity_available=50,
                     reorder_level=5, stock_status="In Stock")],
    "seller_listing": [LISTING],
    "review": [dict(product_id=1)],
    "wishlist": [dict(product_id=1)],
    "address": [dict(address_id=3, customer_id=1, label="Home", full_name="Test Customer", phone="9000000000",
                     line1="1 Road", city="Pune", pincode="411001", is_default=1, created_at=NOW)],
    "product_image": [dict(image_id=1, image_url="https://img/a.png"), dict(image_id=2, image_url="https://img/b.png")],
    "coupon": [dict(coupon_id=1, code="SAVE10", description="d", discount_type="percent", discount_value=D("10"),
                    min_order_amount=D("0"), max_discount=None, usage_limit=None, per_customer_limit=1,
                    valid_from=None, valid_to=None, is_active=1, created_at=NOW, used=0, given=D("0"))],
    "vw_returns": [dict(return_id=5, order_id=11, product_id=1, product_name="Test Headphones", image_url=None,
                        customer_id=1, customer_name="Test Customer", customer_email="t@x.com", quantity=1,
                        reason="Damaged or defective", details="broken", refund_amount=D("1999.00"),
                        status="Requested", restock=1, admin_note=None, created_at=NOW, updated_at=NOW)],
    "return_request": [dict(product_id=1, qty=1)],
    "purchase_order": [dict(po_id=9)],
}


class FakeDB:
    """Install with  fake.install()  and remove with  fake.uninstall()."""

    def __init__(self):
        self.writes = []          # (sql, params) of INSERT/UPDATE/DELETE
        self.calls = []           # (procedure, args)
        self.overrides = {}       # regex -> rows  (a test can change an answer)
        self.fail_procedures = {}  # procedure name -> exception to raise
        self.seller_status = "Approved"
        self.customer_exists = True
        self.empty = False         # True = a brand-new database with no rows at all

    # ---- helpers
    def _rows(self, sql):
        for pattern, rows in self.overrides.items():
            if re.search(pattern, sql, re.I | re.S):
                return rows
        if self.empty:
            return []
        if "JOIN vw_product_catalog" in sql:                      # wishlist page: full product rows
            return [dict(r, added_at=NOW) for r in TABLES["vw_product_catalog"]]
        if "DATE_FORMAT(received_at" in sql:                      # seller earnings by month
            return [dict(month="2026-10", orders_count=2, amount=D("5000.00"))]
        m = re.search(r"\bFROM\s+([a-z_]+)", sql, re.I)
        name = m.group(1).lower() if m else ""
        rows = TABLES.get(name, [])
        return [dict(r) for r in rows]

    # ---- the db module interface
    def query_all(self, sql, params=()):
        return self._rows(sql)

    def query_one(self, sql, params=()):
        s = " ".join(sql.split())
        for pattern, rows in self.overrides.items():
            if re.search(pattern, s, re.I):
                return dict(rows[0]) if rows else None
        if re.search(r"AS lo\b", s):
            return dict(lo=None, hi=None) if self.empty else dict(lo=D("500"), hi=D("2500"))
        if self.empty and "FROM admin" not in s and "AS new_items" not in s and "AS open_requests" not in s \
                and "FROM seller WHERE seller_id" not in s and "FROM customer WHERE customer_id" not in s:
            return None
        if "FROM admin" in s:
            from werkzeug.security import generate_password_hash
            return dict(admin_id=1, password_hash=generate_password_hash("secret-pass"))
        if "JOIN customer c" in s and "o.order_id" in s:
            return dict(email="t@x.com", full_name="Test Customer")
        if "AS in_cart" in s:
            return dict(in_cart=0, delivered=0, reviewed=0)
        if "AS cart_count" in s:
            return dict(cart_count=0, wish_count=1, wish_ids="1")
        if "AS wishlist" in s and "AS returns" in s:
            return dict(orders=1, wishlist=1, returns=0)
        if "FROM order_extra" in s:
            return dict(subtotal=D("3998.00"), coupon_code=None, discount_amount=D("0"), delivery_fee=D("0"))
        if "FROM customer WHERE customer_id" in s and "AS orders" not in s:
            return dict(customer_id=1, full_name="Test Customer", email="t@x.com", phone="9000000000",
                        address="1 Road", city="Pune", pincode="411001", created_at=NOW)
        if "AS earned" in s and "AS completed" in s:
            return dict(earned=D("5000"), pending=D("500"), completed=2)
        if "FROM address WHERE address_id" in s:
            return dict(self._rows("SELECT * FROM address")[0])
        if "AS new_items" in s:
            return dict(orders=5, revenue=D("9999.00"), customers=3, low_stock=1, pending_sellers=1, offers=0,
                        to_receive=0, to_process=2, new_items=1, categories=1, products=1, approved_sellers=1,
                        open_returns=1)
        if "AS open_requests" in s:
            return dict(listings=1, units=100, open_requests=0, new_orders=1, to_ship=0, earned=D("0"))
        if "FROM seller WHERE seller_id" in s and "business_name" in s:
            return dict(seller_id=2, business_name="Traders", contact_name="Sam", email="s@x.com", phone="9111111111",
                        address="2 Lane", city="Pune", pincode="411002", gst_number=None, status=self.seller_status)
        if "FROM seller WHERE seller_id" in s:
            return dict(seller_id=2)
        if "FROM customer WHERE customer_id" in s:
            return dict(customer_id=1, full_name="Test Customer", email="t@x.com", phone="9000000000",
                        address="1 Road", city="Pune", pincode="411001", created_at=NOW)
        if "FROM customer WHERE" in s or "FROM seller WHERE" in s:
            return None                                   # account lookups: nobody yet
        if "FROM product WHERE product_id" in s and "category_id" in s and "SELECT *" not in s:
            return dict(product_id=1, product_name="Test Headphones", category_id=1)
        if "FROM seller_listing WHERE listing_id" in s:
            return dict(LISTING)
        if "FROM seller_listing" in s:
            return None
        if "FROM orders WHERE order_id" in s:
            return dict(order_id=11)
        rows = self._rows(sql)
        return rows[0] if rows else None

    def scalar(self, sql, params=(), default=0):
        s = " ".join(sql.split())
        if "COUNT(*)" in s and ("o.order_status = 'Delivered'" in s or "FROM review" in s):
            return 0
        row = self.query_one(sql, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    def execute(self, sql, params=()):
        self.writes.append((" ".join(sql.split()), params))
        return 77

    def call_procedure(self, name, args=()):
        self.calls.append((name, tuple(args)))
        if name in self.fail_procedures:
            raise self.fail_procedures[name]
        out = list(args)
        if name == "sp_place_order":
            out[6] = 11
        elif name == "sp_validate_coupon":
            out[3], out[4] = 1, D("100.00")
        elif name == "sp_request_return":
            out[7] = 5
        elif name == "sp_add_product":
            out[7] = 3
        elif name == "sp_add_listing_to_store":
            out[2] = 4
        elif name == "sp_order_from_listing":
            out[3] = 9
        elif name == "sp_raise_restock_request":
            out[4] = 9
        return out

    # ---- install into the db module
    def install(self):
        from shop import db
        self._saved = {n: getattr(db, n) for n in ("query_all", "query_one", "scalar", "execute", "call_procedure")}
        for n in self._saved:
            setattr(db, n, getattr(self, n))

    def uninstall(self):
        from shop import db
        for n, f in self._saved.items():
            setattr(db, n, f)
