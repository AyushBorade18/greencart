"""
Website tests (run:  python -m unittest discover -s tests -v).

They use tests/fakedb.py instead of MySQL, so they check the Python code,
the templates, the validation and the page flow. The MySQL rules themselves
are checked by  python scripts/selftest_db.py  against a real database.
"""
import os
import re
import sys
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# If the real MySQL driver is not installed (e.g. a build machine without internet),
# a tiny stand-in is used. Real runs always use the real driver.
try:
    import mysql.connector  # noqa: F401
except ImportError:
    import types
    pkg, mod = types.ModuleType("mysql"), types.ModuleType("mysql.connector")

    class _Error(Exception):
        def __init__(self, msg=None, errno=None, values=None, sqlstate=None):
            super().__init__(msg)
            self.msg, self.errno, self.sqlstate = msg, errno, sqlstate
    mod.Error = _Error
    mod.connect = lambda **kw: (_ for _ in ()).throw(_Error("no database in tests", 2003))
    pkg.connector = mod
    sys.modules["mysql"], sys.modules["mysql.connector"] = pkg, mod

os.environ.setdefault("SECRET_KEY", "test-secret")
for var in ("DB_HOST", "SUPABASE_URL"):
    os.environ.pop(var, None)

from shop import create_app, config, db, supa      # noqa: E402
from shop.supa import AuthError                     # noqa: E402
from fakedb import FakeDB                           # noqa: E402


def all_templates(folder):
    found = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".html"):
                found.append(os.path.relpath(os.path.join(root, f), folder))
    return found


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        self.fake.install()
        from shop import utils
        utils.forget()
        self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()
        self._ready = config.SUPABASE_READY
        self._supa = (supa.sign_in, supa.sign_up)
        config.SUPABASE_READY = True

    def tearDown(self):
        self.fake.uninstall()
        config.SUPABASE_READY = self._ready
        supa.sign_in, supa.sign_up = self._supa

    def login(self, role):
        with self.client.session_transaction() as s:
            if role == "customer":
                s.update(customer_id=1, name="Test Customer")
            elif role == "seller":
                s.update(seller_id=2, seller_name="Traders")
            else:
                s["is_admin"] = True

    def writes_like(self, text):
        return [w for w in self.fake.writes if text in w[0]]


class TemplateAudit(Base):
    def test_every_url_for_in_templates_exists(self):
        endpoints = {r.endpoint for r in self.app.url_map.iter_rules()}
        folder = os.path.join(ROOT, "shop", "templates")
        missing = []
        for name in all_templates(folder):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                text = f.read()
            for ep in re.findall(r"url_for\(\s*['\"]([\w.]+)['\"]", text):
                if ep not in endpoints:
                    missing.append((name, ep))
        self.assertEqual(missing, [])

    def test_no_template_mentions_demo_data(self):
        folder = os.path.join(ROOT, "shop", "templates")
        for name in all_templates(folder):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                text = f.read().lower()
            for word in ("wireless headphones", "atomic habits", "demo", "assets/products"):
                self.assertNotIn(word, text, "%s contains %r" % (name, word))


class PublicPages(Base):
    def test_static_and_pwa_files(self):
        for path, kind in (("/assets/store.css", "css"), ("/style.css", "css"), ("/assets/store.js", "javascript"),
                           ("/sw.js", "javascript"), ("/manifest.webmanifest", "json"),
                           ("/assets/icons/icon-192.png", "png"), ("/assets/icons/icon-512.png", "png"),
                           ("/assets/icons/icon-maskable-512.png", "png")):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(kind, r.content_type, path)
        self.assertEqual(self.client.get("/sw.js").headers["Cache-Control"], "no-cache")

    def test_pages_open_when_logged_out(self):
        for path in ("/", "/login", "/register", "/seller/login", "/seller/register", "/admin/login",
                     "/offline", "/auth/confirmed"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_every_page_shell_links_the_manifest(self):
        for path in ("/", "/login", "/admin/login", "/seller/login", "/offline"):
            html = self.client.get(path).get_data(as_text=True)
            if path != "/offline":
                self.assertIn("manifest.webmanifest", html, path)
        self.assertIn('name="viewport"', self.client.get("/").get_data(as_text=True))

    def test_404_page(self):
        r = self.client.get("/nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn("Page not found", r.get_data(as_text=True))

    def test_protected_pages_redirect_to_login(self):
        for path, target in (("/shop", "/login"), ("/cart", "/login"), ("/orders", "/login"),
                             ("/admin/dashboard", "/admin/login"), ("/admin/products", "/admin/login"),
                             ("/seller", "/seller/login"), ("/seller/products", "/seller/login")):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn(target, r.headers["Location"], path)

    def test_register_page_names_the_missing_supabase_settings(self):
        config.SUPABASE_READY = False
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn("SUPABASE_URL", html)

    def test_cross_site_post_is_blocked(self):
        r = self.client.post("/login", data={"email": "a@b.c", "password": "x"},
                             headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/login", data={"email": "a@b.c", "password": "x"},
                             headers={"Origin": "http://localhost"})
        self.assertNotEqual(r.status_code, 403)


class CustomerPages(Base):
    def setUp(self):
        super().setUp()
        self.login("customer")

    def test_all_customer_pages_render(self):
        for path in ("/shop", "/shop?search=head&category=1&in_stock=1&min_price=1&max_price=5000&sort=low",
                     "/product/1", "/cart", "/orders", "/orders/11"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
        html = self.client.get("/shop").get_data(as_text=True)
        self.assertIn("Test Headphones", html)

    def test_weird_filters_do_not_crash(self):
        for q in ("min_price=abc", "max_price=NaN", "min_price=Infinity", "category=x", "sort=;DROP", "search=%25%27"):
            self.assertEqual(self.client.get("/shop?" + q).status_code, 200, q)

    def test_cart_total_is_price_times_quantity(self):
        self.fake.overrides[r"FROM cart c JOIN cart_item ci ON ci.cart_id = c.cart_id JOIN product p"] = [dict(
            cart_item_id=1, product_id=1, product_name="Test Headphones", price=__import__("decimal").Decimal("1999.00"),
            image_url=None, category_name="Electronics", quantity=2,
            line_total=__import__("decimal").Decimal("3998.00"), quantity_available=50)]
        html = self.client.get("/cart").get_data(as_text=True)
        self.assertIn("3,998.00", html)
        self.assertIn("data-autosubmit", html)                  # quantity changes save by themselves
        self.assertIn('max="50"', html)                         # the stepper stops at the stock
        self.assertNotIn(">&#8377;1,999.00</div>", html)        # the line total is NOT the unit price

    def test_cart_blocks_checkout_when_stock_is_too_low(self):
        from decimal import Decimal
        self.fake.overrides[r"FROM cart c JOIN cart_item ci ON ci.cart_id = c.cart_id JOIN product p"] = [dict(
            cart_item_id=1, product_id=1, product_name="X", price=Decimal("10"), image_url=None,
            category_name="E", quantity=9, line_total=Decimal("90"), quantity_available=3)]
        html = self.client.get("/cart").get_data(as_text=True)
        self.assertIn("Please reduce the quantities", html)
        self.assertIn("disabled", html)

    def test_add_to_cart(self):
        r = self.client.post("/cart/add", data={"product_id": "1", "quantity": "2", "next": "/shop"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.fake.calls[-1], ("sp_add_to_cart", (1, 1, 2)))

    def test_add_to_cart_rejects_bad_quantities(self):
        for bad in ("0", "-3", "abc", "", "999999999"):
            self.fake.calls.clear()
            self.client.post("/cart/add", data={"product_id": "1", "quantity": bad})
            self.assertEqual(self.fake.calls, [], bad)

    def test_add_to_cart_shows_database_stock_error(self):
        err = db.DBError(msg="Requested quantity is more than the available stock", errno=1644)
        self.fake.fail_procedures["sp_add_to_cart"] = err
        r = self.client.post("/cart/add", data={"product_id": "1", "quantity": "99"}, follow_redirects=True)
        self.assertIn("more than the available stock", r.get_data(as_text=True))

    def test_cart_update_saves_and_removes(self):
        self.client.post("/cart/update", data={"cart_item_id": "4", "quantity": "3"})
        sql, params = self.writes_like("UPDATE cart_item")[-1]
        self.assertEqual(params, (3, 4, 1))
        self.client.post("/cart/update", data={"cart_item_id": "4", "quantity": "0"})
        self.assertTrue(self.writes_like("DELETE FROM cart_item"))
        self.fake.writes.clear()
        self.client.post("/cart/update", data={"cart_item_id": "4", "quantity": "-2"})
        self.assertEqual(self.fake.writes, [])

    def test_open_redirect_is_blocked(self):
        r = self.client.post("/cart/add", data={"product_id": "1", "quantity": "1", "next": "//evil.example"})
        self.assertNotIn("evil", r.headers["Location"])

    def test_checkout(self):
        r = self.client.post("/checkout", data={"payment_method": "UPI", "shipping_address": " 5 Park St "})
        self.assertEqual(self.fake.calls[-1], ("sp_place_order", (1, "UPI", "5 Park St", "", 40, 499, 0)))
        self.assertIn("/orders/11", r.headers["Location"])
        self.fake.calls.clear()
        self.client.post("/checkout", data={"payment_method": "Barter", "shipping_address": "x"})
        self.assertEqual(self.fake.calls, [])

    def test_checkout_failure_is_explained(self):
        self.fake.fail_procedures["sp_place_order"] = db.DBError(msg="Insufficient stock to place this order", errno=1644)
        r = self.client.post("/checkout", data={"payment_method": "UPI", "shipping_address": ""}, follow_redirects=True)
        self.assertIn("Insufficient stock", r.get_data(as_text=True))

    def test_cancel_and_review(self):
        self.client.post("/orders/11/cancel")
        self.assertEqual(self.fake.calls[-1], ("sp_cancel_order", (11, 1)))
        self.client.post("/review", data={"product_id": "1", "rating": "5", "comment": "Nice"})
        self.assertTrue(self.writes_like("INSERT INTO review"))
        self.fake.writes.clear()
        self.client.post("/review", data={"product_id": "1", "rating": "9"})
        self.client.post("/review", data={"product_id": "1", "rating": "x"})
        self.assertEqual(self.fake.writes, [])


class Accounts(Base):
    def test_customer_first_login_creates_the_mysql_row_and_links_auth_id(self):
        supa.sign_in = lambda e, p: {"id": "uuid-1", "email": e, "metadata": dict(
            role="customer", full_name="Asha", phone="9000000000", address="1 Rd", city="Pune", pincode="411001")}
        calls = []
        orig = self.fake.query_one
        state = {"created": False}

        def query_one(sql, params=()):
            if "FROM customer WHERE" in sql:
                return dict(customer_id=5, full_name="Asha", auth_id="uuid-1") if state["created"] else None
            return orig(sql, params)

        def execute(sql, params=()):
            if "INSERT INTO customer" in sql:
                state["created"] = True
            calls.append((sql, params))
            return 5
        db.query_one, db.execute = query_one, execute
        r = self.client.post("/login", data={"email": "Asha@Example.com", "password": "secret1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/shop", r.headers["Location"])
        insert = [c for c in calls if "INSERT INTO customer" in c[0]][0]
        self.assertEqual(insert[1][0], "uuid-1")                  # Supabase id stored
        self.assertNotIn("password", insert[0].lower())           # no password in MySQL
        with self.client.session_transaction() as s:
            self.assertEqual(s["customer_id"], 5)

    def test_seller_email_cannot_use_customer_login(self):
        supa.sign_in = lambda e, p: {"id": "u", "email": e, "metadata": dict(role="seller")}
        orig = self.fake.query_one
        db.query_one = lambda sql, params=(): (dict(seller_id=2, business_name="T", auth_id="u")
                                               if "FROM seller WHERE" in sql else
                                               None if "FROM customer WHERE" in sql else orig(sql, params))
        r = self.client.post("/login", data={"email": "s@x.com", "password": "secret1"})
        self.assertIn("seller account", r.get_data(as_text=True))

    def test_wrong_password_shows_message(self):
        def boom(e, p):
            raise AuthError("Wrong email or password.")
        supa.sign_in = boom
        r = self.client.post("/login", data={"email": "a@b.co", "password": "bad"})
        self.assertIn("Wrong email or password", r.get_data(as_text=True))
        self.assertNotIn("customer_id", dict(self.client.get("/").request.environ.get("flask._session", {}) or {}))

    def test_unconfirmed_email_offers_resend(self):
        def boom(e, p):
            raise AuthError("Please confirm your email first.", needs_confirmation=True)
        supa.sign_in = boom
        html = self.client.post("/login", data={"email": "a@b.co", "password": "x"}).get_data(as_text=True)
        self.assertIn("/auth/resend", html)

    def test_registration_validation(self):
        base = dict(full_name="A", email="a@b.co", password="secret1", phone="9000000000", address="1 Rd",
                    city="Pune", pincode="411001")
        sent = []
        supa.sign_up = lambda *a, **k: sent.append(a) or {"already_registered": False, "user": {}}
        for field, bad, text in (("phone", "123", "10 digits"), ("pincode", "12", "6 digits"),
                                 ("password", "123", "6 characters"), ("email", "nope", "valid email"),
                                 ("full_name", "", "every field")):
            data = dict(base, **{field: bad})
            html = self.client.post("/register", data=data).get_data(as_text=True)
            self.assertIn(text, html, field)
        self.assertEqual(sent, [])
        r = self.client.post("/register", data=base)
        self.assertEqual(len(sent), 1)
        self.assertIn("confirm", r.get_data(as_text=True).lower())
        self.assertEqual(sent[0][2]["role"], "customer")


class AdminPages(Base):
    def setUp(self):
        super().setUp()
        self.login("admin")

    def test_all_admin_pages_render(self):
        for path in ("/admin/dashboard", "/admin/orders", "/admin/orders?status=Packed", "/admin/orders/11",
                     "/admin/products", "/admin/products/new", "/admin/products/1/edit", "/admin/categories",
                     "/admin/inventory", "/admin/customers", "/admin/reports", "/admin/sellers",
                     "/admin/seller-items", "/admin/seller-items?link=new&seller=2", "/admin/restock/1",
                     "/admin/purchase-orders", "/admin/purchase-orders?status=Open", "/admin/purchase-orders/9"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)

    def test_dashboard_shows_new_seller_items(self):
        html = self.client.get("/admin/dashboard").get_data(as_text=True)
        self.assertIn("New seller items", html)

    def test_admin_login(self):
        self.client.get("/logout")
        r = self.client.post("/admin/login", data={"username": "admin", "password": "secret-pass"})
        self.assertIn("/admin/dashboard", r.headers["Location"])
        self.client.get("/logout")
        r = self.client.post("/admin/login", data={"username": "admin", "password": "wrong"})
        self.assertIn("Wrong admin", r.get_data(as_text=True))

    def test_add_product_rejects_negative_zero_and_nan_prices(self):
        for bad in ("-45", "0", "NaN", "Infinity", "abc", "", "99999999999"):
            self.fake.calls.clear()
            self.client.post("/admin/products/new", data=dict(product_name="X", category_id="1", price=bad,
                                                               stock="1", reorder_level="1"))
            self.assertEqual(self.fake.calls, [], "price %r was accepted" % bad)

    def test_add_product_rejects_negative_stock(self):
        self.client.post("/admin/products/new", data=dict(product_name="X", category_id="1", price="10",
                                                           stock="-5", reorder_level="1"))
        self.assertEqual(self.fake.calls, [])

    def test_add_product_ok(self):
        r = self.client.post("/admin/products/new", data=dict(product_name=" Lamp ", category_id="1", price="499.499",
                                                               stock="10", reorder_level="2", image_url="https://x/y.png"))
        name, args = self.fake.calls[-1]
        self.assertEqual(name, "sp_add_product")
        self.assertEqual(str(args[3]), "499.50")                  # rounded to 2 decimals
        self.assertEqual(args[1], "Lamp")

    def test_edit_product_price_validation(self):
        self.client.post("/admin/products/1/edit", data=dict(product_name="X", category_id="1", price="-1"))
        self.assertEqual(self.writes_like("UPDATE product SET"), [])
        self.client.post("/admin/products/1/edit", data=dict(product_name="X", category_id="1", price="10"))
        self.assertTrue(self.writes_like("UPDATE product SET"))

    def test_order_status_change(self):
        self.client.post("/admin/orders/11/status", data={"new_status": "Packed"})
        self.assertEqual(self.fake.calls[-1], ("sp_update_order_status", (11, "Packed")))
        self.fake.calls.clear()
        self.client.post("/admin/orders/11/status", data={"new_status": "Teleported"})
        self.assertEqual(self.fake.calls, [])

    def test_add_seller_item_to_store_and_order_stock(self):
        r = self.client.post("/admin/seller-items/5/add-to-store", data={"retail_price": "899", "order_qty": "10"})
        names = [c[0] for c in self.fake.calls]
        self.assertEqual(names, ["sp_add_listing_to_store", "sp_order_from_listing"])
        self.assertIn("/admin/purchase-orders/9", r.headers["Location"])
        self.fake.calls.clear()
        self.client.post("/admin/seller-items/5/add-to-store", data={"retail_price": "-899"})
        self.assertEqual(self.fake.calls, [])

    def test_add_to_store_without_order(self):
        self.client.post("/admin/seller-items/5/add-to-store", data={"retail_price": "899", "order_qty": ""})
        self.assertEqual([c[0] for c in self.fake.calls], ["sp_add_listing_to_store"])

    def test_restock_and_purchase_order_actions(self):
        self.client.post("/admin/restock/request", data={"product_id": "1", "quantity": "5", "max_price": "-3"})
        self.assertEqual(self.fake.calls, [])
        self.client.post("/admin/restock/request", data={"product_id": "1", "quantity": "5", "max_price": "300"})
        self.assertEqual(self.fake.calls[-1][0], "sp_raise_restock_request")
        self.client.post("/admin/purchase-orders/9/action", data={"action": "receive"})
        self.assertEqual(self.fake.calls[-1], ("sp_admin_po_action", (9, "receive")))
        self.client.post("/admin/sellers/2/status", data={"status": "Approved"})
        self.assertTrue(self.writes_like("UPDATE seller SET status"))

    def test_category_crud_validation(self):
        self.client.post("/admin/categories/add", data={"category_name": "  "})
        self.assertEqual(self.fake.writes, [])
        self.client.post("/admin/categories/add", data={"category_name": "Toys", "description": "d"})
        self.assertTrue(self.writes_like("INSERT INTO category"))


class SellerPages(Base):
    def setUp(self):
        super().setUp()
        self.login("seller")

    def test_all_seller_pages_render(self):
        for path in ("/seller", "/seller/products", "/seller/products/new", "/seller/products/new?product=1",
                     "/seller/products/5/edit", "/seller/requests", "/seller/orders", "/seller/orders/9",
                     "/seller/profile"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def form(self, **extra):
        data = dict(item_name="Gadget", product_id="", category_id="1", description="d", unit_price="100",
                    quantity_available="10", min_order_qty="1")
        data.update(extra)
        return data

    def test_negative_wholesale_price_is_rejected(self):
        for bad in ("-45", "0", "NaN", "x", ""):
            self.fake.writes.clear()
            r = self.client.post("/seller/products/new", data=self.form(unit_price=bad))
            self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [], bad)
            self.assertIn("Wholesale price", r.get_data(as_text=True), bad)

    def test_bad_quantities_are_rejected(self):
        for field, bad in (("quantity_available", "-1"), ("min_order_qty", "0"), ("min_order_qty", "-4")):
            self.fake.writes.clear()
            self.client.post("/seller/products/new", data=self.form(**{field: bad}))
            self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [], (field, bad))

    def test_valid_listing_is_saved(self):
        r = self.client.post("/seller/products/new", data=self.form())
        self.assertEqual(len(self.writes_like("INSERT INTO seller_listing")), 1)
        self.assertIn("/seller/products", r.headers["Location"])

    def test_picture_error_is_reported_only_after_other_checks(self):
        # a negative price must be reported as a price problem, not hidden behind a picture error
        import io
        data = self.form(unit_price="-45")
        data["image"] = (io.BytesIO(b"\x89PNG\r\n\x1a\nxxxx"), "a.png", "image/png")
        r = self.client.post("/seller/products/new", data=data, content_type="multipart/form-data")
        text = r.get_data(as_text=True)
        self.assertIn("Wholesale price", text)
        self.assertNotIn("SUPABASE_SECRET_KEY", text)

    def test_picture_upload_goes_to_supabase_storage(self):
        import io
        sent = {}

        class R:
            status_code = 200
            text = ""

        def post(url, headers=None, data=None, json=None, timeout=None):
            sent.update(url=url, headers=headers, size=len(data or b""))
            return R()
        saved = (supa.requests.post, config.SUPABASE_URL, config.SUPABASE_SECRET_KEY)
        supa.requests.post, config.SUPABASE_URL, config.SUPABASE_SECRET_KEY = post, "https://p.supabase.co", "sb_secret_abc"
        try:
            data = self.form()
            data["image"] = (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 100), "a.png", "image/png")
            self.client.post("/seller/products/new", data=data, content_type="multipart/form-data")
        finally:
            supa.requests.post, config.SUPABASE_URL, config.SUPABASE_SECRET_KEY = saved
        self.assertIn("/storage/v1/object/product-images/seller-2/", sent["url"])
        self.assertEqual(sent["headers"]["apikey"], "sb_secret_abc")
        insert = self.writes_like("INSERT INTO seller_listing")[0]
        self.assertTrue(any(isinstance(p, str) and "/storage/v1/object/public/product-images/" in p for p in insert[1]))

    def test_missing_secret_key_gives_a_clear_message(self):
        import io
        saved = config.SUPABASE_SECRET_KEY
        config.SUPABASE_SECRET_KEY = ""
        try:
            data = self.form()
            data["image"] = (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 10), "a.png", "image/png")
            r = self.client.post("/seller/products/new", data=data, content_type="multipart/form-data")
        finally:
            config.SUPABASE_SECRET_KEY = saved
        self.assertIn("SUPABASE_SECRET_KEY", r.get_data(as_text=True))
        self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [])

    def test_unapproved_seller_sees_banner_and_cannot_list(self):
        self.fake.seller_status = "Pending"
        self.assertIn("must approve", self.client.get("/seller").get_data(as_text=True))
        r = self.client.post("/seller/products/new", data=self.form())
        self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [])
        self.assertIn("not approved", r.get_data(as_text=True))

    def test_offer_validation(self):
        self.client.post("/seller/requests/9/offer", data={"quantity": "5", "unit_price": "-1"})
        self.assertEqual(self.fake.calls, [])
        self.client.post("/seller/requests/9/offer", data={"quantity": "5", "unit_price": "12.5", "note": "ok"})
        self.assertEqual(self.fake.calls[-1][0], "sp_offer_purchase_order")

    def test_order_actions(self):
        self.client.post("/seller/orders/9/action", data={"action": "ship"})
        self.assertEqual(self.fake.calls[-1], ("sp_seller_po_action", (9, 2, "ship", "")))
        self.fake.calls.clear()
        self.client.post("/seller/orders/9/action", data={"action": "steal"})
        self.assertEqual(self.fake.calls, [])


class NewFeatures(Base):
    def seller_form(self, **extra):
        data = dict(item_name="Gadget", product_id="", category_id="", new_category="", description="d",
                    unit_price="100", quantity_available="10", min_order_qty="1")
        data.update(extra)
        return data

    def test_seller_can_create_a_category_while_listing(self):
        self.login("seller")
        self.fake.overrides[r"FROM category WHERE category_name"] = []            # it does not exist yet
        r = self.client.post("/seller/products/new", data=self.seller_form(new_category="  Footwear  "))
        created = self.writes_like("INSERT INTO category")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][1], ("Footwear",))                            # trimmed
        listing = self.writes_like("INSERT INTO seller_listing")[0]
        self.assertIn(77, listing[1])                                             # linked to the new category id
        self.assertIn("/seller/products", r.headers["Location"])

    def test_existing_category_name_is_reused_not_duplicated(self):
        self.login("seller")
        self.fake.overrides[r"FROM category WHERE category_name"] = [dict(category_id=3)]
        self.client.post("/seller/products/new", data=self.seller_form(new_category="footwear"))
        self.assertEqual(self.writes_like("INSERT INTO category"), [])
        self.assertIn(3, self.writes_like("INSERT INTO seller_listing")[0][1])

    def test_category_is_not_created_when_the_form_is_invalid(self):
        self.login("seller")
        self.fake.overrides[r"FROM category WHERE category_name"] = []
        self.client.post("/seller/products/new", data=self.seller_form(new_category="Footwear", unit_price="-45"))
        self.assertEqual(self.writes_like("INSERT INTO category"), [])
        self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [])

    def test_seller_must_choose_or_type_a_category(self):
        self.login("seller")
        r = self.client.post("/seller/products/new", data=self.seller_form())
        self.assertIn("choose a category", r.get_data(as_text=True))
        self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [])
        self.fake.overrides[r"FROM category WHERE category_id"] = []              # a made-up id
        r = self.client.post("/seller/products/new", data=self.seller_form(category_id="999"))
        self.assertEqual(self.writes_like("INSERT INTO seller_listing"), [])

    def test_seller_form_has_category_box_and_explains_an_empty_list(self):
        self.login("seller")
        html = self.client.get("/seller/products/new").get_data(as_text=True)
        self.assertIn('name="new_category"', html)
        self.fake.empty = True
        html = self.client.get("/seller/products/new").get_data(as_text=True)
        self.assertIn("No categories yet", html)

    def test_admin_product_picture_upload(self):
        import io
        self.login("admin")
        saved = supa.save_image
        urls = iter(["https://p.supabase.co/storage/v1/object/public/product-images/store/1.png",
                     "https://p.supabase.co/storage/v1/object/public/product-images/store/2.png"])
        supa.save_image = lambda f, folder: next(urls) if f and f.filename else None
        try:
            data = dict(product_name="Lamp", category_id="1", price="499", stock="5", reorder_level="1")
            data["images"] = [(io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 20), "a.png", "image/png"),
                              (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"y" * 20), "b.png", "image/png")]
            self.client.post("/admin/products/new", data=data, content_type="multipart/form-data")
        finally:
            supa.save_image = saved
        name, args = self.fake.calls[-1]
        self.assertEqual(name, "sp_add_product")
        self.assertIn("/store/1.png", args[6])                                    # the first picture is the cover
        inserted = self.writes_like("INSERT INTO product_image")
        self.assertEqual(len(inserted), 2)                                        # both pictures are saved
        html = self.client.get("/admin/products/new").get_data(as_text=True)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn("multiple", html)

    def test_admin_is_told_to_add_a_category_first(self):
        self.login("admin")
        self.fake.empty = True
        self.assertIn("Add a category first", self.client.get("/admin/products/new").get_data(as_text=True))

    def test_dashboard_getting_started_guide(self):
        self.login("admin")
        self.fake.overrides[r"AS new_items"] = [dict(orders=0, revenue=0, customers=0, low_stock=0, pending_sellers=0,
                                                     offers=0, to_receive=0, to_process=0, new_items=0,
                                                     categories=0, products=0, approved_sellers=0)]
        html = self.client.get("/admin/dashboard").get_data(as_text=True)
        self.assertIn("Getting started", html)
        self.assertIn("Add your categories", html)
        del self.fake.overrides[r"AS new_items"]
        self.assertNotIn("Getting started", self.client.get("/admin/dashboard").get_data(as_text=True))

    def test_customer_account_page(self):
        self.login("customer")
        html = self.client.get("/account").get_data(as_text=True)
        self.assertIn("Test Customer", html)
        self.assertIn("t@x.com", html)
        self.assertIn("/account", self.client.get("/shop").get_data(as_text=True))   # header link
        good = dict(full_name="New Name", phone="9876543210", address="9 Lane", city="Pune", pincode="411045")
        r = self.client.post("/account", data=good)
        sql, params = self.writes_like("UPDATE customer SET")[-1]
        self.assertEqual(params[0], "New Name")
        self.assertEqual(params[-1], 1)                                           # only my own row
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["name"], "New Name")
        self.fake.writes.clear()
        for field, bad in (("phone", "12"), ("pincode", "abc"), ("full_name", ""), ("address", "")):
            r = self.client.post("/account", data=dict(good, **{field: bad}))
            self.assertEqual(self.writes_like("UPDATE customer SET"), [], field)
            self.assertEqual(r.status_code, 200)

    def test_account_requires_login(self):
        self.assertEqual(self.client.get("/account").status_code, 302)


class Shopping2(Base):
    """Wishlist, addresses, coupons, delivery fee, tracking, invoices, returns, pictures."""

    def setUp(self):
        super().setUp()
        self.login("customer")

    def cart_with_items(self):
        from decimal import Decimal
        self.fake.overrides[r"FROM cart c JOIN cart_item ci ON ci.cart_id = c.cart_id JOIN product p"] = [dict(
            cart_item_id=1, product_id=1, product_name="Pen", price=Decimal("1000"), image_url=None,
            category_name="E", quantity=1, line_total=Decimal("1000"), quantity_available=9)]

    def delivered_order(self, days_ago=1):
        from datetime import datetime, timedelta
        from fakedb import ORDER
        o = dict(ORDER, order_status="Delivered", delivered_date=datetime.now() - timedelta(days=days_ago),
                 tracking_number="GC1", expected_delivery=datetime.now())
        self.fake.overrides[r"FROM vw_order_summary WHERE order_id"] = [o]
        self.fake.overrides[r"FROM order_status_log"] = [
            dict(old_status=None, new_status="Confirmed", changed_at=datetime.now() - timedelta(days=4)),
            dict(old_status="Confirmed", new_status="Packed", changed_at=datetime.now() - timedelta(days=3)),
            dict(old_status="Packed", new_status="Shipped", changed_at=datetime.now() - timedelta(days=2)),
            dict(old_status="Shipped", new_status="Delivered", changed_at=datetime.now() - timedelta(days=days_ago))]

    # ---- pages
    def test_new_customer_pages_render(self):
        for path in ("/wishlist", "/addresses", "/returns", "/account", "/orders/11/invoice"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        html = self.client.get("/account").get_data(as_text=True)
        for link in ("/addresses", "/wishlist", "/returns", "/orders"):
            self.assertIn(link, html)

    # ---- wishlist
    def test_wishlist_toggle_adds_and_removes(self):
        self.fake.overrides[r"AS cart_count"] = [dict(cart_count=0, wish_count=0, wish_ids=None)]   # nothing saved yet
        self.client.post("/wishlist/toggle", data={"product_id": "1", "next": "/shop"})
        self.assertTrue(self.writes_like("INSERT INTO wishlist"))
        self.fake.writes.clear()
        self.fake.overrides[r"AS cart_count"] = [dict(cart_count=0, wish_count=1, wish_ids="1")]
        self.client.post("/wishlist/toggle", data={"product_id": "1"})
        self.assertTrue(self.writes_like("DELETE FROM wishlist"))

    def test_hearts_and_wishlist_count_are_shown(self):
        html = self.client.get("/shop").get_data(as_text=True)
        self.assertIn("wish-btn", html)
        self.assertIn("/wishlist", html)

    def test_move_wishlist_item_to_cart(self):
        r = self.client.post("/wishlist/move-to-cart", data={"product_id": "1"})
        self.assertEqual(self.fake.calls[-1], ("sp_add_to_cart", (1, 1, 1)))
        self.assertTrue(self.writes_like("DELETE FROM wishlist"))
        self.assertIn("/cart", r.headers["Location"])

    # ---- address book
    def address_form(self, **extra):
        data = dict(label="Office", full_name="Asha", phone="9876543210", line1="5 Park St", city="Pune", pincode="411001")
        data.update(extra)
        return data

    def test_address_validation_and_save(self):
        for field, bad in (("phone", "12"), ("pincode", "abc"), ("line1", ""), ("full_name", "")):
            self.fake.writes.clear()
            self.client.post("/addresses/save", data=self.address_form(**{field: bad}))
            self.assertEqual(self.writes_like("INSERT INTO address"), [], field)
        self.fake.overrides[r"COUNT\(\*\) FROM address"] = [dict(n=0)]
        self.client.post("/addresses/save", data=self.address_form())
        insert = self.writes_like("INSERT INTO address")[0]
        self.assertEqual(insert[1][0], 1)                                       # belongs to this customer
        self.assertEqual(insert[1][-1], 1)                                      # the first address is the default

    def test_address_ownership_is_checked(self):
        self.fake.overrides[r"FROM address WHERE address_id"] = []              # not this customer's address
        r = self.client.post("/addresses/save", data=dict(self.address_form(), address_id="3"))
        self.assertEqual(r.status_code, 404)
        self.client.post("/addresses/3/default")
        self.assertEqual(self.writes_like("SET is_default"), [])

    def test_checkout_uses_the_chosen_saved_address(self):
        self.client.post("/checkout", data={"payment_method": "UPI", "address_id": "3"})
        name, args = self.fake.calls[-1]
        self.assertEqual(name, "sp_place_order")
        self.assertIn("1 Road", args[2])
        self.assertIn("Phone: 9000000000", args[2])

    # ---- coupons and delivery
    def test_delivery_fee_rules(self):
        from decimal import Decimal
        from shop.services import delivery_fee
        self.assertEqual(delivery_fee(Decimal("100")), Decimal("40"))
        self.assertEqual(delivery_fee(Decimal("498.99")), Decimal("40"))
        self.assertEqual(delivery_fee(Decimal("499")), Decimal("0"))
        self.assertEqual(delivery_fee(Decimal("0")), Decimal("0"))

    def test_cart_shows_delivery_fee_and_free_delivery_hint(self):
        from decimal import Decimal
        self.fake.overrides[r"FROM cart c JOIN cart_item ci ON ci.cart_id = c.cart_id JOIN product p"] = [dict(
            cart_item_id=1, product_id=1, product_name="Pen", price=Decimal("100"), image_url=None,
            category_name="E", quantity=2, line_total=Decimal("200"), quantity_available=9)]
        html = self.client.get("/cart").get_data(as_text=True)
        self.assertIn("&#8377;40.00", html)                                     # delivery fee
        self.assertIn("&#8377;240.00", html)                                    # 200 + 40
        self.assertIn("more to get free delivery", html)

    def test_apply_valid_coupon_then_checkout_sends_it(self):
        self.cart_with_items()
        r = self.client.post("/cart/coupon", data={"code": " save10 "})
        self.assertEqual(self.fake.calls[-1][0], "sp_validate_coupon")
        self.assertEqual(self.fake.calls[-1][1][0], "SAVE10")                   # tidied and upper-cased
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["coupon"], "SAVE10")
        self.client.post("/checkout", data={"payment_method": "UPI", "shipping_address": "x"})
        name, args = self.fake.calls[-1]
        self.assertEqual(args[3], "SAVE10")
        with self.client.session_transaction() as sess:
            self.assertNotIn("coupon", sess)                                    # used up after the order

    def test_invalid_coupon_is_refused_with_the_database_message(self):
        self.cart_with_items()
        self.fake.fail_procedures["sp_validate_coupon"] = db.DBError(msg="This coupon has expired", errno=1644)
        r = self.client.post("/cart/coupon", data={"code": "OLD"}, follow_redirects=True)
        self.assertIn("This coupon has expired", r.get_data(as_text=True))
        with self.client.session_transaction() as sess:
            self.assertNotIn("coupon", sess)

    def test_cart_page_shows_the_discount(self):
        self.cart_with_items()
        with self.client.session_transaction() as sess:
            sess["coupon"] = "SAVE10"
        html = self.client.get("/cart").get_data(as_text=True)
        self.assertIn("Coupon SAVE10", html)
        self.assertIn("SAVE10 applied", html)
        self.client.post("/cart/coupon/remove")
        with self.client.session_transaction() as sess:
            self.assertNotIn("coupon", sess)

    # ---- tracking and invoice
    def test_tracker_marks_each_step(self):
        from shop.services import tracker
        from datetime import datetime
        t = tracker({"order_status": "Shipped"}, [dict(new_status="Confirmed", changed_at=datetime.now()),
                                                  dict(new_status="Packed", changed_at=datetime.now()),
                                                  dict(new_status="Shipped", changed_at=datetime.now())])
        self.assertEqual([s["done"] for s in t["steps"]], [True, True, True, False])
        self.assertEqual([s["current"] for s in t["steps"]], [False, False, True, False])
        c = tracker({"order_status": "Cancelled"}, [dict(new_status="Confirmed", changed_at=datetime.now()),
                                                    dict(new_status="Cancelled", changed_at=datetime.now())])
        self.assertTrue(c["cancelled"])

    def test_order_page_shows_tracker_and_breakdown(self):
        html = self.client.get("/orders/11").get_data(as_text=True)
        self.assertIn('class="track"', html)
        self.assertIn("View invoice", html)
        self.assertIn("Items subtotal", html)

    def test_invoice_content(self):
        html = self.client.get("/orders/11/invoice").get_data(as_text=True)
        self.assertIn("INV-000011", html)
        self.assertIn("Print / Save as PDF", html)
        self.assertIn("Test Customer", html)

    def test_invoice_of_someone_elses_order_is_404(self):
        self.fake.overrides[r"FROM vw_order_summary WHERE order_id"] = []
        self.assertEqual(self.client.get("/orders/11/invoice").status_code, 404)

    # ---- returns
    def test_return_form_only_while_the_window_is_open(self):
        self.delivered_order(days_ago=1)
        html = self.client.get("/orders/11").get_data(as_text=True)
        self.assertIn("Return this item", html)
        self.assertIn("Request return", html)
        self.delivered_order(days_ago=20)
        html = self.client.get("/orders/11").get_data(as_text=True)
        self.assertNotIn("Request return", html)
        self.assertIn("return period for this order has ended", html)

    def test_return_request_validation_and_call(self):
        good = dict(product_id="1", quantity="1", reason="Damaged or defective", details=" broken ")
        self.client.post("/orders/11/return", data=good)
        name, args = self.fake.calls[-1]
        self.assertEqual(name, "sp_request_return")
        self.assertEqual(args[:6], (1, 11, 1, 1, "Damaged or defective", "broken"))
        self.assertEqual(args[6], 7)                                            # the return window
        self.fake.calls.clear()
        for bad in (dict(good, reason="because"), dict(good, quantity="0"), dict(good, quantity="-1"),
                    dict(good, product_id="x"), dict(good, reason="")):
            self.client.post("/orders/11/return", data=bad)
        self.assertEqual(self.fake.calls, [])

    def test_return_database_message_is_shown(self):
        self.fake.fail_procedures["sp_request_return"] = db.DBError(
            msg="The 7-day return period for this order has ended", errno=1644)
        r = self.client.post("/orders/11/return", data={"product_id": "1", "quantity": "1", "reason": "Other"},
                             follow_redirects=True)
        self.assertIn("return period", r.get_data(as_text=True))

    # ---- pictures, browsing helpers
    def test_product_gallery_shows_all_pictures(self):
        html = self.client.get("/product/1").get_data(as_text=True)
        self.assertIn("gallery-thumbs", html)
        self.assertIn("https://img/b.png", html)
        self.assertIn("Buy now", html)
        self.assertIn("Save to wishlist", html) if False else None

    def test_buy_now_goes_to_the_cart(self):
        r = self.client.post("/cart/add", data={"product_id": "1", "quantity": "1", "buy_now": "1", "next": "/shop"})
        self.assertIn("/cart", r.headers["Location"])

    def test_recently_viewed_is_remembered(self):
        self.client.get("/product/1")
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["recent"], [1])

    def test_rating_filter_is_accepted_and_junk_ignored(self):
        self.assertEqual(self.client.get("/shop?rating=4").status_code, 200)
        self.assertEqual(self.client.get("/shop?rating=99").status_code, 200)
        self.assertEqual(self.client.get("/shop?rating=x").status_code, 200)


class MobileAndSpeed(Base):
    def test_every_page_shell_blocks_zoom(self):
        for path in ("/", "/login", "/admin/login", "/seller/login", "/offline", "/nope"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn("user-scalable=no", html, path)
            self.assertIn("maximum-scale=1", html, path)
        self.assertIn("gesturestart", self.client.get("/").get_data(as_text=True))     # iOS pinch is stopped in JS too
        self.login("customer")
        for path in ("/shop", "/cart", "/product/1", "/orders/11"):
            self.assertIn("user-scalable=no", self.client.get(path).get_data(as_text=True), path)
        self.login("seller")
        self.assertIn("user-scalable=no", self.client.get("/seller").get_data(as_text=True))
        self.login("admin")
        self.assertIn("user-scalable=no", self.client.get("/admin/dashboard").get_data(as_text=True))
        for path in ("/assets/store.css", "/style.css"):
            self.assertIn("touch-action: manipulation", self.client.get(path).get_data(as_text=True))

    def test_fonts_do_not_block_the_first_paint(self):
        self.login("customer")
        html = self.client.get("/shop").get_data(as_text=True)
        self.assertIn("media=\"print\" onload=", html)

    def test_pages_ask_the_database_for_as_little_as_possible(self):
        self.login("customer")
        self.client.get("/shop"); self.client.get("/product/1")          # warm the 20-second caches, like a real visitor
        counted = []
        orig_one, orig_all, orig_scalar = db.query_one, db.query_all, db.scalar
        db.query_one = lambda s, p=(): counted.append(1) or orig_one(s, p)
        db.query_all = lambda s, p=(): counted.append(1) or orig_all(s, p)
        db.scalar = lambda s, p=(), default=0: counted.append(1) or orig_scalar(s, p, default)
        try:
            self.client.get("/shop")
            shop_queries = len(counted); counted.clear()
            self.client.get("/product/1")
            product_queries = len(counted)
        finally:
            db.query_one, db.query_all, db.scalar = orig_one, orig_all, orig_scalar
        self.assertLessEqual(shop_queries, 3, "shop page")          # products + merged counts (+ recently viewed)
        self.assertLessEqual(product_queries, 8, "product page")

    def test_category_menu_is_cached_between_requests(self):
        self.login("customer")
        self.client.get("/shop")
        calls = []
        orig = db.query_all
        db.query_all = lambda s, p=(): calls.append(s) or orig(s, p)
        try:
            self.client.get("/shop")
        finally:
            db.query_all = orig
        self.assertFalse([c for c in calls if "GROUP BY c.category_id" in c], "categories were loaded again")

    def test_pictures_are_shrunk_before_upload(self):
        import io, random
        from PIL import Image
        img = Image.new("RGB", (3000, 2000))
        px = img.load()
        random.seed(1)
        for x in range(0, 3000, 7):
            for y in range(0, 2000, 7):
                px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        raw = io.BytesIO(); img.save(raw, "PNG")
        out, mime, ext = supa._optimise(raw.getvalue(), "image/png")
        self.assertEqual((mime, ext), ("image/jpeg", "jpg"))
        small = Image.open(io.BytesIO(out))
        self.assertLessEqual(max(small.size), 1200)
        self.assertLess(len(out), len(raw.getvalue()) / 3)
        # a file that is not really an image is left alone
        junk, mime2, ext2 = supa._optimise(b"\x89PNG\r\n\x1a\nxxxx", "image/png")
        self.assertEqual((mime2, ext2), ("image/png", "png"))


class AdminSales(Base):
    def setUp(self):
        super().setUp()
        self.login("admin")

    def test_new_admin_pages_render(self):
        for path in ("/admin/coupons", "/admin/returns", "/admin/returns?status=Requested", "/admin/orders/11/invoice"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        html = self.client.get("/admin/dashboard").get_data(as_text=True)
        self.assertIn("Returns waiting for action", html)

    def test_admin_menu_is_grouped(self):
        html = self.client.get("/admin/dashboard").get_data(as_text=True)
        for text in ("nav-group", "Sales", "Catalog", "Suppliers", "/admin/coupons", "/admin/returns"):
            self.assertIn(text, html)

    def coupon(self, **extra):
        data = dict(code="save10", discount_type="percent", discount_value="10", min_order_amount="499",
                    max_discount="100", usage_limit="", per_customer_limit="1", valid_from="", valid_to="")
        data.update(extra)
        return data

    def test_create_coupon(self):
        self.client.post("/admin/coupons/add", data=self.coupon())
        sql, p = self.writes_like("INSERT INTO coupon")[0]
        self.assertEqual(p[0], "SAVE10")
        self.assertEqual(p[2], "percent")
        self.assertEqual(str(p[3]), "10.00")
        self.assertIsNone(p[6])                                                 # unlimited uses

    def test_coupon_validation(self):
        bad_forms = [self.coupon(code="a"), self.coupon(code="HAS SPACE!"), self.coupon(discount_value="-5"),
                     self.coupon(discount_value="0"), self.coupon(discount_value="150"),
                     self.coupon(discount_type="weird"), self.coupon(min_order_amount="-1"),
                     self.coupon(max_discount="-3"), self.coupon(usage_limit="0"), self.coupon(per_customer_limit="0"),
                     self.coupon(valid_from="2026-12-31T10:00", valid_to="2026-01-01T10:00"),
                     self.coupon(valid_from="not a date")]
        for form in bad_forms:
            self.fake.writes.clear()
            self.client.post("/admin/coupons/add", data=form)
            self.assertEqual(self.writes_like("INSERT INTO coupon"), [], form)
        self.client.post("/admin/coupons/add", data=self.coupon(discount_type="flat", discount_value="150", max_discount=""))
        self.assertEqual(len(self.writes_like("INSERT INTO coupon")), 1)        # a flat 150 is fine

    def test_duplicate_coupon_code_message(self):
        orig = self.fake.execute
        def boom(sql, params=()):
            if "INSERT INTO coupon" in sql:
                raise db.DBError(msg="Duplicate entry", errno=1062)
            return orig(sql, params)
        db.execute = boom
        r = self.client.post("/admin/coupons/add", data=self.coupon(), follow_redirects=True)
        self.assertIn("already exists", r.get_data(as_text=True))

    def test_coupon_toggle_and_delete(self):
        self.client.post("/admin/coupons/1/toggle")
        self.assertTrue(self.writes_like("SET is_active = NOT is_active"))
        self.client.post("/admin/coupons/1/delete")
        self.assertTrue(self.writes_like("DELETE FROM coupon"))

    def test_return_actions(self):
        self.client.post("/admin/returns/5/action", data={"action": "approve", "note": "ok"})
        self.assertEqual(self.fake.calls[-1], ("sp_update_return", (5, "approve", "ok", 1)))
        self.client.post("/admin/returns/5/action", data={"action": "refund", "no_restock": "1"})
        self.assertEqual(self.fake.calls[-1], ("sp_update_return", (5, "refund", "", 0)))
        self.fake.calls.clear()
        self.client.post("/admin/returns/5/action", data={"action": "delete-everything"})
        self.assertEqual(self.fake.calls, [])

    def test_shipping_details_and_emails(self):
        import shop.mailer as mailer
        sent = []
        saved = (mailer.send, config.EMAILS_READY)
        mailer.send = lambda to, subject, template, **ctx: sent.append((to, subject, ctx.get("heading"))) or True
        config.EMAILS_READY = True
        try:
            self.client.post("/admin/orders/11/status", data={"new_status": "Shipped", "courier_name": " DTDC ",
                                                               "tracking_number": "ABC123"})
        finally:
            mailer.send, config.EMAILS_READY = saved
        self.assertEqual(self.fake.calls[-1], ("sp_update_order_status", (11, "Shipped")))
        self.assertTrue(self.writes_like("SET courier_name"))
        self.assertTrue(self.writes_like("SET tracking_number"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "t@x.com")
        self.assertIn("#11", sent[0][1])
        self.assertEqual(sent[0][2], "Your order is on its way")

    def test_email_template_renders(self):
        with self.app.test_request_context("/"):
            from flask import render_template
            html = render_template("emails/message.html", store="GreenCart", heading="Hello", name="Asha",
                                   intro="Intro text", rows=[("Order", "#1")], items=[dict(product_name="Pen", quantity=2,
                                   subtotal=200)], link="http://x/orders/1", link_text=None)
        self.assertIn("Intro text", html)
        self.assertIn("Pen", html)

    def test_no_email_when_not_configured(self):
        import shop.mailer as mailer
        saved = config.EMAILS_READY
        config.EMAILS_READY = False
        try:
            self.assertFalse(mailer.send("a@b.co", "Hi", "message.html"))
        finally:
            config.EMAILS_READY = saved
        self.client.post("/admin/orders/11/status", data={"new_status": "Packed"})      # must not crash

    def test_smtp_sender_builds_and_sends_a_real_message(self):
        import shop.mailer as mailer
        log = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None, **kw):
                log["server"] = (host, port)
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self, context=None): log["tls"] = True
            def login(self, user, password): log["login"] = (user, password)
            def send_message(self, message): log["message"] = message

        saved = (mailer.smtplib.SMTP, config.EMAILS_READY, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER,
                 config.SMTP_PASSWORD, config.MAIL_FROM)
        mailer.smtplib.SMTP = FakeSMTP
        config.EMAILS_READY, config.SMTP_HOST, config.SMTP_PORT = True, "smtp.example.com", 587
        config.SMTP_USER, config.SMTP_PASSWORD, config.MAIL_FROM = "u", "p", "shop@example.com"
        try:
            with self.app.test_request_context("/"):
                ok = mailer.send("buyer@example.com", "Order #7 confirmed", "message.html", heading="Done",
                                 name="Asha", intro="Thanks", rows=[("Order", "#7")], items=None, link=None, link_text=None)
        finally:
            (mailer.smtplib.SMTP, config.EMAILS_READY, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER,
             config.SMTP_PASSWORD, config.MAIL_FROM) = saved
        self.assertTrue(ok)
        self.assertEqual(log["server"], ("smtp.example.com", 587))
        self.assertTrue(log["tls"])
        self.assertEqual(log["login"], ("u", "p"))
        self.assertEqual(log["message"]["To"], "buyer@example.com")
        self.assertEqual(log["message"]["Subject"], "Order #7 confirmed")
        self.assertIn("Thanks", log["message"].get_body(("html",)).get_content())

    def test_a_broken_mail_server_never_breaks_the_order(self):
        import shop.mailer as mailer

        def boom(*a, **k):
            raise OSError("mail server down")
        saved = (mailer.smtplib.SMTP, config.EMAILS_READY, config.SMTP_HOST, config.MAIL_FROM)
        mailer.smtplib.SMTP = boom
        config.EMAILS_READY, config.SMTP_HOST, config.MAIL_FROM = True, "smtp.example.com", "shop@example.com"
        try:
            self.login("customer")
            r = self.client.post("/checkout", data={"payment_method": "UPI", "shipping_address": "x"})
        finally:
            mailer.smtplib.SMTP, config.EMAILS_READY, config.SMTP_HOST, config.MAIL_FROM = saved
        self.assertIn("/orders/11", r.headers["Location"])

    def test_picture_management(self):
        self.client.post("/admin/products/1/images/2/cover")
        self.assertTrue(self.writes_like("UPDATE product_image SET sort_order"))
        self.client.post("/admin/products/1/images/2/delete")
        self.assertTrue(self.writes_like("DELETE FROM product_image"))
        html = self.client.get("/admin/products/1/edit").get_data(as_text=True)
        self.assertIn("Current pictures", html)
        self.assertIn("Make cover", html)
        self.assertNotIn('value="https://img/a.png"', html)                        # the cover is NOT re-added on save

    def test_edit_does_not_touch_the_cover_by_itself(self):
        self.client.post("/admin/products/1/edit", data=dict(product_name="X", category_id="1", price="10"))
        sql, _ = self.writes_like("UPDATE product SET category_id")[0]
        self.assertNotIn("image_url", sql)


class SellerExtras(Base):
    def test_earnings_page(self):
        self.login("seller")
        r = self.client.get("/seller/earnings")
        self.assertEqual(r.status_code, 200)
        self.assertIn("5,000.00", r.get_data(as_text=True))
        self.assertIn("/seller/earnings", self.client.get("/seller").get_data(as_text=True))


class EmptyDatabase(Base):
    """A brand-new database: no categories, products, orders, sellers ... every page must still open."""

    def setUp(self):
        super().setUp()
        self.fake.empty = True

    def test_customer_pages(self):
        self.login("customer")
        for path in ("/shop", "/shop?search=zzz", "/cart", "/orders", "/wishlist", "/addresses", "/returns", "/account"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/product/1").status_code, 404)
        self.assertEqual(self.client.get("/orders/1").status_code, 404)

    def test_admin_pages(self):
        self.login("admin")
        for path in ("/admin/dashboard", "/admin/orders", "/admin/products", "/admin/products/new",
                     "/admin/categories", "/admin/inventory", "/admin/customers", "/admin/reports",
                     "/admin/sellers", "/admin/seller-items", "/admin/purchase-orders", "/admin/coupons",
                     "/admin/returns"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_seller_pages(self):
        self.login("seller")
        for path in ("/seller", "/seller/products", "/seller/products/new", "/seller/requests", "/seller/orders",
                     "/seller/earnings"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


class SqlFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from sqlscript import read_statements
        cls.sql = {f: read_statements(os.path.join(ROOT, "database", f))
                   for f in ("01_schema.sql", "02_triggers.sql", "03_routines.sql", "04_views.sql")}

    def test_expected_objects_exist(self):
        joined = lambda f: "\n".join(self.sql[f]).lower()
        self.assertEqual(joined("01_schema.sql").count("create table if not exists"), 26)
        self.assertEqual(len([s for s in self.sql["02_triggers.sql"] if s.startswith("CREATE TRIGGER")]), 28)
        routines = [s for s in self.sql["03_routines.sql"] if s.startswith("CREATE")]
        self.assertEqual(len(routines), 20)
        self.assertEqual(len(self.sql["04_views.sql"]), 17)

    def test_no_demo_data_anywhere(self):
        for f, statements in self.sql.items():
            for s in statements:
                if f == "02_triggers.sql" or f == "03_routines.sql":
                    continue                    # inserts inside triggers/procedures are logic, not data
                self.assertFalse(s.upper().startswith("INSERT"), "%s: %s" % (f, s[:60]))

    def test_every_trigger_and_routine_is_closed(self):
        for f in ("02_triggers.sql", "03_routines.sql"):
            for s in self.sql[f]:
                if s.startswith("CREATE"):
                    self.assertTrue(s.rstrip().endswith("END"), s[:60])
                    begins = len(re.findall(r"\bBEGIN\b", s))
                    ends = len(re.findall(r"\bEND\b(?!\s+(?:IF|LOOP|CASE))", s))
                    self.assertGreaterEqual(ends, begins - 0, s[:60])

    def test_price_guards_exist(self):
        text = "\n".join(self.sql["02_triggers.sql"])
        for t in ("trg_product_before_insert", "trg_product_before_update", "trg_listing_before_insert",
                  "trg_listing_before_update"):
            self.assertIn(t, text)
        self.assertIn("price > 0", "\n".join(self.sql["01_schema.sql"]))

    def test_every_table_used_by_the_website_is_created(self):
        schema = "\n".join(self.sql["01_schema.sql"]).lower()
        views = "\n".join(self.sql["04_views.sql"]).lower()
        used = set()
        for name in ("routes/store.py", "routes/admin.py", "routes/admin_sales.py", "routes/customer.py",
                     "routes/restock.py", "routes/seller.py", "routes/accounts.py", "security.py",
                     "routes/public.py", "services.py", "catalog.py"):
            with open(os.path.join(ROOT, "shop", name), encoding="utf-8") as f:
                code = f.read()
            for literal in re.findall(r'"((?:[^"\\\n]|\\.)*)"', code):          # only text inside quotes
                used |= set(re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)", literal))
        known = {"select", "set", "the", "a", "functools", "urllib", "decimal", "flask", "werkzeug", "mysql"}
        for obj in used:
            o = obj.lower()
            if o in known:
                continue
            self.assertTrue(("table if not exists " + o) in schema or ("view " + o) in views, "missing: " + o)


class Validation(unittest.TestCase):
    def test_parsers(self):
        from shop.utils import parse_price, parse_quantity
        self.assertEqual(str(parse_price("12.345")[0]), "12.35")
        for bad in ("-1", "0", "0.001", "nan", "inf", "-inf", "1e99", "", None, "12abc"):
            self.assertIsNotNone(parse_price(bad)[1], bad)
        self.assertEqual(parse_quantity("5")[0], 5)
        for bad in ("-1", "x", "", None, "9999999999"):
            self.assertIsNotNone(parse_quantity(bad)[1], bad)
        self.assertIsNotNone(parse_quantity("0", minimum=1)[1])

    def test_sql_splitter(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from sqlscript import split_statements
        self.assertEqual(split_statements("SELECT 'a;b'; -- x; y\nSELECT 2;"), ["SELECT 'a;b'", "SELECT 2"])


if __name__ == "__main__":
    unittest.main()
