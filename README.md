# GreenCart

E-commerce order processing system: **Flask** website, **MySQL** database (all business rules live in
triggers and stored procedures), **Supabase** for login and product pictures, deployable on **Vercel**.
Installable on phones as an app (PWA). The database starts completely **empty** — there is no demo data.

---

## Features

| | Customer | Seller | Admin |
|---|---|---|---|
| **Account** | profile, **address book** (several addresses), wishlist, order history | profile, approval status | secure login |
| **Shopping** | search, filters (category, price, **rating**, stock), sort, **recently viewed**, **wishlist hearts**, product **gallery** (several pictures), reviews, **Buy now** | list items with picture, price, quantity | products with several pictures, categories, inventory |
| **Checkout** | cart saves by itself, **coupon codes**, **delivery fee** (free above a limit), saved address, 5 payment methods | | **coupon manager** (percent / flat, minimum order, cap, usage limits, dates) |
| **Orders** | **live tracking bar** (Confirmed > Packed > Shipped > Delivered), tracking number, **printable invoice**, cancel before shipping | accept / ship restock orders | status changes with **courier + tracking number**, **invoice** |
| **After sale** | **return and refund requests** (7-day window, fair share of coupon discount) | | approve / reject / refund returns (restock or not) |
| **Emails** | confirmed, packed, shipped, delivered, cancelled, return updates | | |
| **Money** | | **earnings** page (by month, in progress) | reports, low-stock alerts |
| **App** | installable on phones (PWA), responsive | | grouped menu: Sales / Catalog / Suppliers |

Rules that live in MySQL (triggers/procedures, so nothing can bypass them): stock, prices above zero, order status steps, coupon limits,
return window and quantities, refunds restocking items, payment = order total.

---

## 1. How MySQL, Supabase and Vercel fit together

```
 Browser / phone app (PWA)
          │
          ▼
 ┌──────────────────────────┐        ┌──────────────────────────────┐
 │  Flask website (Vercel)  │──────► │ SUPABASE                     │
 │  shop/…                  │        │  Auth     who you are        │
 │                          │        │           (email, password,  │
 │  checks input, calls the │        │            confirmed email)  │
 │  database, shows pages   │        │  Storage  product pictures   │
 └────────────┬─────────────┘        └──────────────────────────────┘
              │ SQL / stored procedures
              ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ MySQL   tables · TRIGGERS · stored procedures · views            │
 │         every business rule: stock, prices, order status, refunds │
 └──────────────────────────────────────────────────────────────────┘
```

| Question | Answer |
|---|---|
| **Who proves a person's identity?** | Supabase Auth (password + confirmation email). |
| **Where is the person's data kept?** | MySQL (`customer`, `seller`). The Supabase user id is saved in `auth_id`, so the two are linked. No password is ever stored in MySQL. |
| **When is the MySQL row created?** | The first time a *confirmed* person logs in (`shop/routes/accounts.py`). A MySQL trigger then creates their cart automatically. |
| **Where do pictures live?** | Supabase Storage bucket `product-images` (created automatically on the first upload). MySQL stores only the picture's web address. |
| **Where are the business rules?** | MySQL triggers/procedures (`database/02_triggers.sql`, `03_routines.sql`). The website repeats the checks only to show friendly messages. |

**Why not Supabase SQL triggers?** Supabase's database is PostgreSQL. A trigger can only act on tables in its
*own* database, so a Supabase trigger cannot change your MySQL stock or orders. Using both would mean two
databases to keep in sync. So: **MySQL holds all shop data and triggers; Supabase only does login and
pictures.** (If you ever want a Supabase-side trigger, e.g. to log sign-ups, it would only see Supabase data.)

---

## 2. Run it on your computer

You need: Python 3.10+, MySQL 8.0.16+ (or MariaDB 10.5+), a free Supabase project.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows        (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # Mac/Linux: cp .env.example .env     then edit .env
```

**MySQL** — create an empty database once (MySQL Workbench or command line):
```sql
CREATE DATABASE greencart CHARACTER SET utf8mb4;
```

**Supabase** (dashboard → your project):
1. *Project Settings → API*: copy the **Project URL** (`SUPABASE_URL`), the **publishable / anon key**
   (`SUPABASE_PUBLISHABLE_KEY`) and the **secret / service_role key** (`SUPABASE_SECRET_KEY`).
   The secret key is only for the server — never put it in a web page or on GitHub.
2. *Authentication → Sign In / Providers → Email*: keep **Confirm email** ON.
3. *Authentication → URL Configuration*: set **Site URL** to your `SITE_URL`
   (`http://127.0.0.1:5000` now, your Vercel address later) and add it under **Redirect URLs**.
4. Supabase's built-in mail sender allows only a few emails per hour. While testing, either set
   `REQUIRE_EMAIL_CONFIRMATION=false` **and** switch *Confirm email* OFF in Supabase, or add your own SMTP
   (*Authentication → Emails → SMTP*). Switch both back before real use.

**Build the database and the admin login, then check it, then start:**
```bash
python scripts/setup_db.py --admin          # tables, triggers, procedures, views + admin (ADMIN_USERNAME / ADMIN_PASSWORD from .env)
python scripts/selftest_db.py               # run ONCE on the fresh database: tests every rule, then wipes its test data
python app.py                               # http://127.0.0.1:5000
```
`selftest_db.py` refuses to run if the database already contains shop data. To start over:
`python scripts/setup_db.py --reset --yes --admin`.

Website tests (no database needed): `python -W ignore -m unittest discover -s tests -v`

---

## 3. Feeding the data (nothing is pre-loaded)

1. **Admin** → `/admin/login` → *Categories*: add your categories.
2. **Admin** → *Products* → *Add product*: price, opening stock, reorder level, picture link (optional).
3. **Sellers** register at `/seller/register`, confirm their email, log in. **Admin → Sellers → Approve.**
4. An approved **seller** lists items (wholesale price, quantity, picture upload).
5. **Admin → Seller items**: a *new* item shows a form: enter the selling price (and optionally an order
   quantity) and click **Add to store**. The product now appears in the shop (sold out until stock arrives).
6. The seller **accepts** and **ships** the order; the admin clicks **Receive** → store stock goes up
   (trigger `trg_po_after_update`). This is the only way stock increases after a product is created.
7. **Customers** register at `/register`, shop, check out. The admin moves orders
   Confirmed → Packed → Shipped → Delivered (typing the courier and tracking number when shipping); triggers fill in the
   rest, mark cash-on-delivery paid, restore stock and refund on cancellation.
8. **Coupons:** Admin → Sales → Coupons → create `WELCOME10` (10 %, minimum 499, max 100). Customers type it in the cart.
9. **Returns:** after delivery a customer opens the order → *Return this item*. Admin → Sales → Returns & refunds →
   Approve → Refund. The item goes back into stock (untick if it is damaged) and the customer is emailed.

---

## 3b. Order emails (optional)

Emails are sent by the website (MySQL cannot send email). Set these in `.env` (and later in Vercel):
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-16-letter-app-password     (Gmail: Google Account > Security > App passwords)
MAIL_FROM=you@gmail.com
```
Leave `SMTP_HOST` empty to switch emails off. A failed email never stops an order. Brevo, Resend, Mailgun and any other SMTP service work the same way.

Shop rules (all optional, defaults shown): `DELIVERY_FEE=40`, `FREE_DELIVERY_ABOVE=499`, `RETURN_WINDOW_DAYS=7`, `STORE_NAME=GreenCart`.

---

## 4. Deploy on Vercel

Vercel cannot run MySQL, so you need a **cloud MySQL** database (e.g. Aiven, TiDB Cloud, Railway, or any host
that gives you host / port / user / password). Free tiers change, so check current limits. Choose a region
close to your Vercel region — every query crosses that distance.

1. Create the cloud database, then from your computer set the `DB_*` values in `.env` to the cloud
   values (use `DB_SSL=true`) and run `python scripts/setup_db.py --admin` once. The host user must be allowed
   to create triggers and functions (if you see error 1419, ask the host to enable
   `log_bin_trust_function_creators` or use the admin user).
2. Push this folder to GitHub → Vercel → *Add New Project* → import it (framework: *Flask* / Other).
3. *Settings → Environment Variables*: add every name from `.env.example`:
   `SECRET_KEY` (required — the site refuses to start without it), `DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME DB_SSL`,
   `SUPABASE_URL SUPABASE_PUBLISHABLE_KEY SUPABASE_SECRET_KEY SUPABASE_BUCKET`, the optional `SMTP_*` / `MAIL_FROM` / `DELIVERY_FEE` / `FREE_DELIVERY_ABOVE` / `RETURN_WINDOW_DAYS`, `SITE_URL`
   (your `https://….vercel.app` address), `REQUIRE_EMAIL_CONFIRMATION=true`.
4. Redeploy. Open `https://your-app.vercel.app/health` — it must show `{"database": true, "ok": true}`.
5. Put the Vercel address into Supabase → *Authentication → URL Configuration* (Site URL + Redirect URLs).
6. On a phone open the site in Chrome/Safari → *Add to Home Screen* / *Install app*.

Vercel notes: files uploaded by users cannot be saved on Vercel's disk, which is why pictures go to Supabase
Storage. Request bodies are limited to about 4.5 MB (pictures are limited to 2 MB).

---

## 5. What was fixed compared with the old version

| Problem | Cause | Fix |
|---|---|---|
| Picture not uploading | `SUPABASE_SECRET_KEY` missing; bucket may not exist | Clear message naming the missing setting; bucket is created on first upload; pictures are uploaded only after the rest of the form is valid |
| Price stays ₹1,999 when quantity changes | The +/− buttons only changed the box; nothing was saved until "Update" was clicked | The cart saves by itself half a second after a change; stepper stops at stock and never goes below 1 |
| Negative prices | Only an HTML attribute stopped them | Rejected in Python **and** by CHECK constraints **and** by triggers (so old MySQL versions are protected too) |
| Seller items missing in admin / shop | Several hidden steps; the core trigger file was missing from the project | Dashboard counts new seller items; one-click **Add to store (+ order stock)**; missing triggers rewritten |
| Admin login failing | Admin only existed in demo data | `setup_db.py --admin` creates it with a salted password hash |
| Slow | A new database connection for every query; ~10 queries per dashboard | One re-used connection per worker, one query for dashboards, cached category menu |
| Demo data | `02_sample_data.sql`, sample pictures, sample sellers | Removed completely |
| Weak spots | Hard-coded secret key, unsalted SHA-256 passwords, no CSRF guard | `SECRET_KEY` required, werkzeug password hashes, same-origin check on every POST, secure cookies |

---

## 6. Project layout

```
app.py                  entry point (Vercel + local)
vercel.json             caching headers
requirements.txt  .env.example
database/
  01_schema.sql         26 tables (no data)
  02_triggers.sql       28 triggers
  03_routines.sql       6 functions + 14 stored procedures
  04_views.sql          17 views
scripts/
  setup_db.py           builds the database and the admin login
  selftest_db.py        end-to-end test of every rule against a real MySQL
  sqlscript.py          runs .sql files that use DELIMITER
shop/
  config.py db.py supa.py security.py utils.py
  services.py catalog.py mailer.py
  routes/               public · accounts · store · customer · admin · admin_sales · restock · seller
  templates/            same pages as before
public/                 css, js, icons, manifest.webmanifest, sw.js (PWA)
tests/                  website tests (fake database)
```

## 7. Troubleshooting

| You see | Do this |
|---|---|
| "Sign-in is not set up yet" | `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY` missing |
| "Picture upload is not set up…" | add `SUPABASE_SECRET_KEY` |
| "Email confirmation is switched off in Supabase" | turn *Confirm email* ON, or set `REQUIRE_EMAIL_CONFIRMATION=false` |
| "Too many emails were sent" | Supabase's free mail limit — wait, or add custom SMTP |
| `/health` shows `database: false` | check `DB_*`, `DB_SSL=true` for cloud hosts, and that the host allows outside connections |
| setup_db error 1419 / 1227 | the MySQL user needs rights to create triggers/functions |
| Site shows "Request blocked" (403) | a form was posted from another address; open the site on its real `SITE_URL` |

---

## 8. Upgrading an existing installation

New tables, triggers and procedures are added by re-running the setup script. It is safe to repeat and never deletes data:
```
python scripts/setup_db.py            # (add --admin only if you want to reset the admin password)
```
Your existing data stays. Then restart `python app.py`.
