"""Connection pool, read-only sessions and the schema contract (DECISIONS.md D-023 Q2, Q8).

The pipeline and the API share nothing but the database. ``EXPECTED_SCHEMA_VERSION`` is
the migration this API was written against; at startup (and in ``/health``) the API
refuses a database at any other version instead of failing on a renamed column later.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = "touchgrass"
EXPECTED_SCHEMA_VERSION = 9

Conn = psycopg.Connection[dict[str, Any]]


class SchemaMismatchError(RuntimeError):
    pass


SESSION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=10000"


def make_pool(
    conninfo: str, min_size: int = 1, max_size: int = 8, *, session_options: bool = True
) -> ConnectionPool[Conn]:
    """A pool of autocommit, read-only connections that return rows as dicts.

    ``session_options`` sends the read-only and timeout settings at connect time. Turn it
    off only behind a pooler that rejects startup options, with the settings on the role
    instead (``touchgrass db create-reader``, D-026 Q7).
    """
    kwargs: dict[str, Any] = {
        "autocommit": True,
        "row_factory": dict_row,
        "application_name": "touchgrass-api",
    }
    if session_options:
        # Every transaction is read-only: a bug cannot write (D-023 Q8).
        kwargs["options"] = SESSION_OPTIONS
    return ConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        open=False,
        check=ConnectionPool.check_connection,  # drop connections the server closed
        kwargs=kwargs,
    )


def schema_version(conn: Conn) -> int | None:
    row = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{SCHEMA}.schema_migrations",)
    ).fetchone()
    if not row or not row["ok"]:
        return None
    row = conn.execute(f"SELECT max(version) AS v FROM {SCHEMA}.schema_migrations").fetchone()
    return None if row is None else row["v"]


def require_schema(conn: Conn) -> int:
    version = schema_version(conn)
    if version != EXPECTED_SCHEMA_VERSION:
        raise SchemaMismatchError(
            f"database schema is at migration {version}, this API expects "
            f"{EXPECTED_SCHEMA_VERSION}; run `touchgrass db migrate` or update the API"
        )
    return version
