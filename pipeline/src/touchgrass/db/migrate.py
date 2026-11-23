"""Versioned schema migrations: numbered SQL files, applied in order, exactly once (D-019 Q2).

Files live in ``touchgrass/db/migrations/NNNN_<name>.sql`` (shipped inside the package so
the runner finds them wherever it is installed). For each pending file the runner, in ONE
transaction: takes an advisory lock, runs the SQL, and records (version, filename, sha256)
in ``touchgrass.schema_migrations``. PostgreSQL DDL is transactional, so a failing file
leaves no half-built schema behind.

An already-applied file whose checksum has changed is refused: editing history would make
databases built at different times silently differ. Change the schema with a NEW file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from typing import Any

import psycopg

from touchgrass.db import SCHEMA

_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Any constant works; it only has to be the same for every runner (pg_advisory_xact_lock).
_LOCK_KEY = 0x70A6_6A55

Conn = psycopg.Connection[Any]


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    filename: str
    sql: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MigrationState:
    migration: Migration
    applied_at: datetime | None

    @property
    def applied(self) -> bool:
        return self.applied_at is not None


def discover() -> list[Migration]:
    """All migration files, sorted; versions must be unique and contiguous from 1."""
    found = []
    for entry in resources.files("touchgrass.db").joinpath("migrations").iterdir():
        match = _FILENAME.match(entry.name)
        if match is None:
            continue
        sql = entry.read_text("utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found.append(Migration(int(match.group(1)), entry.name, sql, digest))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"migration versions must be 1..N with no gaps: {versions}")
    return found


def _bootstrap(conn: Conn) -> None:
    """The schema and the bookkeeping table must exist before any migration can be recorded."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_migrations (
                version     integer PRIMARY KEY,
                filename    text NOT NULL,
                sha256      text NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )"""
    )


def _applied(conn: Conn) -> dict[int, tuple[str, str, datetime]]:
    exists = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (f"{SCHEMA}.schema_migrations",)
    ).fetchone()
    if not (exists and exists[0]):
        return {}
    rows = conn.execute(
        f"SELECT version, filename, sha256, applied_at FROM {SCHEMA}.schema_migrations"
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def status(conn: Conn, migrations: list[Migration] | None = None) -> list[MigrationState]:
    """Applied/pending state of every migration. Raises if history and files disagree."""
    migrations = discover() if migrations is None else migrations
    applied = _applied(conn)
    known = {m.version: m for m in migrations}
    problems = [
        f"version {v} ({applied[v][0]}) is applied in the database but has no file here "
        "(is this code older than the database?)"
        for v in sorted(applied.keys() - known.keys())
    ]
    for m in migrations:
        if m.version in applied and applied[m.version][1] != m.sha256:
            problems.append(
                f"{m.filename} was changed after being applied (checksum mismatch); "
                "add a new migration instead of editing an applied one"
            )
    if problems:
        raise MigrationError("; ".join(problems))
    return [
        MigrationState(m, applied[m.version][2] if m.version in applied else None)
        for m in migrations
    ]


def migrate(conn: Conn, migrations: list[Migration] | None = None) -> list[Migration]:
    """Apply every pending migration, each in its own transaction. Returns those applied."""
    migrations = discover() if migrations is None else migrations
    done: list[Migration] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        _bootstrap(conn)
    for state in status(conn, migrations):
        if state.applied:
            continue
        m = state.migration
        with conn.transaction():
            # Serialise concurrent runners; re-check under the lock so nobody applies twice.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            if m.version in _applied(conn):
                continue
            try:
                conn.execute(m.sql.encode("utf-8"))  # no params → multi-statement allowed
            except psycopg.Error as err:
                raise MigrationError(f"{m.filename} failed: {err}") from err
            conn.execute(
                f"INSERT INTO {SCHEMA}.schema_migrations (version, filename, sha256)"
                " VALUES (%s, %s, %s)",
                (m.version, m.filename, m.sha256),
            )
        done.append(m)
    return done


def require_current(conn: Conn) -> None:
    """Raise unless every migration is applied (used before loading data)."""
    pending = [s.migration.filename for s in status(conn) if not s.applied]
    if pending:
        raise MigrationError(f"pending migrations {pending}; run `touchgrass db migrate`")
