"""API settings from the environment (DECISIONS.md D-023).

Database: the same resolution rules as the pipeline (D-019 Q7), re-implemented here because
the API does not import the pipeline:
  1. ``DATABASE_URL``; else
  2. ``POSTGRES_*`` environment variables, then the repo's ``.env`` (next to
     ``docker-compose.yml``), then defaults (localhost:5432).

``TOUCHGRASS_CORS_ORIGINS``: comma-separated browser origins allowed to call the API
(default: the Vite dev server).
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

    def describe(self) -> str:
        d = pg_conninfo.conninfo_to_dict(self.conninfo)
        return (
            f"{d.get('user', '?')}@{d.get('host', '?')}:{d.get('port', '?')}/{d.get('dbname', '?')}"
        )


def load_settings(environ: Mapping[str, str] | None = None, start: Path | None = None) -> Settings:
    env = os.environ if environ is None else environ
    origins = tuple(
        o.strip() for o in env.get("TOUCHGRASS_CORS_ORIGINS", ",".join(DEFAULT_CORS)).split(",")
    )
    origins = tuple(o for o in origins if o)
    url = env.get("DATABASE_URL")
    if url:
        return Settings(pg_conninfo.make_conninfo(url), origins)
    file_values = _env_file((start or Path.cwd()).resolve())
    params = {"host": "localhost", "port": "5432"}
    for var, keyword in _KEYS.items():
        value = env.get(var) or file_values.get(var)
        if value:
            params[keyword] = value
    missing = [k for k in ("user", "dbname") if k not in params]
    if missing:
        raise SettingsError(f"no database settings for {missing}: set DATABASE_URL or POSTGRES_*")
    return Settings(pg_conninfo.make_conninfo(**params), origins)
