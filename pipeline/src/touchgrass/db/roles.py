"""The API's database role: read-only by privilege, not only by session setting (D-026 Q7).

The API already opens read-only sessions with a statement timeout (D-023 Q8), but it sets
them through the connection's startup ``options``, which some poolers drop. Here the same
safeguards live **on the role**, so they hold however the API connects, and the role has
no privilege to write at all:

* ``LOGIN`` only: no superuser, no database or role creation;
* ``USAGE`` on the schema and ``SELECT`` on its tables, now and for tables later migrations
  create (default privileges of the role that runs the migrations);
* ``default_transaction_read_only = on`` and ``statement_timeout`` set on the role.

Run it as the database owner (the role that runs ``touchgrass db migrate``).
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql

SCHEMA = "touchgrass"
DEFAULT_ROLE = "touchgrass_reader"
DEFAULT_TIMEOUT_MS = 10_000


def ensure_reader(
    conn: psycopg.Connection[Any],
    role: str,
    password: str,
    *,
    statement_timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> bool:
    """Create (or update) ``role`` as a SELECT-only login. Returns True if it was created."""
    if not password:
        raise ValueError("the reader role needs a password")
    r = sql.Identifier(role)
    s = sql.Identifier(SCHEMA)
    with conn.transaction():
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = sql.SQL("ALTER ROLE" if exists else "CREATE ROLE")
        conn.execute(
            sql.SQL("{} {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(
                verb, r, sql.Literal(password)
            )
        )
        db = sql.Identifier(conn.info.dbname)
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, r))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(s, r))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(s, r))
        conn.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(
                s, r
            )
        )
        conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(r))
        conn.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
                r, sql.Literal(int(statement_timeout_ms))
            )
        )
    return not exists
