-- =====================================================================
--  GreenCart  |  03_routines.sql  |  FUNCTIONS and STORED PROCEDURES
--
--  Functions   : return one value, can be used inside SELECT
--  Procedures  : do a whole job as ONE transaction (all or nothing)
--  Cursors     : sp_place_order
--  Transactions: sp_place_order, sp_add_product and the seller procedures
--
--  Run AFTER 02_triggers.sql. Safe to run again.
--  Stock can NOT be added by hand: it only goes up when a seller's
--  purchase order is received (see trg_po_after_update).
-- =====================================================================

DROP FUNCTION  IF EXISTS fn_cart_total;
DROP FUNCTION  IF EXISTS fn_avg_rating;
DROP FUNCTION  IF EXISTS fn_customer_total_spent;
DROP FUNCTION  IF EXISTS fn_customer_tier;
DROP FUNCTION  IF EXISTS fn_stock_status;
DROP FUNCTION  IF EXISTS fn_seller_earnings;
DROP PROCEDURE IF EXISTS sp_add_to_cart;
DROP PROCEDURE IF EXISTS sp_place_order;
DROP PROCEDURE IF EXISTS sp_cancel_order;
DROP PROCEDURE IF EXISTS sp_update_order_status;
DROP PROCEDURE IF EXISTS sp_add_product;
DROP PROCEDURE IF EXISTS sp_restock_product;        -- removed: stock only comes from purchase orders
DROP PROCEDURE IF EXISTS sp_restock_low_items;      -- removed: stock only comes from purchase orders
DROP PROCEDURE IF EXISTS sp_raise_restock_request;
DROP PROCEDURE IF EXISTS sp_order_from_listing;
DROP PROCEDURE IF EXISTS sp_offer_purchase_order;
DROP PROCEDURE IF EXISTS sp_seller_po_action;
DROP PROCEDURE IF EXISTS sp_admin_po_action;
DROP PROCEDURE IF EXISTS sp_add_listing_to_store;
DROP PROCEDURE IF EXISTS sp_validate_coupon;
DROP PROCEDURE IF EXISTS sp_request_return;
DROP PROCEDURE IF EXISTS sp_update_return;

DELIMITER $$

-- =====================================================================
--  FUNCTIONS  (return one value, can be used inside SELECT)
--  READS SQL DATA tells MySQL the function only reads tables
-- =====================================================================

-- F1. Total price of everything in a customer's cart
CREATE FUNCTION fn_cart_total(p_customer_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_total DECIMAL(12,2);

    SELECT COALESCE(SUM(p.price * ci.quantity), 0)
    INTO   v_total
    FROM   cart c
    JOIN   cart_item ci ON ci.cart_id    = c.cart_id
    JOIN   product p    ON p.product_id  = ci.product_id
    WHERE  c.customer_id = p_customer_id;

    RETURN v_total;
END$$

-- F2. Average rating of a product (0 if nobody reviewed it)
CREATE FUNCTION fn_avg_rating(p_product_id INT)
RETURNS DECIMAL(3,2)
READS SQL DATA
BEGIN
    DECLARE v_avg DECIMAL(3,2);

    SELECT ROUND(COALESCE(AVG(rating), 0), 2)
    INTO   v_avg
    FROM   review
    WHERE  product_id = p_product_id;

    RETURN v_avg;
END$$

-- F3. Total money a customer has spent (cancelled orders are ignored)
CREATE FUNCTION fn_customer_total_spent(p_customer_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_spent DECIMAL(12,2);

    SELECT COALESCE(SUM(total_amount), 0)
    INTO   v_spent
    FROM   orders
    WHERE  customer_id  = p_customer_id
      AND  order_status <> 'Cancelled';

    RETURN v_spent;
END$$

-- F4. Customer tier based on total spent (uses function F3)
CREATE FUNCTION fn_customer_tier(p_customer_id INT)
RETURNS VARCHAR(10)
READS SQL DATA
BEGIN
    DECLARE v_spent DECIMAL(12,2);
    SET v_spent = fn_customer_total_spent(p_customer_id);

    IF v_spent >= 10000 THEN
        RETURN 'Gold';
    ELSEIF v_spent >= 5000 THEN
        RETURN 'Silver';
    ELSE
        RETURN 'Bronze';
    END IF;
END$$

-- F5. Stock status text for a product
CREATE FUNCTION fn_stock_status(p_product_id INT)
RETURNS VARCHAR(15)
READS SQL DATA
BEGIN
    DECLARE v_qty     INT;
    DECLARE v_reorder INT;

    SELECT quantity_available, reorder_level
    INTO   v_qty, v_reorder
    FROM   inventory
    WHERE  product_id = p_product_id;

    RETURN CASE
               WHEN v_qty IS NULL      THEN 'Unknown'
               WHEN v_qty = 0          THEN 'Out of Stock'
               WHEN v_qty <= v_reorder THEN 'Low Stock'
               ELSE 'In Stock'
           END;
END$$

-- =====================================================================
--  PROCEDURES
-- =====================================================================

-- P1. Add a product to the customer's cart
--     (if it is already there, the quantity is increased)
CREATE PROCEDURE sp_add_to_cart(
    IN p_customer_id INT,
    IN p_product_id  INT,
    IN p_quantity    INT
)
BEGIN
    DECLARE v_cart_id INT;

    IF p_quantity <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be at least 1';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM product
                   WHERE product_id = p_product_id AND is_active = 1) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This product is not available';
    END IF;

    SELECT cart_id INTO v_cart_id FROM cart WHERE customer_id = p_customer_id;
    IF v_cart_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cart not found for this customer';
    END IF;

    INSERT INTO cart_item (cart_id, product_id, quantity)
    VALUES (v_cart_id, p_product_id, p_quantity)
    ON DUPLICATE KEY UPDATE quantity = quantity + p_quantity;
END$$

-- P2. PLACE ORDER  (the heart of the project)
--     One TRANSACTION: either everything succeeds, or nothing is saved.
--     Steps: check cart -> lock stock rows -> create order -> CURSOR loops
--     over cart items and inserts order items (triggers cut stock and
--     calculate total) -> record payment -> empty cart -> COMMIT
CREATE PROCEDURE sp_place_order(
    IN  p_customer_id      INT,
    IN  p_payment_method   VARCHAR(30),
    IN  p_shipping_address VARCHAR(300),
    IN  p_coupon_code      VARCHAR(30),      -- '' = no coupon
    IN  p_delivery_fee     DECIMAL(8,2),     -- charged when the order is small ...
    IN  p_free_above       DECIMAL(10,2),    -- ... free from this amount (0 = always charge)
    OUT p_order_id         INT
)
BEGIN
    DECLARE v_cart_id    INT;
    DECLARE v_item_count INT DEFAULT 0;
    DECLARE v_locked     INT;
    DECLARE v_address    VARCHAR(300);
    DECLARE v_total      DECIMAL(12,2);
    DECLARE v_subtotal   DECIMAL(12,2);
    DECLARE v_discount   DECIMAL(12,2) DEFAULT 0;
    DECLARE v_coupon_id  INT DEFAULT NULL;
    DECLARE v_fee        DECIMAL(8,2) DEFAULT 0;
    DECLARE v_product_id INT;
    DECLARE v_quantity   INT;
    DECLARE v_done       INT DEFAULT 0;

    -- Cursor: goes through the cart items one by one
    DECLARE cur_cart CURSOR FOR
        SELECT product_id, quantity FROM cart_item WHERE cart_id = v_cart_id;

    -- Handlers
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;      -- undo everything done so far
        RESIGNAL;      -- pass the original error message to the caller
    END;

    IF p_payment_method IS NULL OR p_payment_method NOT IN
       ('UPI', 'Credit Card', 'Debit Card', 'Net Banking', 'Cash on Delivery') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Please choose a valid payment method';
    END IF;

    START TRANSACTION;

    SELECT cart_id INTO v_cart_id FROM cart WHERE customer_id = p_customer_id;
    SELECT COUNT(*) INTO v_item_count FROM cart_item WHERE cart_id = v_cart_id;

    IF v_item_count = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Your cart is empty';
    END IF;

    -- Lock the stock rows so two customers cannot buy the last item together
    SELECT COUNT(*) INTO v_locked
    FROM   inventory i
    JOIN   cart_item ci ON ci.product_id = i.product_id
    WHERE  ci.cart_id = v_cart_id
    FOR UPDATE;

    -- Use the customer's saved address if none was typed
    IF p_shipping_address IS NULL OR TRIM(p_shipping_address) = '' THEN
        SELECT CONCAT(address, ', ', city, ' ', pincode)
        INTO   v_address
        FROM   customer
        WHERE  customer_id = p_customer_id;
    ELSE
        SET v_address = TRIM(p_shipping_address);
    END IF;

    INSERT INTO orders (customer_id, shipping_address)
    VALUES (p_customer_id, v_address);
    SET p_order_id = LAST_INSERT_ID();

    -- Loop over the cart with the cursor
    SET v_done = 0;
    OPEN cur_cart;
    cart_loop: LOOP
        FETCH cur_cart INTO v_product_id, v_quantity;
        IF v_done = 1 THEN
            LEAVE cart_loop;
        END IF;

        -- unit_price is filled by trigger, stock is reduced by trigger
        INSERT INTO order_item (order_id, product_id, quantity, unit_price)
        VALUES (p_order_id, v_product_id, v_quantity, 0);
    END LOOP;
    CLOSE cur_cart;

    -- Coupon and delivery fee (the triggers have already added up the items)
    SELECT total_amount INTO v_subtotal FROM orders WHERE order_id = p_order_id;

    IF p_coupon_code IS NOT NULL AND TRIM(p_coupon_code) <> '' THEN
        CALL sp_validate_coupon(p_coupon_code, p_customer_id, v_subtotal, v_coupon_id, v_discount);
    END IF;

    IF p_free_above > 0 AND (v_subtotal - v_discount) >= p_free_above THEN
        SET v_fee = 0;
    ELSE
        SET v_fee = COALESCE(p_delivery_fee, 0);
    END IF;

    UPDATE orders
    SET    total_amount = v_subtotal - v_discount + v_fee
    WHERE  order_id = p_order_id;

    INSERT INTO order_extra (order_id, subtotal, coupon_id, coupon_code, discount_amount, delivery_fee)
    VALUES (p_order_id, v_subtotal, v_coupon_id, NULLIF(UPPER(TRIM(p_coupon_code)), ''), v_discount, v_fee);

    IF v_coupon_id IS NOT NULL THEN
        INSERT INTO coupon_redemption (coupon_id, customer_id, order_id, discount_amount)
        VALUES (v_coupon_id, p_customer_id, p_order_id, v_discount);
    END IF;

    -- Record the payment (online = Paid, Cash on Delivery = Pending)
    SELECT total_amount INTO v_total FROM orders WHERE order_id = p_order_id;

    INSERT INTO payment (order_id, payment_method, amount, payment_status, transaction_ref)
    VALUES (p_order_id, p_payment_method, v_total,
            IF(p_payment_method = 'Cash on Delivery', 'Pending', 'Paid'),
            CONCAT('TXN', DATE_FORMAT(NOW(), '%y%m%d%H%i%s'), LPAD(p_order_id, 4, '0')));

    -- Empty the cart
    DELETE FROM cart_item WHERE cart_id = v_cart_id;

    COMMIT;
END$$

-- P3. Customer cancels his own order (only Confirmed / Packed orders)
CREATE PROCEDURE sp_cancel_order(
    IN p_order_id    INT,
    IN p_customer_id INT
)
BEGIN
    DECLARE v_status VARCHAR(20);

    SELECT order_status INTO v_status
    FROM   orders
    WHERE  order_id = p_order_id AND customer_id = p_customer_id;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Order not found for this customer';
    ELSEIF v_status NOT IN ('Confirmed', 'Packed') THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Only Confirmed or Packed orders can be cancelled';
    END IF;

    UPDATE orders SET order_status = 'Cancelled' WHERE order_id = p_order_id;
END$$

-- P4. Admin changes the status of an order
CREATE PROCEDURE sp_update_order_status(
    IN p_order_id   INT,
    IN p_new_status VARCHAR(20)
)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM orders WHERE order_id = p_order_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Order not found';
    END IF;

    UPDATE orders SET order_status = p_new_status WHERE order_id = p_order_id;
END$$

-- =====================================================================
--  ADMIN: add a product
-- =====================================================================
-- Admin adds a product AND its opening stock as ONE transaction
CREATE PROCEDURE sp_add_product(
    IN  p_category_id   INT,
    IN  p_name          VARCHAR(100),
    IN  p_description   VARCHAR(500),
    IN  p_price         DECIMAL(10,2),
    IN  p_stock         INT,
    IN  p_reorder_level INT,
    IN  p_image_url     VARCHAR(500),
    OUT p_product_id    INT
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    INSERT INTO product (category_id, product_name, description, price, image_url)
    VALUES (p_category_id, TRIM(p_name), p_description, p_price, NULLIF(TRIM(p_image_url), ''));
    SET p_product_id = LAST_INSERT_ID();

    UPDATE inventory
    SET    quantity_available = p_stock,
           reorder_level      = p_reorder_level
    WHERE  product_id = p_product_id;

    COMMIT;
END$$

-- =====================================================================
--  COUPONS
-- =====================================================================
-- Checks a coupon for a customer and a cart value.
-- Gives back the coupon id and the discount, or stops with a clear message.
-- Used by the cart page (preview) and by sp_place_order (the real thing).
CREATE PROCEDURE sp_validate_coupon(
    IN  p_code        VARCHAR(30),
    IN  p_customer_id INT,
    IN  p_subtotal    DECIMAL(12,2),
    OUT p_coupon_id   INT,
    OUT p_discount    DECIMAL(12,2)
)
BEGIN
    DECLARE v_id      INT;
    DECLARE v_type    VARCHAR(10);
    DECLARE v_value   DECIMAL(10,2);
    DECLARE v_min     DECIMAL(10,2);
    DECLARE v_max     DECIMAL(10,2);
    DECLARE v_active  BOOLEAN;
    DECLARE v_from    DATETIME;
    DECLARE v_to      DATETIME;
    DECLARE v_limit   INT;
    DECLARE v_per     INT;
    DECLARE v_used    INT DEFAULT 0;
    DECLARE v_used_by INT DEFAULT 0;
    DECLARE v_off     DECIMAL(12,2);
    DECLARE v_msg     VARCHAR(128);

    SET p_coupon_id = NULL;
    SET p_discount  = 0;

    SELECT coupon_id, discount_type, discount_value, min_order_amount, max_discount,
           is_active, valid_from, valid_to, usage_limit, per_customer_limit
    INTO   v_id, v_type, v_value, v_min, v_max, v_active, v_from, v_to, v_limit, v_per
    FROM   coupon
    WHERE  code = UPPER(TRIM(p_code))
    FOR UPDATE;

    IF v_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This coupon code is not valid';
    END IF;
    IF v_active = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This coupon is not active';
    END IF;
    IF v_from IS NOT NULL AND NOW() < v_from THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This coupon is not active yet';
    END IF;
    IF v_to IS NOT NULL AND NOW() > v_to THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This coupon has expired';
    END IF;
    IF p_subtotal < v_min THEN
        SET v_msg = CONCAT('Add items worth at least Rs ', v_min, ' to use this coupon');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
    END IF;

    SELECT COUNT(*) INTO v_used FROM coupon_redemption WHERE coupon_id = v_id;
    IF v_limit IS NOT NULL AND v_used >= v_limit THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This coupon has been fully used';
    END IF;

    SELECT COUNT(*) INTO v_used_by
    FROM   coupon_redemption WHERE coupon_id = v_id AND customer_id = p_customer_id;
    IF v_used_by >= v_per THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You have already used this coupon';
    END IF;

    IF v_type = 'percent' THEN
        SET v_off = ROUND(p_subtotal * v_value / 100, 2);
        IF v_max IS NOT NULL AND v_off > v_max THEN
            SET v_off = v_max;
        END IF;
    ELSE
        SET v_off = v_value;
    END IF;
    IF v_off > p_subtotal THEN
        SET v_off = p_subtotal;
    END IF;

    SET p_coupon_id = v_id;
    SET p_discount  = v_off;
END$$

-- =====================================================================
--  RETURNS AND REFUNDS
-- =====================================================================
-- A customer asks to return items of a DELIVERED order, inside the return window.
-- The refund is the item price minus its fair share of any coupon discount.
CREATE PROCEDURE sp_request_return(
    IN  p_customer_id  INT,
    IN  p_order_id     INT,
    IN  p_product_id   INT,
    IN  p_quantity     INT,
    IN  p_reason       VARCHAR(60),
    IN  p_details      VARCHAR(500),
    IN  p_window_days  INT,
    OUT p_return_id    INT
)
BEGIN
    DECLARE v_status    VARCHAR(20);
    DECLARE v_delivered DATETIME;
    DECLARE v_ordered   INT;
    DECLARE v_price     DECIMAL(10,2);
    DECLARE v_returned  INT DEFAULT 0;
    DECLARE v_sub       DECIMAL(12,2);
    DECLARE v_disc      DECIMAL(12,2);
    DECLARE v_amount    DECIMAL(12,2);
    DECLARE v_msg       VARCHAR(128);

    SELECT o.order_status, s.delivered_date
    INTO   v_status, v_delivered
    FROM   orders o
    JOIN   shipment s ON s.order_id = o.order_id
    WHERE  o.order_id = p_order_id AND o.customer_id = p_customer_id;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Order not found';
    END IF;
    IF v_status <> 'Delivered' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only delivered orders can be returned';
    END IF;
    IF v_delivered IS NULL OR v_delivered < DATE_SUB(NOW(), INTERVAL p_window_days DAY) THEN
        SET v_msg = CONCAT('The ', p_window_days, '-day return period for this order has ended');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
    END IF;

    SELECT quantity, unit_price INTO v_ordered, v_price
    FROM   order_item WHERE order_id = p_order_id AND product_id = p_product_id;
    IF v_ordered IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This item is not part of the order';
    END IF;

    SELECT COALESCE(SUM(quantity), 0) INTO v_returned
    FROM   return_request
    WHERE  order_id = p_order_id AND product_id = p_product_id AND status <> 'Rejected';

    IF p_quantity IS NULL OR p_quantity < 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be at least 1';
    END IF;
    IF p_quantity > v_ordered - v_returned THEN
        SET v_msg = CONCAT('You can return at most ', v_ordered - v_returned, ' more of this item');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
    END IF;

    -- fair share of the coupon discount
    SELECT subtotal, discount_amount INTO v_sub, v_disc FROM order_extra WHERE order_id = p_order_id;
    IF v_sub IS NULL OR v_sub = 0 THEN
        SET v_amount = v_price * p_quantity;
    ELSE
        SET v_amount = ROUND(v_price * p_quantity * (v_sub - v_disc) / v_sub, 2);
    END IF;

    INSERT INTO return_request (order_id, product_id, customer_id, quantity, reason, details, refund_amount)
    VALUES (p_order_id, p_product_id, p_customer_id, p_quantity, p_reason, NULLIF(TRIM(p_details), ''), v_amount);
    SET p_return_id = LAST_INSERT_ID();
END$$

-- The admin approves, rejects or refunds a return (the trigger does the rest)
CREATE PROCEDURE sp_update_return(
    IN p_return_id INT,
    IN p_action    VARCHAR(20),     -- approve / reject / refund
    IN p_note      VARCHAR(300),
    IN p_restock   BOOLEAN          -- only used by 'refund'
)
BEGIN
    DECLARE v_status VARCHAR(20);

    SELECT status INTO v_status FROM return_request WHERE return_id = p_return_id;
    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Return request not found';
    END IF;

    IF p_action = 'approve' THEN
        UPDATE return_request
        SET    status = 'Approved', admin_note = COALESCE(NULLIF(TRIM(p_note), ''), admin_note)
        WHERE  return_id = p_return_id;
    ELSEIF p_action = 'reject' THEN
        UPDATE return_request
        SET    status = 'Rejected', admin_note = COALESCE(NULLIF(TRIM(p_note), ''), admin_note)
        WHERE  return_id = p_return_id;
    ELSEIF p_action = 'refund' THEN
        UPDATE return_request
        SET    status = 'Refunded', restock = COALESCE(p_restock, 1),
               admin_note = COALESCE(NULLIF(TRIM(p_note), ''), admin_note)
        WHERE  return_id = p_return_id;
    ELSE
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown action';
    END IF;
END$$

-- =====================================================================
--  SELLER MODULE
-- =====================================================================

CREATE FUNCTION fn_seller_earnings(p_seller_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_total DECIMAL(12,2);
    SELECT COALESCE(SUM(total_cost), 0) INTO v_total
    FROM   purchase_order
    WHERE  seller_id = p_seller_id AND status = 'Received';
    RETURN v_total;
END$$

-- =====================================================================
--  PROCEDURES
-- =====================================================================

-- P1. Admin raises an OPEN request to all sellers.
--     Blocked if a seller already has stock (the admin must order from
--     that seller instead) or if an order is already in progress.
CREATE PROCEDURE sp_raise_restock_request(
    IN  p_product_id INT,
    IN  p_qty        INT,
    IN  p_max_price  DECIMAL(10,2),
    IN  p_note       VARCHAR(300),
    OUT p_po_id      INT
)
BEGIN
    DECLARE v_count INT;

    IF NOT EXISTS (SELECT 1 FROM product WHERE product_id = p_product_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Product not found';
    END IF;
    IF p_qty IS NULL OR p_qty <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be at least 1';
    END IF;

    SELECT COUNT(*) INTO v_count FROM purchase_order
    WHERE  product_id = p_product_id
      AND  status IN ('Open', 'Offered', 'Requested', 'Accepted', 'Shipped');
    IF v_count > 0 THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'A restock order is already in progress for this product';
    END IF;

    SELECT COUNT(*) INTO v_count
    FROM   seller_listing l JOIN seller s ON s.seller_id = l.seller_id
    WHERE  l.product_id = p_product_id AND l.is_active = 1 AND s.status = 'Approved'
      AND  l.quantity_available >= l.min_order_qty;
    IF v_count > 0 THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Sellers already have this product in stock. Order from a seller listing instead';
    END IF;

    INSERT INTO purchase_order (product_id, quantity_requested, max_unit_price, admin_note)
    VALUES (p_product_id, p_qty, p_max_price, NULLIF(TRIM(p_note), ''));
    SET p_po_id = LAST_INSERT_ID();
END$$

-- P2. Admin orders directly from one seller's listing
CREATE PROCEDURE sp_order_from_listing(
    IN  p_listing_id INT,
    IN  p_qty        INT,
    IN  p_note       VARCHAR(300),
    OUT p_po_id      INT
)
BEGIN
    DECLARE v_product INT;
    DECLARE v_count   INT;

    SELECT product_id INTO v_product FROM seller_listing WHERE listing_id = p_listing_id;
    IF v_product IS NULL THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'This listing is not linked to a store product';
    END IF;
    IF p_qty IS NULL OR p_qty <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Quantity must be at least 1';
    END IF;

    SELECT COUNT(*) INTO v_count FROM purchase_order
    WHERE  product_id = v_product
      AND  status IN ('Open', 'Offered', 'Requested', 'Accepted', 'Shipped');
    IF v_count > 0 THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'A restock order is already in progress for this product';
    END IF;

    -- trigger trg_po_before_insert checks stock, minimum quantity and seller status
    INSERT INTO purchase_order (product_id, listing_id, quantity_requested, admin_note)
    VALUES (v_product, p_listing_id, p_qty, NULLIF(TRIM(p_note), ''));
    SET p_po_id = LAST_INSERT_ID();
END$$

-- P3. A seller answers an OPEN request with an offer.
--     First seller to offer wins (the row is locked: concurrency control).
CREATE PROCEDURE sp_offer_purchase_order(
    IN p_po_id     INT,
    IN p_seller_id INT,
    IN p_qty       INT,
    IN p_price     DECIMAL(10,2),
    IN p_note      VARCHAR(300)
)
BEGIN
    DECLARE v_status    VARCHAR(20);
    DECLARE v_requested INT;
    DECLARE v_max       DECIMAL(10,2);
    DECLARE v_seller_st VARCHAR(20);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT status, quantity_requested, max_unit_price
    INTO   v_status, v_requested, v_max
    FROM   purchase_order WHERE po_id = p_po_id FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Request not found';
    END IF;
    IF v_status <> 'Open' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This request is no longer open';
    END IF;

    SELECT status INTO v_seller_st FROM seller WHERE seller_id = p_seller_id;
    IF v_seller_st IS NULL OR v_seller_st <> 'Approved' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Your seller account is not approved';
    END IF;
    IF p_qty IS NULL OR p_qty <= 0 OR p_qty > v_requested THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Offer quantity must be between 1 and the quantity requested';
    END IF;
    IF p_price IS NULL OR p_price <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Price must be greater than 0';
    END IF;
    IF v_max IS NOT NULL AND p_price > v_max THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Your price is above the maximum the store will pay';
    END IF;

    UPDATE purchase_order
    SET    seller_id = p_seller_id, quantity_offered = p_qty, unit_price = p_price,
           seller_note = NULLIF(TRIM(p_note), ''), status = 'Offered'
    WHERE  po_id = p_po_id;

    COMMIT;
END$$

-- P4. Seller actions on an order that belongs to them
--     accept  : Requested -> Accepted
--     decline : Requested -> Declined
--     ship    : Accepted  -> Shipped   (the seller has provided the goods)
CREATE PROCEDURE sp_seller_po_action(
    IN p_po_id     INT,
    IN p_seller_id INT,
    IN p_action    VARCHAR(10),
    IN p_note      VARCHAR(300)
)
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_seller INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT status, seller_id INTO v_status, v_seller
    FROM   purchase_order WHERE po_id = p_po_id FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Order not found';
    END IF;
    IF v_seller IS NULL OR v_seller <> p_seller_id THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This order does not belong to you';
    END IF;

    IF p_action = 'accept' THEN
        IF v_status <> 'Requested' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only new orders can be accepted';
        END IF;
        UPDATE purchase_order
        SET status = 'Accepted', seller_note = COALESCE(NULLIF(TRIM(p_note), ''), seller_note)
        WHERE po_id = p_po_id;
    ELSEIF p_action = 'decline' THEN
        IF v_status <> 'Requested' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only new orders can be declined';
        END IF;
        UPDATE purchase_order
        SET status = 'Declined', seller_note = COALESCE(NULLIF(TRIM(p_note), ''), seller_note)
        WHERE po_id = p_po_id;
    ELSEIF p_action = 'ship' THEN
        IF v_status <> 'Accepted' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only accepted orders can be marked as shipped';
        END IF;
        UPDATE purchase_order
        SET status = 'Shipped', seller_note = COALESCE(NULLIF(TRIM(p_note), ''), seller_note)
        WHERE po_id = p_po_id;
    ELSE
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown action';
    END IF;

    COMMIT;
END$$

-- P5. Admin actions
--     accept_offer : Offered -> Accepted
--     reject_offer : Offered -> Open again (the seller is removed)
--     cancel       : Open / Offered / Requested / Accepted -> Cancelled
--     receive      : Shipped -> Received  (trigger adds the stock)
CREATE PROCEDURE sp_admin_po_action(
    IN p_po_id  INT,
    IN p_action VARCHAR(15)
)
BEGIN
    DECLARE v_status VARCHAR(20);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT status INTO v_status FROM purchase_order WHERE po_id = p_po_id FOR UPDATE;
    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Order not found';
    END IF;

    IF p_action = 'accept_offer' THEN
        IF v_status <> 'Offered' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'There is no offer to accept';
        END IF;
        UPDATE purchase_order SET status = 'Accepted' WHERE po_id = p_po_id;
    ELSEIF p_action = 'reject_offer' THEN
        IF v_status <> 'Offered' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'There is no offer to reject';
        END IF;
        UPDATE purchase_order
        SET status = 'Open', seller_id = NULL, quantity_offered = NULL,
            unit_price = NULL, seller_note = NULL
        WHERE po_id = p_po_id;
    ELSEIF p_action = 'cancel' THEN
        UPDATE purchase_order SET status = 'Cancelled' WHERE po_id = p_po_id;
    ELSEIF p_action = 'receive' THEN
        IF v_status <> 'Shipped' THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Only shipped orders can be received';
        END IF;
        UPDATE purchase_order SET status = 'Received' WHERE po_id = p_po_id;
    ELSE
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown action';
    END IF;

    COMMIT;
END$$

-- P6. Admin adds a seller's NEW item to the store catalog (stock starts at 0)
CREATE PROCEDURE sp_add_listing_to_store(
    IN  p_listing_id   INT,
    IN  p_retail_price DECIMAL(10,2),
    OUT p_product_id   INT
)
BEGIN
    DECLARE v_product INT;
    DECLARE v_name    VARCHAR(100);
    DECLARE v_cat     INT;
    DECLARE v_desc    VARCHAR(500);
    DECLARE v_image   VARCHAR(500);
    DECLARE v_found   INT DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT 1, product_id, item_name, category_id, description, image_url
    INTO   v_found, v_product, v_name, v_cat, v_desc, v_image
    FROM   seller_listing WHERE listing_id = p_listing_id FOR UPDATE;

    IF v_found = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Listing not found';
    END IF;
    IF v_product IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This item is already in the store';
    END IF;

    -- trigger trg_product_after_insert creates the inventory row (0 items)
    INSERT INTO product (category_id, product_name, description, price, image_url)
    VALUES (v_cat, v_name, v_desc, p_retail_price, v_image);
    SET p_product_id = LAST_INSERT_ID();

    UPDATE seller_listing SET product_id = p_product_id WHERE listing_id = p_listing_id;

    COMMIT;
END$$

DELIMITER ;
