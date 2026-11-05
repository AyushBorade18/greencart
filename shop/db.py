"""
db.py - everything that talks to MySQL.

Speed: opening a connection to a cloud database takes 100-500 ms. The old
code opened a NEW connection for EVERY query, which is why pages felt
slow. This version keeps one connection per worker thread and re-uses it
(it only checks that it is still alive after a quiet period).

Autocommit is ON on purpose: a long-lived connection that is NOT in
autocommit mode keeps showing an old snapshot of the data.
"""
import logging
import threading
import time

import mysql.connector

from . import config

DBError = mysql.connector.Error
log = logging.getLogger("greencart.db")

_local = threading.local()
_IDLE_CHECK_SECONDS = 20
# errors that mean "the connection died", so a retry on a new one makes sense
_LOST = {2003, 2006, 2013, 2055, 1927}


def _settings():
    cfg = dict(config.DB)
    cfg.update(connection_timeout=10, autocommit=True, charset="utf8mb4", use_pure=True)
    if config.DB_SSL:
        cfg["ssl_disabled"] = False
    return cfg


def _connection():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        if time.time() - getattr(_local, "last_used", 0) > _IDLE_CHECK_SECONDS:
            try:
                conn.ping(reconnect=True, attempts=2, delay=0)
            except DBError:
                conn = None
        if conn is not None and not conn.is_connected():
            conn = None
    if conn is None:
        conn = mysql.connector.connect(**_settings())
        _local.conn = conn
    _local.last_used = time.time()
    return conn


def close():
    """Drop this thread's connection (used by tests and after fatal errors)."""
    conn = getattr(_local, "conn", None)
    _local.conn = None
    if conn is not None:
        try:
            conn.close()
        except DBError:
            pass


def _run(work, retry=True):
    try:
        return work(_connection())
    except DBError as e:
        if retry and getattr(e, "errno", None) in _LOST:
            close()
            return _run(work, retry=False)
        raise


def query_all(sql, params=()):
    """SELECT -> list of dictionaries."""
    def work(conn):
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            cur.close()
    return _run(work)


def query_one(sql, params=()):
    """SELECT -> one dictionary (or None)."""
    def work(conn):
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            cur.fetchall()                      # drain, so the connection stays clean
            return row
        finally:
            cur.close()
    return _run(work)


def scalar(sql, params=(), default=0):
    row = query_one(sql, params)
    if not row:
        return default
    value = next(iter(row.values()))
    return default if value is None else value


def execute(sql, params=()):
    """INSERT / UPDATE / DELETE -> id of the new row (INSERT) or 0."""
    def work(conn):
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.lastrowid
        finally:
            cur.close()
    return _run(work, retry=False)             # never repeat a write by accident


def call_procedure(name, args=()):
    """
    Call a stored procedure. For an OUT parameter pass 0 as a placeholder.
    Returns the list of arguments with the OUT values filled in:
        result = call_procedure("sp_place_order", (1, "UPI", "", 0))
        order_id = result[3]
    """
    def work(conn):
        cur = conn.cursor()
        try:
            result = cur.callproc(name, args)
            for _ in cur.stored_results():     # drain any result set
                pass
            return list(result)
        finally:
            cur.close()
    return _run(work, retry=False)


# ---------------------------------------------------------------------
#  MySQL error -> a sentence a customer can understand
# ---------------------------------------------------------------------
def error_message(e):
    text = getattr(e, "msg", None) or str(e)
    code = getattr(e, "errno", None)

    if code == 1062:
        if "email" in text:
            return "This email is already registered."
        return "This value already exists. Please use a different one."
    if code == 1451:
        return "This record is used by other data (for example past orders), so it cannot be deleted."
    if code == 1452:
        return "That choice does not exist any more. Please refresh the page and try again."
    if code == 1644:                      # our own SIGNAL messages from triggers / procedures
        return text
    if code == 3819 or code == 4025:      # a CHECK constraint failed
        low = text.lower()
        if "phone" in low:
            return "Phone number must be exactly 10 digits."
        if "pincode" in low:
            return "Pincode must be exactly 6 digits."
        if "email" in low:
            return "Please enter a valid email address."
        if "price" in low:
            return "Price must be greater than 0."
        if "reorder" in low:
            return "Reorder level cannot be negative."
        if "listing_min" in low:
            return "Minimum order quantity must be at least 1."
        if "qty" in low or "inventory" in low or "quantity" in low:
            return "Quantity cannot be negative."
        return "Some of the entered values are not valid."
    if code in _LOST or code in (2002, 1045, 1049):
        return "The database is not reachable right now. Please try again in a moment."
    log.warning("Unhandled MySQL error %s: %s", code, text)
    return "Something went wrong while saving. Please try again."
