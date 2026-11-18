"""Apply db/migrations/*.sql in order, once each, over Neon's HTTP endpoint.

    DATABASE_URL_UNPOOLED=... python -m gyrex.migrate          # apply pending
    DATABASE_URL_UNPOOLED=... python -m gyrex.migrate --status

Each file runs in one transaction together with its schema_migrations row. Uses the direct
(unpooled) connection string, as Neon recommends for migrations.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

from gyrex.neon_http import NeonHttp

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


def split_statements(sql: str) -> list[str]:
    """Split on top-level semicolons, respecting quotes, dollar-quoted bodies and comments."""
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j]); i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j]); i = j
        elif c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == c and sql[j + 1 : j + 2] == c:
                    j += 2
                elif sql[j] == c:
                    break
                else:
                    j += 1
            buf.append(sql[i : j + 1]); i = j + 1
        elif c == "$":
            end = sql.find("$", i + 1)
            tag = sql[i : end + 1] if end != -1 else ""
            if tag and (tag == "$$" or tag[1:-1].replace("_", "a").isalnum()):
                j = sql.find(tag, end + 1)
                j = n if j == -1 else j + len(tag)
                buf.append(sql[i:j]); i = j
            else:
                buf.append(c); i += 1
        elif c == ";":
            out.append("".join(buf)); buf = []; i += 1
        else:
            buf.append(c); i += 1
    out.append("".join(buf))

    def has_code(s: str) -> bool:
        s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
        return any(line.strip() and not line.strip().startswith("--") for line in s.splitlines())

    return [s.strip() for s in out if has_code(s)]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--status", action="store_true", help="list applied and pending migrations")
    args = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not url:
        p.error("set DATABASE_URL_UNPOOLED (or DATABASE_URL)")
    db = NeonHttp(url)
    db.query(
        "create table if not exists schema_migrations ("
        " name text primary key, checksum text not null, applied_at timestamptz not null default now())"
    )
    applied = {r["name"]: r["checksum"] for r in db.query("select name, checksum from schema_migrations")}

    files = sorted(MIGRATIONS.glob("*.sql"))
    for f in files:
        text = f.read_text()
        checksum = hashlib.sha256(text.encode()).hexdigest()[:16]
        if f.name in applied:
            note = "" if applied[f.name] == checksum else "  (file changed since it was applied)"
            print(f"applied  {f.name}{note}")
            continue
        if args.status:
            print(f"pending  {f.name}")
            continue
        stmts = [(s, []) for s in split_statements(text)]
        stmts.append(("insert into schema_migrations (name, checksum) values ($1, $2)", [f.name, checksum]))
        db.transaction(stmts)
        print(f"applied  {f.name} ({len(stmts) - 1} statements)")
    if not files:
        print("no migrations found", file=sys.stderr)


if __name__ == "__main__":
    main()
