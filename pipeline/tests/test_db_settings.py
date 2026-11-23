"""Connection settings resolution (offline: no database needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from touchgrass.db.connection import DatabaseUnavailableError, load_settings, read_env_file


def _env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nPOSTGRES_USER=alice\nPOSTGRES_PASSWORD='s3cret'\n"
        "POSTGRES_DB=touchgrass\nPOSTGRES_PORT=5433\n",
        "utf-8",
    )
    return path


def test_env_file_parsing(tmp_path: Path) -> None:
    values = read_env_file(_env_file(tmp_path))
    assert values["POSTGRES_PASSWORD"] == "s3cret"  # quotes stripped
    assert "# comment" not in values


def test_env_file_then_environment_override(tmp_path: Path) -> None:
    settings = load_settings(environ={"POSTGRES_PORT": "6000"}, env_file=_env_file(tmp_path))
    assert settings.describe() == "alice@localhost:6000/touchgrass"
    assert "s3cret" not in settings.describe()  # safe to print
    assert settings.with_dbname("touchgrass_test").dbname == "touchgrass_test"


def test_database_url_wins(tmp_path: Path) -> None:
    settings = load_settings(
        environ={"DATABASE_URL": "postgresql://bob:pw@db.internal:5432/other"},
        env_file=_env_file(tmp_path),
    )
    assert settings.describe() == "bob@db.internal:5432/other"


def test_missing_settings_are_reported(tmp_path: Path) -> None:
    with pytest.raises(DatabaseUnavailableError, match="POSTGRES_"):
        load_settings(environ={}, env_file=tmp_path / "absent.env")
