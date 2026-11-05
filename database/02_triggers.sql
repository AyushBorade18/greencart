-- =====================================================================
--  GreenCart  |  02_triggers.sql  |  TRIGGERS  (business rules in the DB)
--
--  A trigger runs by itself when a row is inserted / updated / deleted.
--  The website never has to remember these rules: even a bug in the
--  website (or a manual SQL statement) cannot break them.
--
--  Errors raised with SIGNAL SQLSTATE '45000' reach the website as
--  MySQL error 1644 and the message is shown to the user.
--
--  Safe to run again (every trigger is dropped first).
-- =====================================================================

DROP TRIGGER IF EXISTS trg_customer_before_insert;
DROP TRIGGER IF EXISTS trg_customer_after_insert;
DROP TRIGGER IF EXISTS trg_product_before_insert;
DROP TRIGGER IF EXISTS trg_product_before_update;
DROP TRIGGER IF EXISTS trg_product_after_insert;
DROP TRIGGER IF EXISTS trg_product_after_update;
DROP TRIGGER IF EXISTS trg_cart_item_before_insert;
DROP TRIGGER IF EXISTS trg_cart_item_before_update;
DROP TRIGGER IF EXISTS trg_order_item_before_insert;
DROP TRIGGER IF EXISTS trg_order_item_after_insert;
DROP TRIGGER IF EXISTS trg_orders_after_insert;
DROP TRIGGER IF EXISTS trg_orders_before_update;
DROP TRIGGER IF EXISTS trg_orders_after_update;
DROP TRIGGER IF EXISTS trg_orders_before_delete;
DROP TRIGGER IF EXISTS trg_payment_before_insert;
DROP TRIGGER IF EXISTS trg_review_before_insert;
DROP TRIGGER IF EXISTS trg_seller_before_insert;
DROP TRIGGER IF EXISTS trg_listing_before_insert;
DROP TRIGGER IF EXISTS trg_listing_before_update;
DROP TRIGGER IF EXISTS trg_po_before_insert;
DROP TRIGGER IF EXISTS trg_po_before_update;
DROP TRIGGER IF EXISTS trg_po_after_insert;
DROP TRIGGER IF EXISTS trg_po_after_update;
DROP TRIGGER IF EXISTS trg_po_before_delete;
DROP TRIGGER IF EXISTS trg_coupon_before_insert;
DROP TRIGGER IF EXISTS trg_coupon_before_update;
DROP TRIGGER IF EXISTS trg_return_before_update;
DROP TRIGGER IF EXISTS trg_return_after_update;

DELIMITER $$

-- =====================================================================
--  CUSTOMER
-- =====================================================================

-- T1. Clean the email (lower case, no spaces)
CREATE TRIGGER trg_customer_before_insert
BEFORE INSERT ON customer
FOR EACH ROW
BEGIN
    SET NEW.email = LOWER(TRIM(NEW.email));
END$$

-- T2. Every new customer automatically gets an (empty) cart
CREATE TRIGGER trg_customer_after_insert
AFTER INSERT ON customer
FOR EACH ROW
BEGIN
    INSERT INTO cart (customer_id) VALUES (NEW.customer_id);
END$$

-- =====================================================================
--  PRODUCT   (no negative or zero prices, ever)
-- =====================================================================

-- T3. Price must be above zero (repeats the CHECK so old MySQL is safe too)
CREATE TRIGGER trg_product_before_insert
BEFORE INSERT ON product
FOR EACH ROW
BEGIN
    IF NEW.price IS NULL OR NEW.price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;
    SET NEW.product_name = TRIM(NEW.product_name);
END$$

CREATE TRIGGER trg_product_before_update
BEFORE UPDATE ON product
FOR EACH ROW
BEGIN
    IF NEW.price IS NULL OR NEW.price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;
END$$

-- T4. Every new product automatically gets a stock row (0 items)
CREATE TRIGGER trg_product_after_insert
AFTER INSERT ON product
FOR EACH ROW
BEGIN
    INSERT INTO inventory (product_id, quantity_available) VALUES (NEW.product_id, 0);
END$$

-- T5. Remember every price change
CREATE TRIGGER trg_product_after_update
AFTER UPDATE ON product
FOR EACH ROW
BEGIN
    IF OLD.price <> NEW.price THEN
        INSERT INTO price_history (product_id, old_price, new_price)
        VALUES (NEW.product_id, OLD.price, NEW.price);
    END IF;
END$$

-- =====================================================================
--  CART   (cannot hold more than the stock)
-- =====================================================================

CREATE TRIGGER trg_cart_item_before_insert
BEFORE INSERT ON cart_item
FOR EACH ROW
BEGIN
    DECLARE v_stock INT;
    SELECT quantity_available INTO v_stock FROM inventory WHERE product_id = NEW.product_id;
    IF v_stock IS NULL OR NEW.quantity > v_stock THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Requested quantity is more than the available stock';
    END IF;
END$$

CREATE TRIGGER trg_cart_item_before_update
BEFORE UPDATE ON cart_item
FOR EACH ROW
BEGIN
    DECLARE v_stock INT;
    IF NEW.quantity <> OLD.quantity THEN
        SELECT quantity_available INTO v_stock FROM inventory WHERE product_id = NEW.product_id;
        IF v_stock IS NULL OR NEW.quantity > v_stock THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Requested quantity is more than the available stock';
        END IF;
    END IF;
END$$

-- =====================================================================
--  ORDER ITEMS   (real price, stock check, stock cut, order total)
-- =====================================================================

-- T6. The price always comes from the product table, never from the caller
CREATE TRIGGER trg_order_item_before_insert
BEFORE INSERT ON order_item
FOR EACH ROW
BEGIN
    DECLARE v_price  DECIMAL(10,2);
    DECLARE v_active BOOLEAN;
    DECLARE v_stock  INT;

    SELECT p.price, p.is_active, i.quantity_available
    INTO   v_price, v_active, v_stock
    FROM   product p
    JOIN   inventory i ON i.product_id = p.product_id
    WHERE  p.product_id = NEW.product_id;

    IF v_price IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Product not found';
    END IF;
    IF v_active = 0 THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'A product in your cart is no longer available';
    END IF;
    IF NEW.quantity > v_stock THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Insufficient stock to place this order';
    END IF;

    SET NEW.unit_price = v_price;
END$$

-- T7. Cut the stock, add to the order total, raise a low-stock alert
CREATE TRIGGER trg_order_item_after_insert
AFTER INSERT ON order_item
FOR EACH ROW
BEGIN
    DECLARE v_left    INT;
    DECLARE v_reorder INT;

    UPDATE inventory
    SET    quantity_available = quantity_available - NEW.quantity
    WHERE  product_id = NEW.product_id;

    UPDATE orders
    SET    total_amount = total_amount + (NEW.quantity * NEW.unit_price)
    WHERE  order_id = NEW.order_id;

    SELECT quantity_available, reorder_level INTO v_left, v_reorder
    FROM   inventory WHERE product_id = NEW.product_id;

    IF v_left <= v_reorder THEN
        INSERT INTO low_stock_alert (product_id, quantity_left) VALUES (NEW.product_id, v_left);
    END IF;
END$$

-- =====================================================================
--  ORDERS   (life cycle: Confirmed > Packed > Shipped > Delivered)
-- =====================================================================

-- T8. A new order gets its shipment row and the first history line
CREATE TRIGGER trg_orders_after_insert
AFTER INSERT ON orders
FOR EACH ROW
BEGIN
    INSERT INTO shipment (order_id) VALUES (NEW.order_id);
    INSERT INTO order_status_log (order_id, old_status, new_status)
    VALUES (NEW.order_id, NULL, NEW.order_status);
END$$

-- T9. Only sensible status jumps are allowed
CREATE TRIGGER trg_orders_before_update
BEFORE UPDATE ON orders
FOR EACH ROW
BEGIN
    IF OLD.order_status <> NEW.order_status THEN
        IF NOT (
               (OLD.order_status = 'Confirmed' AND NEW.order_status IN ('Packed', 'Cancelled'))
            OR (OLD.order_status = 'Packed'    AND NEW.order_status IN ('Shipped', 'Cancelled'))
            OR (OLD.order_status = 'Shipped'   AND NEW.order_status = 'Delivered')
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid order status change';
        END IF;
    END IF;
END$$

-- T10. React to every status change
--      any change  -> history line
--      Shipped     -> tracking number + dates
--      Delivered   -> delivered date, cash-on-delivery becomes Paid
--      Cancelled   -> stock comes back, payment is refunded, coupon is given back
CREATE TRIGGER trg_orders_after_update
AFTER UPDATE ON orders
FOR EACH ROW
BEGIN
    IF OLD.order_status <> NEW.order_status THEN

        INSERT INTO order_status_log (order_id, old_status, new_status)
        VALUES (NEW.order_id, OLD.order_status, NEW.order_status);

        IF NEW.order_status = 'Shipped' THEN
            UPDATE shipment
            SET    tracking_number   = CONCAT('GC', DATE_FORMAT(NOW(), '%y%m%d'), LPAD(NEW.order_id, 6, '0')),
                   shipped_date      = NOW(),
                   expected_delivery = DATE_ADD(CURDATE(), INTERVAL 4 DAY)
            WHERE  order_id = NEW.order_id;
        END IF;

        IF NEW.order_status = 'Delivered' THEN
            UPDATE shipment SET delivered_date = NOW() WHERE order_id = NEW.order_id;
            UPDATE payment  SET payment_status = 'Paid', payment_date = NOW()
            WHERE  order_id = NEW.order_id AND payment_status = 'Pending';
        END IF;

        IF NEW.order_status = 'Cancelled' THEN
            UPDATE inventory i
            JOIN   order_item oi ON oi.product_id = i.product_id
            SET    i.quantity_available = i.quantity_available + oi.quantity
            WHERE  oi.order_id = NEW.order_id;

            UPDATE payment SET payment_status = 'Refunded'
            WHERE  order_id = NEW.order_id AND payment_status = 'Paid';
            UPDATE payment SET payment_status = 'Failed'
            WHERE  order_id = NEW.order_id AND payment_status = 'Pending';

            -- the customer gets the coupon back
            DELETE FROM coupon_redemption WHERE order_id = NEW.order_id;
        END IF;
    END IF;
END$$

-- T11. Shipped or delivered orders are history: they cannot be deleted
CREATE TRIGGER trg_orders_before_delete
BEFORE DELETE ON orders
FOR EACH ROW
BEGIN
    IF OLD.order_status IN ('Shipped', 'Delivered') THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Shipped or delivered orders cannot be deleted';
    END IF;
END$$

-- =====================================================================
--  PAYMENT and REVIEW
-- =====================================================================

-- T12. The payment must equal the order total
CREATE TRIGGER trg_payment_before_insert
BEFORE INSERT ON payment
FOR EACH ROW
BEGIN
    DECLARE v_total DECIMAL(12,2);
    SELECT total_amount INTO v_total FROM orders WHERE order_id = NEW.order_id;
    IF v_total IS NULL OR NEW.amount <> v_total THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Payment amount does not match the order total';
    END IF;
END$$

-- T13. A review is allowed only after the product was delivered to the customer
CREATE TRIGGER trg_review_before_insert
BEFORE INSERT ON review
FOR EACH ROW
BEGIN
    IF NOT EXISTS (SELECT 1
                   FROM   orders o
                   JOIN   order_item oi ON oi.order_id = o.order_id
                   WHERE  o.customer_id = NEW.customer_id
                     AND  oi.product_id = NEW.product_id
                     AND  o.order_status = 'Delivered') THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'You can review a product only after it is delivered';
    END IF;
END$$

-- =====================================================================
--  SELLER MODULE
-- =====================================================================

-- T14. Clean the seller email
CREATE TRIGGER trg_seller_before_insert
BEFORE INSERT ON seller
FOR EACH ROW
BEGIN
    SET NEW.email = LOWER(TRIM(NEW.email));
END$$

-- T15. Only APPROVED sellers can create listings, and the price must be real
CREATE TRIGGER trg_listing_before_insert
BEFORE INSERT ON seller_listing
FOR EACH ROW
BEGIN
    DECLARE v_status VARCHAR(20);
    SELECT status INTO v_status FROM seller WHERE seller_id = NEW.seller_id;
    IF v_status IS NULL OR v_status <> 'Approved' THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Your seller account is not approved yet';
    END IF;
    IF NEW.unit_price IS NULL OR NEW.unit_price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;
END$$

CREATE TRIGGER trg_listing_before_update
BEFORE UPDATE ON seller_listing
FOR EACH ROW
BEGIN
    IF NEW.unit_price IS NULL OR NEW.unit_price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;
END$$

-- T16. A direct order must match a real, active listing with enough stock
--      (also copies the seller and wholesale price into the order)
CREATE TRIGGER trg_po_before_insert
BEFORE INSERT ON purchase_order
FOR EACH ROW
BEGIN
    DECLARE v_seller   INT;
    DECLARE v_product  INT;
    DECLARE v_price    DECIMAL(10,2);
    DECLARE v_qty      INT;
    DECLARE v_min      INT;
    DECLARE v_active   BOOLEAN;
    DECLARE v_status   VARCHAR(20);

    IF NEW.max_unit_price IS NOT NULL AND NEW.max_unit_price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;

    IF NEW.listing_id IS NOT NULL THEN
        SELECT seller_id, product_id, unit_price, quantity_available, min_order_qty, is_active
        INTO   v_seller, v_product, v_price, v_qty, v_min, v_active
        FROM   seller_listing WHERE listing_id = NEW.listing_id;

        IF v_seller IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Seller listing not found';
        END IF;

        SELECT status INTO v_status FROM seller WHERE seller_id = v_seller;
        IF v_status <> 'Approved' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This seller is not approved';
        END IF;
        IF v_active = 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This listing is not active';
        END IF;
        IF v_product IS NULL OR v_product <> NEW.product_id THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This listing is not linked to this product';
        END IF;
        IF NEW.quantity_requested < v_min THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity is below the seller minimum order';
        END IF;
        IF NEW.quantity_requested > v_qty THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity is more than the seller has available';
        END IF;

        SET NEW.seller_id        = v_seller;
        SET NEW.unit_price       = v_price;
        SET NEW.quantity_offered = NEW.quantity_requested;
        SET NEW.status           = 'Requested';
    ELSE
        IF NEW.seller_id IS NOT NULL THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'An open request cannot name a seller';
        END IF;
        SET NEW.status = 'Open';
    END IF;
END$$

-- T17. Purchase order status can only move along the allowed paths
CREATE TRIGGER trg_po_before_update
BEFORE UPDATE ON purchase_order
FOR EACH ROW
BEGIN
    DECLARE v_listing_qty INT;

    IF NEW.unit_price IS NOT NULL AND NEW.unit_price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;

    IF OLD.status <> NEW.status THEN
        IF NOT (
               (OLD.status = 'Open'      AND NEW.status IN ('Offered', 'Cancelled'))
            OR (OLD.status = 'Offered'   AND NEW.status IN ('Accepted', 'Open', 'Cancelled'))
            OR (OLD.status = 'Requested' AND NEW.status IN ('Accepted', 'Declined', 'Cancelled'))
            OR (OLD.status = 'Accepted'  AND NEW.status IN ('Shipped', 'Cancelled'))
            OR (OLD.status = 'Shipped'   AND NEW.status = 'Received')
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid purchase order status change';
        END IF;

        IF NEW.status = 'Accepted' AND NEW.listing_id IS NOT NULL THEN
            SELECT quantity_available INTO v_listing_qty
            FROM seller_listing WHERE listing_id = NEW.listing_id;
            IF v_listing_qty IS NULL OR v_listing_qty < NEW.quantity_offered THEN
                SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'The listing no longer has enough stock';
            END IF;
        END IF;

        IF NEW.status = 'Shipped'  THEN SET NEW.shipped_at  = NOW(); END IF;
        IF NEW.status = 'Received' THEN SET NEW.received_at = NOW(); END IF;
    END IF;
END$$

-- T18. First history line of a purchase order
CREATE TRIGGER trg_po_after_insert
AFTER INSERT ON purchase_order
FOR EACH ROW
BEGIN
    INSERT INTO purchase_order_log (po_id, old_status, new_status)
    VALUES (NEW.po_id, NULL, NEW.status);
END$$

-- T19. React to every purchase order status change
--      Accepted  -> the seller's listing stock goes down
--      Cancelled (after Accepted) -> the listing stock comes back
--      Received  -> STORE INVENTORY GOES UP (the only way stock is added)
CREATE TRIGGER trg_po_after_update
AFTER UPDATE ON purchase_order
FOR EACH ROW
BEGIN
    IF OLD.status <> NEW.status THEN
        INSERT INTO purchase_order_log (po_id, old_status, new_status)
        VALUES (NEW.po_id, OLD.status, NEW.status);

        IF NEW.status = 'Accepted' AND NEW.listing_id IS NOT NULL THEN
            UPDATE seller_listing
            SET    quantity_available = quantity_available - NEW.quantity_offered
            WHERE  listing_id = NEW.listing_id;
        END IF;

        IF NEW.status = 'Cancelled' AND OLD.status = 'Accepted' AND NEW.listing_id IS NOT NULL THEN
            UPDATE seller_listing
            SET    quantity_available = quantity_available + NEW.quantity_offered
            WHERE  listing_id = NEW.listing_id;
        END IF;

        IF NEW.status = 'Received' THEN
            UPDATE inventory
            SET    quantity_available = quantity_available + NEW.quantity_offered
            WHERE  product_id = NEW.product_id;
        END IF;
    END IF;
END$$

-- T20. A purchase order that is already in progress cannot be deleted
CREATE TRIGGER trg_po_before_delete
BEFORE DELETE ON purchase_order
FOR EACH ROW
BEGIN
    IF OLD.status IN ('Accepted', 'Shipped', 'Received') THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Accepted, shipped or received orders cannot be deleted';
    END IF;
END$$

-- =====================================================================
--  COUPONS
-- =====================================================================

-- T21. Codes are stored in capitals; the value must make sense
CREATE TRIGGER trg_coupon_before_insert
BEFORE INSERT ON coupon
FOR EACH ROW
BEGIN
    SET NEW.code = UPPER(TRIM(NEW.code));
    IF NEW.discount_value IS NULL OR NEW.discount_value <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Discount must be greater than 0';
    END IF;
    IF NEW.discount_type = 'percent' AND NEW.discount_value > 100 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A percentage discount cannot be more than 100';
    END IF;
    IF NEW.valid_from IS NOT NULL AND NEW.valid_to IS NOT NULL AND NEW.valid_to <= NEW.valid_from THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The end date must be after the start date';
    END IF;
END$$

CREATE TRIGGER trg_coupon_before_update
BEFORE UPDATE ON coupon
FOR EACH ROW
BEGIN
    SET NEW.code = UPPER(TRIM(NEW.code));
    IF NEW.discount_value IS NULL OR NEW.discount_value <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Discount must be greater than 0';
    END IF;
    IF NEW.discount_type = 'percent' AND NEW.discount_value > 100 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A percentage discount cannot be more than 100';
    END IF;
END$$

-- =====================================================================
--  RETURNS AND REFUNDS
-- =====================================================================

-- T22. Allowed moves: Requested > Approved | Rejected ; Approved > Refunded | Rejected
CREATE TRIGGER trg_return_before_update
BEFORE UPDATE ON return_request
FOR EACH ROW
BEGIN
    IF OLD.status <> NEW.status THEN
        IF NOT (
               (OLD.status = 'Requested' AND NEW.status IN ('Approved', 'Rejected'))
            OR (OLD.status = 'Approved'  AND NEW.status IN ('Refunded', 'Rejected'))
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid return status change';
        END IF;
    END IF;
END$$

-- T23. When a return is REFUNDED: the item goes back into stock (if chosen)
--      and the payment becomes Refunded once all the goods have been paid back.
CREATE TRIGGER trg_return_after_update
AFTER UPDATE ON return_request
FOR EACH ROW
BEGIN
    DECLARE v_refunded DECIMAL(12,2);
    DECLARE v_paid     DECIMAL(12,2);
    DECLARE v_fee      DECIMAL(8,2);

    IF OLD.status <> NEW.status AND NEW.status = 'Refunded' THEN
        IF NEW.restock = 1 THEN
            UPDATE inventory
            SET    quantity_available = quantity_available + NEW.quantity
            WHERE  product_id = NEW.product_id;
        END IF;

        SELECT COALESCE(SUM(refund_amount), 0) INTO v_refunded
        FROM   return_request
        WHERE  order_id = NEW.order_id AND status = 'Refunded';

        SELECT amount INTO v_paid FROM payment WHERE order_id = NEW.order_id;

        SELECT COALESCE(MAX(delivery_fee), 0) INTO v_fee
        FROM   order_extra WHERE order_id = NEW.order_id;

        IF v_paid IS NOT NULL AND v_refunded >= (v_paid - v_fee) THEN
            UPDATE payment SET payment_status = 'Refunded'
            WHERE  order_id = NEW.order_id AND payment_status = 'Paid';
        END IF;
    END IF;
END$$

DELIMITER ;
