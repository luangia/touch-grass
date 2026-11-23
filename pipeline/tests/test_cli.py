from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from touchgrass.cli import app

from .conftest import REPO_CONFIG_DIR

runner = CliRunner()


def test_validate_repo_region() -> None:
    result = runner.invoke(
        app, ["regions", "validate", "portland-or", "--config-dir", str(REPO_CONFIG_DIR)]
    )
    assert result.exit_code == 0, result.output
    assert "✓ portland-or" in result.output
    assert "400 / 800 / 1200 m" in result.output


def test_validate_invalid_exits_nonzero(write_region: Any, chicago_dict: dict[str, Any]) -> None:
    chicago_dict["analysis_crs"] = 4326
    config_dir = write_region(chicago_dict)
    result = runner.invoke(
        app, ["regions", "validate", "chicago-il", "--config-dir", str(config_dir)]
    )
    assert result.exit_code == 1
    assert "geographic" in result.output


def test_validate_many_reports_each(write_region: Any, chicago_dict: dict[str, Any]) -> None:
    config_dir = write_region(chicago_dict)
    result = runner.invoke(
        app, ["regions", "validate", "chicago-il", "nowhere", "--config-dir", str(config_dir)]
    )
    assert result.exit_code == 1
    assert "✓ chicago-il" in result.output
    assert "nowhere" in result.output


def test_show_includes_defaults(write_region: Any, chicago_dict: dict[str, Any]) -> None:
    config_dir = write_region(chicago_dict)
    result = runner.invoke(app, ["regions", "show", "chicago-il", "--config-dir", str(config_dir)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["analysis"]["thresholds_min"] == [5, 10, 15]


def test_list(write_region: Any, chicago_dict: dict[str, Any]) -> None:
    config_dir = write_region(chicago_dict)
    result = runner.invoke(app, ["regions", "list", "--config-dir", str(config_dir)])
    assert result.stdout.split() == ["chicago-il"]
