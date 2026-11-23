"""Database connection settings (D-019 Q7).

Resolution order:
  1. ``DATABASE_URL`` (environment) — a full libpq URL or ``key=value`` string; wins outright.
  2. ``POSTGRES_*`` environment variables, then the repo's ``.env`` file (the same file
     docker compose reads), then defaults (localhost:5432).

The password is never printed: use :func:`describe` for log lines.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import conninfo as pg_conninfo

DATABASE_URL_ENV = "DATABASE_URL"
_KEYS = {  # POSTGRES_* variable → libpq keyword
    "POSTGRES_HOST": "host",
    "POSTGRES_PORT": "port",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "password",
    "POSTGRES_DB": "dbname",
}
_DEFAULTS = {"host": "localhost", "port": "5432"}


class DatabaseUnavailableError(RuntimeError):
    pass


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` .env file (comments and blank lines ignored)."""
    values: dict[str, str] = {}
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def find_env_file(start: Path | None = None) -> Path | None:
    """The nearest ``.env`` next to a ``docker-compose.yml``, walking up from ``start``."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / ".env").is_file() and (directory / "docker-compose.yml").is_file():
            return directory / ".env"
    return None


@dataclass(frozen=True, slots=True)
class DbSettings:
    conninfo: str  # libpq connection string, as accepted by psycopg.connect

    def with_dbname(self, dbname: str) -> DbSettings:
        return DbSettings(pg_conninfo.make_conninfo(self.conninfo, dbname=dbname))

    @property
    def dbname(self) -> str:
        return str(pg_conninfo.conninfo_to_dict(self.conninfo).get("dbname") or "")

    def describe(self) -> str:
        """``user@host:port/dbname`` — safe to print (no password)."""
        d = pg_conninfo.conninfo_to_dict(self.conninfo)
        return (
            f"{d.get('user', '?')}@{d.get('host', '?')}:{d.get('port', '?')}/{d.get('dbname', '?')}"
        )


def load_settings(
    environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> DbSettings:
    env = os.environ if environ is None else environ
    url = env.get(DATABASE_URL_ENV)
    if url:
        return DbSettings(pg_conninfo.make_conninfo(url))

    file_values: dict[str, str] = {}
    path = env_file if env_file is not None else find_env_file()
    if path is not None and path.is_file():
        file_values = read_env_file(path)

    params = dict(_DEFAULTS)
    for var, keyword in _KEYS.items():
        value = env.get(var) or file_values.get(var)
        if value:
            params[keyword] = value
    missing = [k for k in ("user", "dbname") if k not in params]
    if missing:
        raise DatabaseUnavailableError(
            f"no database settings for {missing}: set {DATABASE_URL_ENV} or POSTGRES_* "
            "(e.g. copy .env.example to .env)"
        )
    return DbSettings(pg_conninfo.make_conninfo(**params))


def connect(
    settings: DbSettings, *, autocommit: bool = False, timeout_s: int = 5
) -> psycopg.Connection[tuple[object, ...]]:
    try:
        return psycopg.connect(
            settings.conninfo,
            autocommit=autocommit,
            connect_timeout=timeout_s,
            application_name="touchgrass-pipeline",
        )
    except psycopg.OperationalError as err:
        raise DatabaseUnavailableError(
            f"cannot connect to {settings.describe()} (is `docker compose up -d db` running?)"
        ) from err
