-- =====================================================================
--  GreenCart  |  04_views.sql  |  VIEWS  (saved SELECT queries)
--
--  The website reads most of its pages from these views.
--  Run AFTER 03_routines.sql (two views call functions). Safe to re-run.
-- =====================================================================

-- V1. Product catalog: product + category + stock + rating + units sold
CREATE OR REPLACE VIEW vw_product_catalog AS
SELECT  p.product_id,
        p.product_name,
        p.description,
        p.price,
        p.image_url,
        p.is_active,
        p.created_at,
        c.category_id,
        c.category_name,
        i.quantity_available,
        CASE
            WHEN i.quantity_available = 0                THEN 'Out of Stock'
            WHEN i.quantity_available <= i.reorder_level THEN 'Low Stock'
            ELSE 'In Stock'
        END AS stock_status,
        COALESCE(r.avg_rating, 0)   AS avg_rating,
        COALESCE(r.review_count, 0) AS review_count,
        COALESCE(s.units_sold, 0)   AS units_sold
FROM    product p
JOIN    category  c ON c.category_id = p.category_id
JOIN    inventory i ON i.product_id  = p.product_id
LEFT JOIN (SELECT product_id,
                  ROUND(AVG(rating), 2) AS avg_rating,
                  COUNT(*)              AS review_count
           FROM   review
           GROUP  BY product_id) r ON r.product_id = p.product_id
LEFT JOIN (SELECT oi.product_id,
                  SUM(oi.quantity) AS units_sold
           FROM   order_item oi
           JOIN   orders o ON o.order_id = oi.order_id
           WHERE  o.order_status <> 'Cancelled'
           GROUP  BY oi.product_id) s ON s.product_id = p.product_id;

-- V2. One row per order with customer, payment and shipment details
CREATE OR REPLACE VIEW vw_order_summary AS
SELECT  o.order_id,
        o.customer_id,
        c.full_name AS customer_name,
        c.city,
        o.order_date,
        o.total_amount,
        o.order_status,
        o.shipping_address,
        pay.payment_method,
        pay.payment_status,
        COALESCE(li.product_lines, 0) AS product_lines,
        COALESCE(li.total_units, 0)   AS total_units,
        s.tracking_number,
        s.expected_delivery,
        s.delivered_date
FROM    orders o
JOIN    customer c   ON c.customer_id = o.customer_id
LEFT JOIN payment pay ON pay.order_id = o.order_id
LEFT JOIN shipment s  ON s.order_id   = o.order_id
LEFT JOIN (SELECT order_id,
                  COUNT(*)      AS product_lines,
                  SUM(quantity) AS total_units
           FROM   order_item
           GROUP  BY order_id) li ON li.order_id = o.order_id;

-- V3. Items inside each order (for the "order details" page)
CREATE OR REPLACE VIEW vw_order_details AS
SELECT  oi.order_id,
        oi.product_id,
        p.product_name,
        cat.category_name,
        oi.quantity,
        oi.unit_price,
        oi.subtotal
FROM    order_item oi
JOIN    product p    ON p.product_id    = oi.product_id
JOIN    category cat ON cat.category_id = p.category_id;

-- V4. Sales by category (cancelled orders are not counted)
CREATE OR REPLACE VIEW vw_sales_by_category AS
SELECT  c.category_id,
        c.category_name,
        COALESCE(SUM(CASE WHEN o.order_id IS NOT NULL THEN oi.quantity ELSE 0 END), 0) AS units_sold,
        COALESCE(SUM(CASE WHEN o.order_id IS NOT NULL THEN oi.subtotal ELSE 0 END), 0) AS revenue
FROM    category c
LEFT JOIN product p     ON p.category_id = c.category_id
LEFT JOIN order_item oi ON oi.product_id = p.product_id
LEFT JOIN orders o      ON o.order_id    = oi.order_id
                       AND o.order_status <> 'Cancelled'
GROUP BY c.category_id, c.category_name;

-- V5. Best selling products (cancelled orders are not counted)
CREATE OR REPLACE VIEW vw_top_products AS
SELECT  p.product_id,
        p.product_name,
        cat.category_name,
        SUM(oi.quantity) AS units_sold,
        SUM(oi.subtotal) AS revenue
FROM    order_item oi
JOIN    orders o     ON o.order_id      = oi.order_id
JOIN    product p    ON p.product_id    = oi.product_id
JOIN    category cat ON cat.category_id = p.category_id
WHERE   o.order_status <> 'Cancelled'
GROUP BY p.product_id, p.product_name, cat.category_name;

-- V6. Customers with orders count, money spent and tier (uses a function)
CREATE OR REPLACE VIEW vw_top_customers AS
SELECT  c.customer_id,
        c.full_name,
        c.email,
        c.city,
        COUNT(o.order_id)                AS orders_count,
        COALESCE(SUM(o.total_amount), 0) AS total_spent,
        fn_customer_tier(c.customer_id)  AS tier
FROM    customer c
LEFT JOIN orders o ON o.customer_id  = c.customer_id
                  AND o.order_status <> 'Cancelled'
GROUP BY c.customer_id, c.full_name, c.email, c.city;

-- V7. Products that need restocking
CREATE OR REPLACE VIEW vw_low_stock AS
SELECT  p.product_id,
        p.product_name,
        cat.category_name,
        i.quantity_available,
        i.reorder_level,
        (i.reorder_level - i.quantity_available) AS shortfall
FROM    inventory i
JOIN    product p    ON p.product_id    = i.product_id
JOIN    category cat ON cat.category_id = p.category_id
WHERE   i.quantity_available <= i.reorder_level;

-- V8. Orders that are not delivered yet
CREATE OR REPLACE VIEW vw_pending_shipments AS
SELECT  o.order_id,
        c.full_name AS customer_name,
        c.city,
        o.order_status,
        o.order_date,
        DATEDIFF(CURDATE(), DATE(o.order_date)) AS days_since_order
FROM    orders o
JOIN    customer c ON c.customer_id = o.customer_id
WHERE   o.order_status IN ('Confirmed', 'Packed', 'Shipped');

-- V9. Revenue per month (cancelled orders are not counted)
CREATE OR REPLACE VIEW vw_monthly_sales AS
SELECT  DATE_FORMAT(order_date, '%Y-%m') AS sales_month,
        COUNT(*)                         AS orders_count,
        SUM(total_amount)                AS revenue
FROM    orders
WHERE   order_status <> 'Cancelled'
GROUP BY DATE_FORMAT(order_date, '%Y-%m');

-- V10. Reviews with product and customer names
CREATE OR REPLACE VIEW vw_review_details AS
SELECT  r.review_id,
        r.product_id,
        p.product_name,
        c.full_name AS customer_name,
        r.rating,
        r.comment,
        r.review_date
FROM    review r
JOIN    product p  ON p.product_id  = r.product_id
JOIN    customer c ON c.customer_id = r.customer_id;

-- V11. Customer data WITHOUT the password (for safe access, used in Step 6)
CREATE OR REPLACE VIEW vw_customer_safe AS
SELECT  customer_id, full_name, email, phone, city, created_at
FROM    customer;

-- V12. Updatable view with CHECK OPTION: only active products can be
--      inserted or updated through it
CREATE OR REPLACE VIEW vw_active_products AS
SELECT  product_id, category_id, product_name, price, is_active
FROM    product
WHERE   is_active = 1
WITH CHECK OPTION;

-- =====================================================================
--  SELLER VIEWS
-- =====================================================================

-- S1. Every product with its stock and what sellers can supply
CREATE OR REPLACE VIEW vw_product_supply AS
SELECT  p.product_id,
        p.product_name,
        p.is_active,
        p.image_url,
        c.category_name,
        i.quantity_available,
        i.reorder_level,
        CASE
            WHEN i.quantity_available = 0                THEN 'Out of Stock'
            WHEN i.quantity_available <= i.reorder_level THEN 'Low Stock'
            ELSE 'In Stock'
        END AS stock_status,
        COALESCE(s.offers, 0) AS seller_offers,
        COALESCE(s.supply, 0) AS seller_supply,
        s.best_price,
        po.po_id   AS active_po_id,
        po.status  AS active_po_status
FROM    product p
JOIN    category  c ON c.category_id = p.category_id
JOIN    inventory i ON i.product_id  = p.product_id
LEFT JOIN (SELECT l.product_id,
                  COUNT(*)              AS offers,
                  SUM(l.quantity_available) AS supply,
                  MIN(l.unit_price)     AS best_price
           FROM   seller_listing l
           JOIN   seller sl ON sl.seller_id = l.seller_id
           WHERE  l.product_id IS NOT NULL AND l.is_active = 1
             AND  sl.status = 'Approved' AND l.quantity_available >= l.min_order_qty
           GROUP  BY l.product_id) s ON s.product_id = p.product_id
LEFT JOIN (SELECT product_id, po_id, status
           FROM   purchase_order
           WHERE  status IN ('Open', 'Offered', 'Requested', 'Accepted', 'Shipped')) po
       ON po.product_id = p.product_id;

-- S2. Purchase orders with product and seller names
CREATE OR REPLACE VIEW vw_purchase_orders AS
SELECT  po.po_id,
        po.product_id,
        p.product_name,
        p.image_url       AS product_image,
        c.category_name,
        i.quantity_available AS stock_now,
        po.seller_id,
        s.business_name   AS seller_name,
        po.listing_id,
        po.quantity_requested,
        po.quantity_offered,
        po.unit_price,
        po.max_unit_price,
        po.total_cost,
        po.status,
        po.admin_note,
        po.seller_note,
        po.created_at,
        po.updated_at,
        po.shipped_at,
        po.received_at
FROM    purchase_order po
JOIN    product p   ON p.product_id  = po.product_id
JOIN    category c  ON c.category_id = p.category_id
JOIN    inventory i ON i.product_id  = po.product_id
LEFT JOIN seller s  ON s.seller_id   = po.seller_id;

-- S3. Seller listings with seller, category and linked product
CREATE OR REPLACE VIEW vw_seller_listings AS
SELECT  l.listing_id,
        l.seller_id,
        s.business_name,
        s.status          AS seller_status,
        l.product_id,
        p.product_name    AS store_product,
        l.item_name,
        l.category_id,
        c.category_name,
        l.description,
        l.image_url,
        l.unit_price,
        l.quantity_available,
        l.min_order_qty,
        l.is_active,
        l.created_at
FROM    seller_listing l
JOIN    seller s   ON s.seller_id   = l.seller_id
JOIN    category c ON c.category_id = l.category_id
LEFT JOIN product p ON p.product_id = l.product_id;

-- S4. Seller summary for the admin
CREATE OR REPLACE VIEW vw_seller_summary AS
SELECT  s.seller_id,
        s.business_name,
        s.contact_name,
        s.email,
        s.phone,
        s.city,
        s.gst_number,
        s.status,
        s.created_at,
        (SELECT COUNT(*) FROM seller_listing l WHERE l.seller_id = s.seller_id) AS listings,
        (SELECT COUNT(*) FROM purchase_order o WHERE o.seller_id = s.seller_id) AS orders_count,
        fn_seller_earnings(s.seller_id) AS earned
FROM    seller s;

-- =====================================================================
--  RETURNS
-- =====================================================================
CREATE OR REPLACE VIEW vw_returns AS
SELECT r.return_id, r.order_id, r.product_id, p.product_name, p.image_url,
       r.customer_id, c.full_name AS customer_name, c.email AS customer_email,
       r.quantity, r.reason, r.details, r.refund_amount, r.status, r.restock,
       r.admin_note, r.created_at, r.updated_at
FROM   return_request r
JOIN   product  p ON p.product_id  = r.product_id
JOIN   customer c ON c.customer_id = r.customer_id;
