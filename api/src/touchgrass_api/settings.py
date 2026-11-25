"""API settings from the environment (DECISIONS.md D-023).

Database: the same resolution rules as the pipeline (D-019 Q7), re-implemented here because
the API does not import the pipeline:
  1. ``DATABASE_URL``; else
  2. ``POSTGRES_*`` environment variables, then the repo's ``.env`` (next to
     ``docker-compose.yml``), then defaults (localhost:5432).

``TOUCHGRASS_CORS_ORIGINS``: comma-separated browser origins allowed to call the API
(default: the Vite dev server).

``TOUCHGRASS_POOL_MIN`` / ``TOUCHGRASS_POOL_MAX``: connection pool size (default 1 / 8; a
small free database wants fewer, D-026 Q2).

``TOUCHGRASS_SESSION_OPTIONS``: ``0`` to stop sending the read-only and timeout settings at
connect time, for a pooler that rejects them; the role must then carry them (D-026 Q7).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from psycopg import conninfo as pg_conninfo

_KEYS = {
    "POSTGRES_HOST": "host",
    "POSTGRES_PORT": "port",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "password",
    "POSTGRES_DB": "dbname",
}
DEFAULT_CORS = ("http://localhost:5173", "http://127.0.0.1:5173")


class SettingsError(RuntimeError):
    pass


def _env_file(start: Path) -> dict[str, str]:
    for d in (start, *start.parents):
        path = d / ".env"
        if path.is_file() and (d / "docker-compose.yml").is_file():
            out: dict[str, str] = {}
            for raw in path.read_text("utf-8").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                        value = value[1:-1]
                    out[key.strip()] = value
            return out
    return {}


@dataclass(frozen=True)
class Settings:
    conninfo: str
    cors_origins: tuple[str, ...] = DEFAULT_CORS
    pool_min: int = 1
    pool_max: int = 8
    cache_max_age_s: int = 300
    session_options: bool = True

    def describe(self) -> str:
        d = pg_conninfo.conninfo_to_dict(self.conninfo)
        return (
            f"{d.get('user', '?')}@{d.get('host', '?')}:{d.get('port', '?')}/{d.get('dbname', '?')}"
        )


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as err:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from err
    if value < 1:
        raise SettingsError(f"{key} must be at least 1, got {value}")
    return value


def load_settings(environ: Mapping[str, str] | None = None, start: Path | None = None) -> Settings:
    env = os.environ if environ is None else environ
    origins = tuple(
        o.strip() for o in env.get("TOUCHGRASS_CORS_ORIGINS", ",".join(DEFAULT_CORS)).split(",")
    )
    origins = tuple(o for o in origins if o)
    pool_min = _int(env, "TOUCHGRASS_POOL_MIN", 1)
    pool_max = _int(env, "TOUCHGRASS_POOL_MAX", 8)
    if pool_min > pool_max:
        raise SettingsError(f"TOUCHGRASS_POOL_MIN ({pool_min}) exceeds TOUCHGRASS_POOL_MAX")
    session_options = env.get("TOUCHGRASS_SESSION_OPTIONS", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    url = env.get("DATABASE_URL")
    if url:
        conninfo = pg_conninfo.make_conninfo(url)
        return Settings(conninfo, origins, pool_min, pool_max, session_options=session_options)
    file_values = _env_file((start or Path.cwd()).resolve())
    params = {"host": "localhost", "port": "5432"}
    for var, keyword in _KEYS.items():
        value = env.get(var) or file_values.get(var)
        if value:
            params[keyword] = value
    missing = [k for k in ("user", "dbname") if k not in params]
    if missing:
        raise SettingsError(f"no database settings for {missing}: set DATABASE_URL or POSTGRES_*")
    conninfo = pg_conninfo.make_conninfo(**params)
    return Settings(conninfo, origins, pool_min, pool_max, session_options=session_options)
