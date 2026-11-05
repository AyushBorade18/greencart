"""
sqlscript.py - split a .sql file into single statements.

MySQL Workbench understands the line  DELIMITER $$  but a Python database
driver does not. This small splitter understands it, so the very same
.sql files work in Workbench AND in scripts/setup_db.py.

It ignores comments, and a ';' or '$$' inside a quoted text is not treated
as the end of a statement.
"""


def split_statements(text):
    """Return a list of SQL statements (without their delimiter)."""
    statements = []
    delimiter = ";"
    buf = []
    i, n = 0, len(text)
    at_line_start = True

    def flush():
        stmt = "".join(buf).strip()
        buf.clear()
        if stmt:
            statements.append(stmt)

    while i < n:
        ch = text[i]

        # ---- DELIMITER x   (a client command, not SQL)
        if at_line_start and text[i:i + 9].upper() == "DELIMITER" and text[i + 9:i + 10] in (" ", "\t"):
            end = text.find("\n", i)
            end = n if end == -1 else end
            new_delimiter = text[i + 9:end].strip()
            if new_delimiter:
                flush()
                delimiter = new_delimiter
            i = end + 1
            at_line_start = True
            continue

        # ---- comments are dropped
        if text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            end = text.find("\n", i)
            i = n if end == -1 else end          # keep the newline
            continue
        if ch == "#":
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue

        # ---- quoted text is copied as it is
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:      # doubled quote
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i:j + 1])
            i = j + 1
            at_line_start = False
            continue

        # ---- end of statement
        if text.startswith(delimiter, i):
            flush()
            i += len(delimiter)
            at_line_start = False
            continue

        buf.append(ch)
        at_line_start = ch == "\n" or (at_line_start and ch in " \t\r")
        i += 1

    flush()
    return statements


def read_statements(path):
    with open(path, encoding="utf-8") as f:
        return split_statements(f.read())
