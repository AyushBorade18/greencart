-- =====================================================================
--  GreenCart  |  01_schema.sql  |  TABLES
--
--  Run by:  python scripts/setup_db.py        (or paste into Workbench)
--  Needs :  MySQL 8.0.16+ (CHECK constraints are enforced from that
--           version; the triggers in 02_triggers.sql repeat the most
--           important checks, so older versions are protected too).
--
--  This file creates structure only. NO demo data is inserted anywhere.
--  It is safe to run again: existing tables are left untouched.
--
--  How MySQL and Supabase fit together
--    * customer.auth_id / seller.auth_id hold the Supabase user id.
--      Supabase proves WHO the person is (email + password + confirmed
--      email). MySQL stores everything ELSE about them and enforces
--      every business rule with triggers and stored procedures.
--    * product.image_url / seller_listing.image_url hold the public link
--      of a picture stored in Supabase Storage.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. ADMIN  (created by scripts/setup_db.py, never by SQL)
--    password_hash holds a salted hash made by the website, not plain SHA.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin (
    admin_id       INT AUTO_INCREMENT PRIMARY KEY,
    username       VARCHAR(50)  NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 2. CUSTOMER
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer (
    customer_id    INT AUTO_INCREMENT PRIMARY KEY,
    auth_id        CHAR(36)     NULL UNIQUE,            -- Supabase user id
    full_name      VARCHAR(100) NOT NULL,
    email          VARCHAR(100) NOT NULL UNIQUE,
    phone          CHAR(10)     NOT NULL,
    address        VARCHAR(255) NOT NULL,
    city           VARCHAR(50)  NOT NULL,
    pincode        CHAR(6)      NOT NULL,
    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer_city (city),
    CONSTRAINT chk_customer_email   CHECK (email LIKE '%_@_%._%'),
    CONSTRAINT chk_customer_phone   CHECK (phone REGEXP '^[0-9]{10}$'),
    CONSTRAINT chk_customer_pincode CHECK (pincode REGEXP '^[0-9]{6}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 3. CATEGORY  (added by the admin)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category (
    category_id    INT AUTO_INCREMENT PRIMARY KEY,
    category_name  VARCHAR(50) NOT NULL UNIQUE,
    description    VARCHAR(255)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 4. PRODUCT
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product (
    product_id     INT AUTO_INCREMENT PRIMARY KEY,
    category_id    INT NOT NULL,
    product_name   VARCHAR(100)  NOT NULL,
    description    VARCHAR(500),
    image_url      VARCHAR(500)  NULL,
    price          DECIMAL(10,2) NOT NULL,
    is_active      BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at     DATETIME      DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_product_name           (product_name),
    INDEX idx_product_category_price (category_id, price),
    CONSTRAINT chk_product_price CHECK (price > 0),
    CONSTRAINT fk_product_category FOREIGN KEY (category_id)
        REFERENCES category(category_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 5. INVENTORY  (one row of stock per product; created by a trigger)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id        INT AUTO_INCREMENT PRIMARY KEY,
    product_id          INT NOT NULL UNIQUE,
    quantity_available  INT NOT NULL DEFAULT 0,
    reorder_level       INT NOT NULL DEFAULT 5,
    last_updated        DATETIME DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT chk_inventory_qty     CHECK (quantity_available >= 0),
    CONSTRAINT chk_inventory_reorder CHECK (reorder_level >= 0),
    CONSTRAINT fk_inventory_product FOREIGN KEY (product_id)
        REFERENCES product(product_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 6. CART (one per customer, created by a trigger)  +  7. CART_ITEM
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cart (
    cart_id        INT AUTO_INCREMENT PRIMARY KEY,
    customer_id    INT NOT NULL UNIQUE,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_cart_customer FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cart_item (
    cart_item_id   INT AUTO_INCREMENT PRIMARY KEY,
    cart_id        INT NOT NULL,
    product_id     INT NOT NULL,
    quantity       INT NOT NULL DEFAULT 1,
    added_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_cart_item_qty CHECK (quantity > 0),
    CONSTRAINT uq_cart_product UNIQUE (cart_id, product_id),
    CONSTRAINT fk_cart_item_cart FOREIGN KEY (cart_id)
        REFERENCES cart(cart_id) ON DELETE CASCADE,
    CONSTRAINT fk_cart_item_product FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 8. ORDERS  (named "orders" because ORDER is a reserved word)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    order_id          INT AUTO_INCREMENT PRIMARY KEY,
    customer_id       INT NOT NULL,
    order_date        DATETIME      DEFAULT CURRENT_TIMESTAMP,
    total_amount      DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_status      ENUM('Confirmed','Packed','Shipped','Delivered','Cancelled')
                      NOT NULL DEFAULT 'Confirmed',
    shipping_address  VARCHAR(300)  NOT NULL,
    INDEX idx_orders_customer_date (customer_id, order_date),
    INDEX idx_orders_status        (order_status),
    INDEX idx_orders_date          (order_date),
    CONSTRAINT chk_orders_total CHECK (total_amount >= 0),
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id)
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 9. ORDER_ITEM  (subtotal is a generated column = quantity x unit_price)
--    unit_price is always overwritten by a trigger with the REAL price.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_item (
    order_item_id  INT AUTO_INCREMENT PRIMARY KEY,
    order_id       INT NOT NULL,
    product_id     INT NOT NULL,
    quantity       INT NOT NULL,
    unit_price     DECIMAL(10,2) NOT NULL,
    subtotal       DECIMAL(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    CONSTRAINT chk_order_item_qty   CHECK (quantity > 0),
    CONSTRAINT chk_order_item_price CHECK (unit_price >= 0),
    CONSTRAINT uq_order_product UNIQUE (order_id, product_id),
    CONSTRAINT fk_order_item_order FOREIGN KEY (order_id)
        REFERENCES orders(order_id) ON DELETE CASCADE,
    CONSTRAINT fk_order_item_product FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 10. PAYMENT  (one payment per order)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment (
    payment_id       INT AUTO_INCREMENT PRIMARY KEY,
    order_id         INT NOT NULL UNIQUE,
    payment_method   ENUM('UPI','Credit Card','Debit Card','Net Banking','Cash on Delivery') NOT NULL,
    amount           DECIMAL(12,2) NOT NULL,
    payment_status   ENUM('Pending','Paid','Failed','Refunded') NOT NULL DEFAULT 'Pending',
    payment_date     DATETIME DEFAULT CURRENT_TIMESTAMP,
    transaction_ref  VARCHAR(40),
    INDEX idx_payment_status (payment_status),
    CONSTRAINT chk_payment_amount CHECK (amount >= 0),
    CONSTRAINT fk_payment_order FOREIGN KEY (order_id)
        REFERENCES orders(order_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 11. SHIPMENT  (one per order, created by a trigger)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shipment (
    shipment_id        INT AUTO_INCREMENT PRIMARY KEY,
    order_id           INT NOT NULL UNIQUE,
    courier_name       VARCHAR(50) NOT NULL DEFAULT 'BlueDart',
    tracking_number    VARCHAR(30),
    shipped_date       DATETIME NULL,
    expected_delivery  DATE NULL,
    delivered_date     DATETIME NULL,
    CONSTRAINT fk_shipment_order FOREIGN KEY (order_id)
        REFERENCES orders(order_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 12. REVIEW  (one review per customer per product, only after delivery)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review (
    review_id      INT AUTO_INCREMENT PRIMARY KEY,
    product_id     INT NOT NULL,
    customer_id    INT NOT NULL,
    rating         TINYINT NOT NULL,
    comment        VARCHAR(500),
    review_date    DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_review_rating CHECK (rating BETWEEN 1 AND 5),
    CONSTRAINT uq_review UNIQUE (customer_id, product_id),
    CONSTRAINT fk_review_product FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE CASCADE,
    CONSTRAINT fk_review_customer FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- 13-15. LOG TABLES (filled automatically by triggers)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_status_log (
    log_id       INT AUTO_INCREMENT PRIMARY KEY,
    order_id     INT NOT NULL,
    old_status   VARCHAR(20),
    new_status   VARCHAR(20) NOT NULL,
    changed_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_log_order FOREIGN KEY (order_id)
        REFERENCES orders(order_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS price_history (
    history_id   INT AUTO_INCREMENT PRIMARY KEY,
    product_id   INT NOT NULL,
    old_price    DECIMAL(10,2) NOT NULL,
    new_price    DECIMAL(10,2) NOT NULL,
    changed_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_price_history_product FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS low_stock_alert (
    alert_id       INT AUTO_INCREMENT PRIMARY KEY,
    product_id     INT NOT NULL,
    quantity_left  INT NOT NULL,
    alert_time     DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_alert_product FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =====================================================================
--  SELLER MODULE
--  Stock rule (enforced by triggers): store stock only goes UP when a
--  purchase order from a seller is RECEIVED.
-- =====================================================================

-- 16. SELLER  (wholesale sellers; the admin approves them)
CREATE TABLE IF NOT EXISTS seller (
    seller_id      INT AUTO_INCREMENT PRIMARY KEY,
    auth_id        CHAR(36)     NULL UNIQUE,            -- Supabase user id
    business_name  VARCHAR(120) NOT NULL,
    contact_name   VARCHAR(100) NOT NULL,
    email          VARCHAR(100) NOT NULL UNIQUE,
    phone          CHAR(10)     NOT NULL,
    address        VARCHAR(255) NOT NULL,
    city           VARCHAR(50)  NOT NULL,
    pincode        CHAR(6)      NOT NULL,
    gst_number     VARCHAR(20)  NULL,
    status         ENUM('Pending','Approved','Suspended') NOT NULL DEFAULT 'Pending',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_seller_email   CHECK (email LIKE '%_@_%._%'),
    CONSTRAINT chk_seller_phone   CHECK (phone REGEXP '^[0-9]{10}$'),
    CONSTRAINT chk_seller_pincode CHECK (pincode REGEXP '^[0-9]{6}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 17. SELLER_LISTING  (what a seller offers wholesale)
--     product_id = the store product it supplies
--     NULL       = a NEW item the store does not sell yet; the admin
--                  can add it to the store with one click
CREATE TABLE IF NOT EXISTS seller_listing (
    listing_id          INT AUTO_INCREMENT PRIMARY KEY,
    seller_id           INT NOT NULL,
    product_id          INT NULL,
    item_name           VARCHAR(100)  NOT NULL,
    category_id         INT NOT NULL,
    description         VARCHAR(500),
    image_url           VARCHAR(500),
    unit_price          DECIMAL(10,2) NOT NULL,
    quantity_available  INT NOT NULL DEFAULT 0,
    min_order_qty       INT NOT NULL DEFAULT 1,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_listing_product (product_id),
    INDEX idx_listing_seller  (seller_id),
    CONSTRAINT chk_listing_price CHECK (unit_price > 0),
    CONSTRAINT chk_listing_qty   CHECK (quantity_available >= 0),
    CONSTRAINT chk_listing_min   CHECK (min_order_qty >= 1),
    CONSTRAINT fk_listing_seller   FOREIGN KEY (seller_id)   REFERENCES seller(seller_id)     ON DELETE CASCADE,
    CONSTRAINT fk_listing_product  FOREIGN KEY (product_id)  REFERENCES product(product_id)   ON DELETE SET NULL,
    CONSTRAINT fk_listing_category FOREIGN KEY (category_id) REFERENCES category(category_id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 18. PURCHASE_ORDER  (the store buys stock from a seller)
--    Open       admin asked all sellers (no seller yet)
--    Offered    a seller made an offer (price + quantity)
--    Requested  admin ordered directly from a seller's listing
--    Accepted   agreed by both sides
--    Shipped    seller has provided the goods
--    Received   admin received them -> inventory goes up (trigger)
--    Declined   seller said no  /  Cancelled  admin cancelled
CREATE TABLE IF NOT EXISTS purchase_order (
    po_id               INT AUTO_INCREMENT PRIMARY KEY,
    product_id          INT NOT NULL,
    seller_id           INT NULL,
    listing_id          INT NULL,
    quantity_requested  INT NOT NULL,
    quantity_offered    INT NULL,
    unit_price          DECIMAL(10,2) NULL,
    max_unit_price      DECIMAL(10,2) NULL,
    total_cost          DECIMAL(12,2) GENERATED ALWAYS AS (quantity_offered * unit_price) STORED,
    status              ENUM('Open','Offered','Requested','Accepted','Shipped','Received','Declined','Cancelled')
                        NOT NULL DEFAULT 'Open',
    admin_note          VARCHAR(300),
    seller_note         VARCHAR(300),
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    shipped_at          DATETIME NULL,
    received_at         DATETIME NULL,
    INDEX idx_po_status  (status),
    INDEX idx_po_seller  (seller_id),
    INDEX idx_po_product (product_id),
    CONSTRAINT chk_po_requested CHECK (quantity_requested > 0),
    CONSTRAINT chk_po_offered   CHECK (quantity_offered IS NULL OR quantity_offered > 0),
    CONSTRAINT chk_po_price     CHECK (unit_price IS NULL OR unit_price > 0),
    CONSTRAINT chk_po_max_price CHECK (max_unit_price IS NULL OR max_unit_price > 0),
    CONSTRAINT fk_po_product FOREIGN KEY (product_id) REFERENCES product(product_id) ON DELETE RESTRICT,
    CONSTRAINT fk_po_seller  FOREIGN KEY (seller_id)  REFERENCES seller(seller_id)   ON DELETE RESTRICT,
    CONSTRAINT fk_po_listing FOREIGN KEY (listing_id) REFERENCES seller_listing(listing_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS purchase_order_log (
    log_id      INT AUTO_INCREMENT PRIMARY KEY,
    po_id       INT NOT NULL,
    old_status  VARCHAR(20),
    new_status  VARCHAR(20) NOT NULL,
    changed_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_po_log FOREIGN KEY (po_id) REFERENCES purchase_order(po_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =====================================================================
--  SHOPPING FEATURES  (wishlist, address book, coupons, returns, pictures)
-- =====================================================================

-- 19. WISHLIST  (a customer's saved products)
CREATE TABLE IF NOT EXISTS wishlist (
    customer_id  INT NOT NULL,
    product_id   INT NOT NULL,
    added_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (customer_id, product_id),
    CONSTRAINT fk_wishlist_customer FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id) ON DELETE CASCADE,
    CONSTRAINT fk_wishlist_product FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 20. ADDRESS  (address book: a customer can keep several delivery addresses)
CREATE TABLE IF NOT EXISTS address (
    address_id   INT AUTO_INCREMENT PRIMARY KEY,
    customer_id  INT NOT NULL,
    label        VARCHAR(30)  NOT NULL DEFAULT 'Home',
    full_name    VARCHAR(100) NOT NULL,
    phone        CHAR(10)     NOT NULL,
    line1        VARCHAR(200) NOT NULL,
    city         VARCHAR(50)  NOT NULL,
    pincode      CHAR(6)      NOT NULL,
    is_default   BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_address_customer (customer_id),
    CONSTRAINT chk_address_phone   CHECK (phone REGEXP '^[0-9]{10}$'),
    CONSTRAINT chk_address_pincode CHECK (pincode REGEXP '^[0-9]{6}$'),
    CONSTRAINT fk_address_customer FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 21. COUPON  (created by the admin)
--     percent : discount_value is a percentage (max_discount caps it)
--     flat    : discount_value is a rupee amount
CREATE TABLE IF NOT EXISTS coupon (
    coupon_id           INT AUTO_INCREMENT PRIMARY KEY,
    code                VARCHAR(30)  NOT NULL UNIQUE,
    description         VARCHAR(200),
    discount_type       ENUM('percent','flat') NOT NULL,
    discount_value      DECIMAL(10,2) NOT NULL,
    min_order_amount    DECIMAL(10,2) NOT NULL DEFAULT 0,
    max_discount        DECIMAL(10,2) NULL,
    usage_limit         INT NULL,                     -- NULL = unlimited uses in total
    per_customer_limit  INT NOT NULL DEFAULT 1,
    valid_from          DATETIME NULL,
    valid_to            DATETIME NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_coupon_value   CHECK (discount_value > 0),
    CONSTRAINT chk_coupon_percent CHECK (discount_type <> 'percent' OR discount_value <= 100),
    CONSTRAINT chk_coupon_min     CHECK (min_order_amount >= 0),
    CONSTRAINT chk_coupon_max     CHECK (max_discount IS NULL OR max_discount > 0),
    CONSTRAINT chk_coupon_limit   CHECK (usage_limit IS NULL OR usage_limit > 0),
    CONSTRAINT chk_coupon_per     CHECK (per_customer_limit >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 22. ORDER_EXTRA  (what was added to / taken off an order: coupon, delivery fee)
CREATE TABLE IF NOT EXISTS order_extra (
    order_id         INT PRIMARY KEY,
    subtotal         DECIMAL(12,2) NOT NULL,          -- items before discount and delivery
    coupon_id        INT NULL,
    coupon_code      VARCHAR(30) NULL,
    discount_amount  DECIMAL(12,2) NOT NULL DEFAULT 0,
    delivery_fee     DECIMAL(8,2)  NOT NULL DEFAULT 0,
    CONSTRAINT chk_extra_discount CHECK (discount_amount >= 0),
    CONSTRAINT chk_extra_fee      CHECK (delivery_fee >= 0),
    CONSTRAINT fk_extra_order  FOREIGN KEY (order_id)  REFERENCES orders(order_id) ON DELETE CASCADE,
    CONSTRAINT fk_extra_coupon FOREIGN KEY (coupon_id) REFERENCES coupon(coupon_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 23. COUPON_REDEMPTION  (who used which coupon on which order)
CREATE TABLE IF NOT EXISTS coupon_redemption (
    redemption_id    INT AUTO_INCREMENT PRIMARY KEY,
    coupon_id        INT NOT NULL,
    customer_id      INT NOT NULL,
    order_id         INT NOT NULL UNIQUE,
    discount_amount  DECIMAL(12,2) NOT NULL,
    redeemed_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_redemption_coupon (coupon_id, customer_id),
    CONSTRAINT fk_redemption_coupon   FOREIGN KEY (coupon_id)   REFERENCES coupon(coupon_id)     ON DELETE RESTRICT,
    CONSTRAINT fk_redemption_customer FOREIGN KEY (customer_id) REFERENCES customer(customer_id) ON DELETE CASCADE,
    CONSTRAINT fk_redemption_order    FOREIGN KEY (order_id)    REFERENCES orders(order_id)      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 24. RETURN_REQUEST  (return + refund of items from a DELIVERED order)
--     Requested -> Approved -> Refunded      or      Requested/Approved -> Rejected
CREATE TABLE IF NOT EXISTS return_request (
    return_id      INT AUTO_INCREMENT PRIMARY KEY,
    order_id       INT NOT NULL,
    product_id     INT NOT NULL,
    customer_id    INT NOT NULL,
    quantity       INT NOT NULL,
    reason         VARCHAR(60)  NOT NULL,
    details        VARCHAR(500),
    refund_amount  DECIMAL(12,2) NOT NULL,
    status         ENUM('Requested','Approved','Rejected','Refunded') NOT NULL DEFAULT 'Requested',
    restock        BOOLEAN NOT NULL DEFAULT TRUE,     -- put the item back into stock when refunded
    admin_note     VARCHAR(300),
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_return_status   (status),
    INDEX idx_return_customer (customer_id),
    INDEX idx_return_order    (order_id, product_id),
    CONSTRAINT chk_return_qty    CHECK (quantity > 0),
    CONSTRAINT chk_return_refund CHECK (refund_amount >= 0),
    CONSTRAINT fk_return_item FOREIGN KEY (order_id, product_id)
        REFERENCES order_item(order_id, product_id) ON DELETE CASCADE,
    CONSTRAINT fk_return_customer FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 25. PRODUCT_IMAGE  (several pictures per product; product.image_url stays the cover picture)
CREATE TABLE IF NOT EXISTS product_image (
    image_id    INT AUTO_INCREMENT PRIMARY KEY,
    product_id  INT NOT NULL,
    image_url   VARCHAR(500) NOT NULL,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_product_image (product_id, sort_order),
    CONSTRAINT fk_product_image FOREIGN KEY (product_id)
        REFERENCES product(product_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
