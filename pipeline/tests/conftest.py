from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG_DIR = REPO_ROOT / "config"

# A second, non-Portland region. If this validates with no code changes, onboarding a new
# city really is "just config" (DECISIONS.md D-004).
_CHICAGO: dict[str, Any] = {
    "region_id": "chicago-il",
    "name": "Chicago, Illinois",
    "country": "US",
    "analysis_crs": 26916,  # NAD83 / UTM zone 16N
    "center_lonlat": [-87.63, 41.88],
    "state_fips": "17",
    "county_fips": ["031"],
    "buffer_m": 2000,
}


@pytest.fixture
def chicago_dict() -> dict[str, Any]:
    return copy.deepcopy(_CHICAGO)


@pytest.fixture
def write_region(tmp_path: Path) -> Any:
    """Write a config dict to <tmp>/config/regions/<stem>.yaml and return the config dir."""

    def _write(data: object, stem: str | None = None) -> Path:
        regions = tmp_path / "config" / "regions"
        regions.mkdir(parents=True, exist_ok=True)
        name = stem or (data["region_id"] if isinstance(data, dict) else "broken")
        (regions / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        return tmp_path / "config"

    return _write
