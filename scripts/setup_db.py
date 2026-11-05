"""
setup_db.py - create the whole database in ONE command.

    python scripts/setup_db.py                 build / update tables, triggers, procedures, views
    python scripts/setup_db.py --admin         ... and create the admin login (from ADMIN_USERNAME / ADMIN_PASSWORD)
    python scripts/setup_db.py --reset --yes   DELETE EVERYTHING in the database first (careful!)

The database itself (DB_NAME) must already exist:   CREATE DATABASE greencart;
No demo data is ever inserted.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import mysql.connector                      # noqa: E402
from shop import config                     # noqa: E402
from shop.security import hash_password     # noqa: E402
from sqlscript import read_statements       # noqa: E402

FILES = ["01_schema.sql", "02_triggers.sql", "03_routines.sql", "04_views.sql"]


def connect():
    cfg = dict(config.DB)
    cfg.update(autocommit=True, charset="utf8mb4", use_pure=True, connection_timeout=15)
    if config.DB_SSL:
        cfg["ssl_disabled"] = False
    try:
        return mysql.connector.connect(**cfg)
    except mysql.connector.Error as e:
        sys.exit("Could not connect to MySQL (%s@%s:%s / %s): %s\n"
                 "Check DB_HOST, DB_PORT, DB_USER, DB_PASSWORD and DB_NAME in your .env file, and that "
                 "the database exists:  CREATE DATABASE %s;"
                 % (cfg["user"], cfg["host"], cfg["port"], cfg["database"], e, cfg["database"]))


def reset(conn):
    cur = conn.cursor()
    db = config.DB["database"]
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    cur.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = %s", (db,))
    for name, kind in cur.fetchall():
        cur.execute("DROP %s `%s`" % ("VIEW" if kind == "VIEW" else "TABLE", name))
    cur.execute("SELECT routine_type, routine_name FROM information_schema.routines WHERE routine_schema = %s", (db,))
    for kind, name in cur.fetchall():
        cur.execute("DROP %s `%s`" % (kind, name))
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    cur.close()
    print("Database emptied.")


def run_files(conn):
    cur = conn.cursor()
    for name in FILES:
        path = os.path.join(ROOT, "database", name)
        statements = read_statements(path)
        for number, sql in enumerate(statements, 1):
            try:
                cur.execute(sql)
            except mysql.connector.Error as e:
                preview = " ".join(sql.split())[:90]
                hint = ""
                if e.errno in (1419, 1227):
                    hint = ("\nHint: MySQL needs permission to create triggers/functions. Use a user with "
                            "full rights on this database, or ask the host to set "
                            "log_bin_trust_function_creators = 1.")
                sys.exit("\nFAILED in %s (statement %d):\n  %s\n  MySQL error %s: %s%s"
                         % (name, number, preview, e.errno, e.msg, hint))
        print("  %-16s %3d statements ok" % (name, len(statements)))
    cur.close()


def create_admin(conn, username, password):
    if not username or len(password) < 8:
        sys.exit("Set ADMIN_USERNAME and an ADMIN_PASSWORD of at least 8 characters (in .env), "
                 "or pass --admin-user / --admin-password.")
    cur = conn.cursor()
    cur.execute("SELECT admin_id FROM admin WHERE username = %s", (username,))
    if cur.fetchone():
        cur.execute("UPDATE admin SET password_hash = %s WHERE username = %s", (hash_password(password), username))
        print("Admin '%s': password updated." % username)
    else:
        cur.execute("INSERT INTO admin (username, password_hash) VALUES (%s, %s)", (username, hash_password(password)))
        print("Admin '%s' created." % username)
    cur.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="delete every table first")
    ap.add_argument("--yes", action="store_true", help="confirm --reset")
    ap.add_argument("--admin", action="store_true", help="create / update the admin login")
    ap.add_argument("--admin-user", default=os.environ.get("ADMIN_USERNAME", "admin"))
    ap.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD", ""))
    args = ap.parse_args()

    if args.reset and not args.yes:
        sys.exit("--reset deletes ALL data in '%s'. Add --yes if you are sure." % config.DB["database"])

    conn = connect()
    print("Connected to MySQL database '%s'." % config.DB["database"])
    if args.reset:
        reset(conn)
    run_files(conn)
    if args.admin:
        create_admin(conn, args.admin_user.strip(), args.admin_password)
    print("\nDone. Next:  python app.py")
    conn.close()


if __name__ == "__main__":
    main()
