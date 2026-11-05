"""
selftest_db.py - checks that every trigger, procedure and view really works.

    python scripts/setup_db.py --admin        (build the database first)
    python scripts/selftest_db.py             (then run this ONCE, before adding real data)

It walks through a complete life of the shop: product, customer, cart,
order, payment, shipment, delivery, review, cancellation, price history,
seller approval, seller listing, purchase order, stock increase ...
Every rule that must FAIL is checked to fail with the right message.

SAFETY: it refuses to run if the database already holds customers,
products, categories, sellers or orders, because it wipes all of those
when it finishes (the admin login is kept).
"""
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shop import db  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print("  [%s] %s%s" % ("ok  " if condition else "FAIL", name, ("   -> " + str(detail)) if detail and not condition else ""))


def expect_error(name, action, text):
    """The action must be refused by the database with a message containing `text`."""
    try:
        action()
    except db.DBError as e:
        message = getattr(e, "msg", str(e))
        check(name, text.lower() in message.lower(), "got: %s" % message)
        return
    check(name, False, "no error was raised")


def one(sql, params=()):
    return db.query_one(sql, params)


def val(sql, params=()):
    return db.scalar(sql, params, default=None)


def stock(pid):
    return val("SELECT quantity_available FROM inventory WHERE product_id = %s", (pid,))


def run():
    # ------------------------------------------------------------ safety
    busy = {t: val("SELECT COUNT(*) FROM " + t) for t in ("customer", "seller", "product", "category", "orders")}
    if any(busy.values()):
        sys.exit("Refusing to run: the database already contains data %s.\n"
                 "This test wipes all shop data at the end. Use a fresh database "
                 "(python scripts/setup_db.py --reset --yes)." % busy)

    print("\n1. Products and prices")
    cat = db.execute("INSERT INTO category (category_name, description) VALUES ('Selftest', 'temporary')")
    expect_error("negative product price is refused",
                 lambda: db.execute("INSERT INTO product (category_id, product_name, price) VALUES (%s, 'X', -5)", (cat,)),
                 "greater than 0")
    expect_error("zero product price is refused",
                 lambda: db.execute("INSERT INTO product (category_id, product_name, price) VALUES (%s, 'X', 0)", (cat,)),
                 "greater than 0")
    pid = db.call_procedure("sp_add_product", (cat, "Selftest Phone", "demo", Decimal("1999.00"), 10, 3, "", 0))[7]
    check("sp_add_product creates product + stock row", stock(pid) == 10, stock(pid))
    check("reorder level saved", val("SELECT reorder_level FROM inventory WHERE product_id = %s", (pid,)) == 3)
    expect_error("price cannot be updated to a negative number",
                 lambda: db.execute("UPDATE product SET price = -1 WHERE product_id = %s", (pid,)), "greater than 0")

    print("\n2. Customer and cart")
    cid = db.execute("INSERT INTO customer (full_name, email, phone, address, city, pincode) "
                     "VALUES ('Test Customer', '  TEST.Customer@Example.COM ', '9000000000', '1 Road', 'Pune', '411001')")
    check("email is cleaned by a trigger", val("SELECT email FROM customer WHERE customer_id = %s", (cid,)) == "test.customer@example.com")
    check("cart is created by a trigger", val("SELECT COUNT(*) FROM cart WHERE customer_id = %s", (cid,)) == 1)
    db.call_procedure("sp_add_to_cart", (cid, pid, 2))
    check("fn_cart_total = quantity x price", val("SELECT fn_cart_total(%s)", (cid,)) == Decimal("3998.00"),
          val("SELECT fn_cart_total(%s)", (cid,)))
    db.call_procedure("sp_add_to_cart", (cid, pid, 1))
    check("adding the same product increases the quantity",
          val("SELECT quantity FROM cart_item ci JOIN cart c ON c.cart_id = ci.cart_id WHERE c.customer_id = %s", (cid,)) == 3)
    expect_error("cart cannot exceed the stock (add)", lambda: db.call_procedure("sp_add_to_cart", (cid, pid, 50)),
                 "more than the available stock")
    expect_error("cart cannot exceed the stock (update)",
                 lambda: db.execute("UPDATE cart_item SET quantity = 99 WHERE cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)", (cid,)),
                 "more than the available stock")
    db.execute("UPDATE cart_item SET quantity = 2 WHERE cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)", (cid,))
    check("cart total follows the quantity", val("SELECT fn_cart_total(%s)", (cid,)) == Decimal("3998.00"))

    print("\n3. Order, payment, shipment")
    expect_error("invalid payment method is refused",
                 lambda: db.call_procedure("sp_place_order", (cid, "Barter", "", "", 0, 0, 0)), "valid payment method")
    oid = db.call_procedure("sp_place_order", (cid, "UPI", "", "", 0, 0, 0))[6]
    order = one("SELECT total_amount, order_status, shipping_address FROM orders WHERE order_id = %s", (oid,))
    check("order total is correct", order["total_amount"] == Decimal("3998.00"), order)
    check("shipping address filled from the profile", "1 Road" in order["shipping_address"], order)
    check("stock was reduced by the order", stock(pid) == 8, stock(pid))
    check("real price copied into the order item", val("SELECT unit_price FROM order_item WHERE order_id = %s", (oid,)) == Decimal("1999.00"))
    pay = one("SELECT payment_status, amount FROM payment WHERE order_id = %s", (oid,))
    check("online payment is Paid with the right amount", pay["payment_status"] == "Paid" and pay["amount"] == Decimal("3998.00"), pay)
    check("shipment row created", val("SELECT COUNT(*) FROM shipment WHERE order_id = %s", (oid,)) == 1)
    check("cart emptied after ordering", val("SELECT COUNT(*) FROM cart_item") == 0)
    check("status history started", val("SELECT COUNT(*) FROM order_status_log WHERE order_id = %s", (oid,)) == 1)
    expect_error("empty cart cannot be ordered", lambda: db.call_procedure("sp_place_order", (cid, "UPI", "", "", 0, 0, 0)), "cart is empty")
    expect_error("payment must match the order total",
                 lambda: db.execute("INSERT INTO payment (order_id, payment_method, amount) VALUES (%s, 'UPI', 1)", (oid,)),
                 "does not match")
    expect_error("order cannot hold more than the stock",
                 lambda: db.execute("INSERT INTO order_item (order_id, product_id, quantity, unit_price) VALUES (%s, %s, 500, 0)", (oid, pid)),
                 "Insufficient stock")

    print("\n4. Order life cycle")
    expect_error("review before delivery is refused",
                 lambda: db.execute("INSERT INTO review (product_id, customer_id, rating) VALUES (%s, %s, 5)", (pid, cid)),
                 "only after it is delivered")
    expect_error("cannot jump Confirmed -> Delivered", lambda: db.call_procedure("sp_update_order_status", (oid, "Delivered")),
                 "Invalid order status change")
    db.call_procedure("sp_update_order_status", (oid, "Packed"))
    db.call_procedure("sp_update_order_status", (oid, "Shipped"))
    ship = one("SELECT tracking_number, shipped_date, expected_delivery FROM shipment WHERE order_id = %s", (oid,))
    check("shipping fills tracking number and dates", all(ship.values()), ship)
    expect_error("cannot go back Shipped -> Packed", lambda: db.call_procedure("sp_update_order_status", (oid, "Packed")),
                 "Invalid order status change")
    db.call_procedure("sp_update_order_status", (oid, "Delivered"))
    check("delivery date filled", val("SELECT delivered_date FROM shipment WHERE order_id = %s", (oid,)) is not None)
    check("4 status history lines", val("SELECT COUNT(*) FROM order_status_log WHERE order_id = %s", (oid,)) == 4)
    expect_error("delivered order cannot be cancelled", lambda: db.call_procedure("sp_cancel_order", (oid, cid)),
                 "Only Confirmed or Packed")
    expect_error("delivered order cannot be deleted", lambda: db.execute("DELETE FROM orders WHERE order_id = %s", (oid,)),
                 "cannot be deleted")
    db.execute("INSERT INTO review (product_id, customer_id, rating, comment) VALUES (%s, %s, 5, 'Great')", (pid, cid))
    check("review allowed after delivery", val("SELECT COUNT(*) FROM review WHERE product_id = %s", (pid,)) == 1)
    expect_error("one review per product", lambda: db.execute("INSERT INTO review (product_id, customer_id, rating) VALUES (%s, %s, 4)", (pid, cid)),
                 "duplicate")
    check("fn_avg_rating works", val("SELECT fn_avg_rating(%s)", (pid,)) == Decimal("5.00"))

    print("\n5. Cancellation, refund, low-stock alert")
    db.call_procedure("sp_add_to_cart", (cid, pid, 3))
    oid2 = db.call_procedure("sp_place_order", (cid, "Cash on Delivery", "Somewhere 12", "", 0, 0, 0))[6]
    check("cash on delivery starts Pending", val("SELECT payment_status FROM payment WHERE order_id = %s", (oid2,)) == "Pending")
    check("stock reduced again", stock(pid) == 5, stock(pid))
    db.call_procedure("sp_cancel_order", (oid2, cid))
    check("cancel restores the stock", stock(pid) == 8, stock(pid))
    check("cancelled cash order is not left pending", val("SELECT payment_status FROM payment WHERE order_id = %s", (oid2,)) == "Failed")
    db.call_procedure("sp_add_to_cart", (cid, pid, 5))
    oid3 = db.call_procedure("sp_place_order", (cid, "Credit Card", "", "", 0, 0, 0))[6]
    check("low-stock alert raised (3 left, reorder level 3)", val("SELECT COUNT(*) FROM low_stock_alert WHERE product_id = %s", (pid,)) >= 1)
    db.call_procedure("sp_cancel_order", (oid3, cid))
    check("cancel restores stock again", stock(pid) == 8, stock(pid))
    check("paid order is Refunded", val("SELECT payment_status FROM payment WHERE order_id = %s", (oid3,)) == "Refunded")
    expect_error("another customer's order cannot be cancelled",
                 lambda: db.call_procedure("sp_cancel_order", (oid3, cid + 999)), "not found")

    print("\n6. Price history")
    db.execute("UPDATE product SET price = 2199 WHERE product_id = %s", (pid,))
    check("price change recorded by trigger",
          val("SELECT COUNT(*) FROM price_history WHERE product_id = %s AND old_price = 1999 AND new_price = 2199", (pid,)) == 1)

    print("\n7. Seller, listing, purchase order, stock increase")
    sid = db.execute("INSERT INTO seller (business_name, contact_name, email, phone, address, city, pincode) "
                     "VALUES ('Selftest Traders', 'Sam', ' SELLER@Example.com', '9111111111', '2 Lane', 'Pune', '411002')")
    check("new seller starts Pending", val("SELECT status FROM seller WHERE seller_id = %s", (sid,)) == "Pending")
    listing_sql = ("INSERT INTO seller_listing (seller_id, product_id, item_name, category_id, unit_price, quantity_available, min_order_qty) "
                   "VALUES (%s, NULL, 'Selftest Gadget', %s, %s, 100, 5)")
    expect_error("unapproved seller cannot list", lambda: db.execute(listing_sql, (sid, cat, 500)), "not approved")
    db.execute("UPDATE seller SET status = 'Approved' WHERE seller_id = %s", (sid,))
    expect_error("negative wholesale price is refused", lambda: db.execute(listing_sql, (sid, cat, -45)), "greater than 0")
    lid = db.execute(listing_sql, (sid, cat, 500))
    check("approved seller can list a new item", lid > 0)
    pid2 = db.call_procedure("sp_add_listing_to_store", (lid, Decimal("899.00"), 0))[2]
    check("admin adds the seller item to the store (0 stock)", stock(pid2) == 0)
    check("listing is now linked to the store product", val("SELECT product_id FROM seller_listing WHERE listing_id = %s", (lid,)) == pid2)
    check("item is visible in the customer catalog",
          one("SELECT product_id FROM vw_product_catalog WHERE product_id = %s AND is_active = 1", (pid2,)) is not None)
    expect_error("cannot add the same item twice", lambda: db.call_procedure("sp_add_listing_to_store", (lid, Decimal("899.00"), 0)),
                 "already in the store")

    expect_error("order below the seller minimum is refused", lambda: db.call_procedure("sp_order_from_listing", (lid, 2, "", 0)), "minimum")
    po = db.call_procedure("sp_order_from_listing", (lid, 10, "first order", 0))[3]
    check("direct purchase order starts as Requested", val("SELECT status FROM purchase_order WHERE po_id = %s", (po,)) == "Requested")
    expect_error("only one restock order at a time", lambda: db.call_procedure("sp_order_from_listing", (lid, 10, "", 0)), "already in progress")
    expect_error("another seller cannot act on this order", lambda: db.call_procedure("sp_seller_po_action", (po, sid + 999, "accept", "")), "does not belong")
    db.call_procedure("sp_seller_po_action", (po, sid, "accept", ""))
    check("accepting reduces the seller's stock", val("SELECT quantity_available FROM seller_listing WHERE listing_id = %s", (lid,)) == 90)
    expect_error("cannot receive before it is shipped", lambda: db.call_procedure("sp_admin_po_action", (po, "receive")), "Only shipped")
    db.call_procedure("sp_seller_po_action", (po, sid, "ship", ""))
    db.call_procedure("sp_admin_po_action", (po, "receive"))
    check("RECEIVING the goods increases store stock", stock(pid2) == 10, stock(pid2))
    check("purchase order history has 4 lines", val("SELECT COUNT(*) FROM purchase_order_log WHERE po_id = %s", (po,)) == 4)
    check("seller earnings counted", val("SELECT fn_seller_earnings(%s)", (sid,)) == Decimal("5000.00"))

    print("\n8. Open restock request -> seller offer")
    po2 = db.call_procedure("sp_raise_restock_request", (pid, 20, Decimal("400.00"), "need more", 0))[4]
    expect_error("offer above the maximum price is refused",
                 lambda: db.call_procedure("sp_offer_purchase_order", (po2, sid, 20, Decimal("450.00"), "")), "above the maximum")
    expect_error("zero offer price is refused",
                 lambda: db.call_procedure("sp_offer_purchase_order", (po2, sid, 20, Decimal("0"), "")), "greater than 0")
    db.call_procedure("sp_offer_purchase_order", (po2, sid, 20, Decimal("350.00"), "ok"))
    check("request is now Offered", val("SELECT status FROM purchase_order WHERE po_id = %s", (po2,)) == "Offered")
    db.call_procedure("sp_admin_po_action", (po2, "accept_offer"))
    db.call_procedure("sp_seller_po_action", (po2, sid, "ship", ""))
    db.call_procedure("sp_admin_po_action", (po2, "receive"))
    check("open request fulfilled: stock 8 + 20", stock(pid) == 28, stock(pid))

    print("\n10. Coupons, delivery fee, order extras")
    def shop_item(name, price, stock):
        return db.call_procedure("sp_add_product", (cat, name, "demo", Decimal(price), stock, 3, "", 0))[7]
    pc = shop_item("Selftest Coupon Item", "1000.00", 20)
    ps = shop_item("Selftest Small Item", "100.00", 10)
    expect_error("coupon with a negative value is refused",
                 lambda: db.execute("INSERT INTO coupon (code, discount_type, discount_value) VALUES ('BAD', 'flat', -5)"),
                 "greater than 0")
    expect_error("percent coupon above 100 is refused",
                 lambda: db.execute("INSERT INTO coupon (code, discount_type, discount_value) VALUES ('BAD', 'percent', 150)"),
                 "more than 100")
    db.execute("INSERT INTO coupon (code, discount_type, discount_value, min_order_amount, max_discount, per_customer_limit) "
               "VALUES (' save10 ', 'percent', 10, 500, 100, 1)")
    check("coupon code is stored in capitals", val("SELECT code FROM coupon") == "SAVE10")
    preview = db.call_procedure("sp_validate_coupon", ("save10", cid, Decimal("1000.00"), 0, 0))
    check("10% of 1000 is capped at the 100 maximum", preview[4] == Decimal("100.00"), preview[4])
    expect_error("coupon below the minimum order is refused",
                 lambda: db.call_procedure("sp_validate_coupon", ("SAVE10", cid, Decimal("300.00"), 0, 0)), "at least")
    expect_error("unknown coupon is refused",
                 lambda: db.call_procedure("sp_validate_coupon", ("NOPE", cid, Decimal("900.00"), 0, 0)), "not valid")

    db.call_procedure("sp_add_to_cart", (cid, ps, 1))
    small = db.call_procedure("sp_place_order", (cid, "UPI", "", "", 40, 499, 0))[6]
    row = one("SELECT total_amount FROM orders WHERE order_id = %s", (small,))
    check("small order pays the 40 delivery fee (100 + 40)", row["total_amount"] == Decimal("140.00"), row)
    check("payment matches the total with the fee",
          val("SELECT amount FROM payment WHERE order_id = %s", (small,)) == Decimal("140.00"))
    check("delivery fee saved in order_extra", val("SELECT delivery_fee FROM order_extra WHERE order_id = %s", (small,)) == Decimal("40.00"))

    db.call_procedure("sp_add_to_cart", (cid, pc, 1))
    c1 = db.call_procedure("sp_place_order", (cid, "UPI", "", "save10", 40, 499, 0))[6]
    extra = one("SELECT subtotal, coupon_code, discount_amount, delivery_fee FROM order_extra WHERE order_id = %s", (c1,))
    check("coupon order: subtotal 1000, discount 100, free delivery", extra["subtotal"] == Decimal("1000.00") and
          extra["discount_amount"] == Decimal("100.00") and extra["delivery_fee"] == 0 and extra["coupon_code"] == "SAVE10", extra)
    check("coupon order total is 900", val("SELECT total_amount FROM orders WHERE order_id = %s", (c1,)) == Decimal("900.00"))
    check("coupon use was recorded", val("SELECT COUNT(*) FROM coupon_redemption WHERE order_id = %s", (c1,)) == 1)
    expect_error("the same customer cannot use the coupon twice",
                 lambda: db.call_procedure("sp_validate_coupon", ("SAVE10", cid, Decimal("1000.00"), 0, 0)), "already used")
    db.call_procedure("sp_cancel_order", (c1, cid))
    check("cancelling the order gives the coupon back", val("SELECT COUNT(*) FROM coupon_redemption WHERE order_id = %s", (c1,)) == 0)
    db.call_procedure("sp_validate_coupon", ("SAVE10", cid, Decimal("1000.00"), 0, 0))
    check("coupon works again after the cancellation", True)

    print("\n11. Returns and refunds")
    db.call_procedure("sp_add_to_cart", (cid, pc, 2))
    c2 = db.call_procedure("sp_place_order", (cid, "Credit Card", "", "SAVE10", 40, 499, 0))[6]
    check("second coupon order: 2000 - 100 = 1900", val("SELECT total_amount FROM orders WHERE order_id = %s", (c2,)) == Decimal("1900.00"))
    expect_error("an order that is not delivered cannot be returned",
                 lambda: db.call_procedure("sp_request_return", (cid, c2, pc, 1, "Other", "", 7, 0)), "Only delivered")
    for status in ("Packed", "Shipped", "Delivered"):
        db.call_procedure("sp_update_order_status", (c2, status))
    expect_error("another customer cannot return this order",
                 lambda: db.call_procedure("sp_request_return", (cid + 999, c2, pc, 1, "Other", "", 7, 0)), "not found")
    expect_error("a closed return window is refused",
                 lambda: db.call_procedure("sp_request_return", (cid, c2, pc, 1, "Other", "", -1, 0)), "return period")
    expect_error("cannot return more than was bought",
                 lambda: db.call_procedure("sp_request_return", (cid, c2, pc, 3, "Other", "", 7, 0)), "at most")
    ret = db.call_procedure("sp_request_return", (cid, c2, pc, 1, "Damaged or defective", "cracked", 7, 0))[7]
    refund = val("SELECT refund_amount FROM return_request WHERE return_id = %s", (ret,))
    check("refund is the item price minus its share of the coupon (950)", refund == Decimal("950.00"), refund)
    expect_error("only one more item can still be returned",
                 lambda: db.call_procedure("sp_request_return", (cid, c2, pc, 2, "Other", "", 7, 0)), "at most 1")
    expect_error("a requested return cannot be refunded before approval",
                 lambda: db.call_procedure("sp_update_return", (ret, "refund", "", 1)), "Invalid return status")
    before = stock(pc)
    db.call_procedure("sp_update_return", (ret, "approve", "ok", 1))
    check("return approved", val("SELECT status FROM return_request WHERE return_id = %s", (ret,)) == "Approved")
    db.call_procedure("sp_update_return", (ret, "refund", "refunded", 1))
    check("refund puts the item back into stock", stock(pc) == before + 1, (before, stock(pc)))
    check("part-refunded order keeps payment Paid", val("SELECT payment_status FROM payment WHERE order_id = %s", (c2,)) == "Paid")
    ret2 = db.call_procedure("sp_request_return", (cid, c2, pc, 1, "No longer needed", "", 7, 0))[7]
    db.call_procedure("sp_update_return", (ret2, "approve", "", 1))
    before = stock(pc)
    db.call_procedure("sp_update_return", (ret2, "refund", "", 0))
    check("refund with 'do not restock' leaves the stock alone", stock(pc) == before, (before, stock(pc)))
    check("everything refunded: payment becomes Refunded",
          val("SELECT payment_status FROM payment WHERE order_id = %s", (c2,)) == "Refunded")
    expect_error("a refunded return cannot change again", lambda: db.call_procedure("sp_update_return", (ret2, "approve", "", 1)),
                 "Invalid return status")
    expect_error("unknown return action is refused", lambda: db.call_procedure("sp_update_return", (ret2, "explode", "", 1)), "Unknown action")

    db.call_procedure("sp_add_to_cart", (cid, ps, 2))
    o3 = db.call_procedure("sp_place_order", (cid, "UPI", "", "", 40, 499, 0))[6]
    for status in ("Packed", "Shipped", "Delivered"):
        db.call_procedure("sp_update_order_status", (o3, status))
    r3 = db.call_procedure("sp_request_return", (cid, o3, ps, 2, "Wrong item received", "", 7, 0))[7]
    db.call_procedure("sp_update_return", (r3, "reject", "not eligible", 1))
    check("rejected return is saved with the note",
          one("SELECT status, admin_note FROM return_request WHERE return_id = %s", (r3,)) == {"status": "Rejected", "admin_note": "not eligible"})
    db.call_procedure("sp_request_return", (cid, o3, ps, 2, "Other", "", 7, 0))
    check("after a rejection the item can be requested again", True)

    print("\n12. Wishlist, address book, product pictures")
    db.execute("INSERT INTO wishlist (customer_id, product_id) VALUES (%s, %s)", (cid, pc))
    expect_error("an item can be saved to the wishlist only once",
                 lambda: db.execute("INSERT INTO wishlist (customer_id, product_id) VALUES (%s, %s)", (cid, pc)), "duplicate")
    expect_error("an address needs a 10-digit phone",
                 lambda: db.execute("INSERT INTO address (customer_id, full_name, phone, line1, city, pincode) "
                                    "VALUES (%s, 'A', '123', 'x', 'Pune', '411001')", (cid,)), "")
    db.execute("INSERT INTO address (customer_id, full_name, phone, line1, city, pincode, is_default) "
               "VALUES (%s, 'A', '9000000001', 'x', 'Pune', '411001', 1)", (cid,))
    check("a valid address is saved", val("SELECT COUNT(*) FROM address WHERE customer_id = %s", (cid,)) == 1)
    db.execute("INSERT INTO product_image (product_id, image_url, sort_order) VALUES (%s, 'https://x/1.png', 0)", (pc,))
    db.execute("INSERT INTO product_image (product_id, image_url, sort_order) VALUES (%s, 'https://x/2.png', 1)", (pc,))
    check("a product can hold several pictures", val("SELECT COUNT(*) FROM product_image WHERE product_id = %s", (pc,)) == 2)

    print("\n13. Views")
    for view in ("vw_product_catalog", "vw_order_summary", "vw_order_details", "vw_sales_by_category", "vw_top_products",
                 "vw_top_customers", "vw_low_stock", "vw_pending_shipments", "vw_monthly_sales", "vw_review_details",
                 "vw_customer_safe", "vw_active_products", "vw_product_supply", "vw_purchase_orders",
                 "vw_seller_listings", "vw_seller_summary", "vw_returns"):
        try:
            db.query_all("SELECT * FROM " + view + " LIMIT 1")
            check("view " + view, True)
        except db.DBError as e:
            check("view " + view, False, e)


def wipe():
    """Remove everything the test created (the admin login stays)."""
    conn = db._connection()
    cur = conn.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in ("purchase_order_log", "purchase_order", "seller_listing", "seller", "low_stock_alert", "price_history",
                  "order_status_log", "review", "shipment", "payment", "order_item", "orders", "cart_item", "cart",
                  "customer", "inventory", "product", "category", "return_request", "coupon_redemption", "order_extra",
                  "coupon", "wishlist", "address", "product_image"):
        cur.execute("TRUNCATE TABLE " + table)
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    cur.close()


if __name__ == "__main__":
    try:
        run()
    except db.DBError as e:
        FAILED.append("unexpected database error")
        print("\nUNEXPECTED DATABASE ERROR %s: %s" % (getattr(e, "errno", "?"), getattr(e, "msg", e)))
    finally:
        try:
            wipe()
            print("\nTest data removed.")
        except db.DBError as e:
            print("\nCould not clean up (%s). Run: python scripts/setup_db.py --reset --yes" % e)
    print("\n%d checks passed, %d failed." % (len(PASSED), len(FAILED)))
    sys.exit(1 if FAILED else 0)
