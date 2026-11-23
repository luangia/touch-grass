"""Region config schema and loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from touchgrass.config import (
    ConfigError,
    RegionConfig,
    find_config_dir,
    list_regions,
    load_region,
)
from touchgrass.config.loader import CONFIG_DIR_ENV

from .conftest import REPO_CONFIG_DIR


class TestRepositoryConfigs:
    def test_every_repo_region_is_valid(self) -> None:
        regions = list_regions(REPO_CONFIG_DIR)
        assert regions, "expected at least one region config in config/regions/"
        for region_id in regions:
            assert load_region(region_id, REPO_CONFIG_DIR).region_id == region_id

    def test_portland_derived_thresholds(self) -> None:
        cfg = load_region("portland-or", REPO_CONFIG_DIR)
        # 4.8 km/h = 80 m/min
        assert cfg.analysis.meters_per_minute == pytest.approx(80)
        assert cfg.analysis.threshold_distances_m == pytest.approx({5: 400, 10: 800, 15: 1200})


class TestSchema:
    def test_second_region_needs_no_code(self, chicago_dict: dict[str, Any]) -> None:
        cfg = RegionConfig.model_validate(chicago_dict)
        assert cfg.crs().name == "NAD83 / UTM zone 16N"

    def test_analysis_defaults(self, chicago_dict: dict[str, Any]) -> None:
        cfg = RegionConfig.model_validate(chicago_dict)
        assert cfg.analysis.thresholds_min == [5, 10, 15]
        assert cfg.analysis.walk_speed_kmh == 4.8

    @pytest.mark.parametrize(
        ("patch", "loc", "msg"),
        [
            ({"analysis_crs": 4326}, "analysis_crs", "geographic"),
            ({"analysis_crs": 3857}, "analysis_crs", "Mercator"),
            ({"analysis_crs": 3435}, "analysis_crs", "meters"),  # Illinois East, US ft
            ({"analysis_crs": 26910}, "", "outside the area of use"),  # Portland zone for Chicago
            ({"region_id": "Chicago IL"}, "region_id", "pattern"),
            ({"state_fips": "7"}, "state_fips", "pattern"),
            ({"county_fips": ["031", "031"]}, "county_fips", "duplicates"),
            ({"county_fips": []}, "county_fips", "at least 1"),
            # Swapped (lat, lon): both numbers are in range (-87.63 is a valid latitude near
            # the South Pole), so only the CRS area-of-use check catches it.
            ({"center_lonlat": [41.88, -87.63]}, "", "outside the area of use"),
            ({"center_lonlat": [-200, 41.88]}, "center_lonlat", "greater than or equal"),
            ({"buffer_m": 0}, "buffer_m", "greater than 0"),
            ({"country": "CA"}, "country", "'US'"),
            ({"analysis": {"thresholds_min": [10, 5]}}, "analysis.thresholds_min", "ascending"),
            ({"analysis": {"thresholds_min": [5, 5]}}, "analysis.thresholds_min", "unique"),
            ({"analysis": {"walk_speed_kmh": -1}}, "analysis.walk_speed_kmh", "greater than 0"),
            ({"bufer_m": 100}, "bufer_m", "Extra inputs are not permitted"),  # typo
        ],
    )
    def test_rejects(
        self, chicago_dict: dict[str, Any], patch: dict[str, Any], loc: str, msg: str
    ) -> None:
        chicago_dict.update(patch)
        with pytest.raises(ValidationError) as info:
            RegionConfig.model_validate(chicago_dict)
        errors = info.value.errors()
        locs = [".".join(str(p) for p in e["loc"]) for e in errors]
        if loc:
            assert any(loc in got for got in locs), locs
        assert any(msg in e["msg"] for e in errors), [e["msg"] for e in errors]

    def test_reports_all_errors_at_once(self, chicago_dict: dict[str, Any]) -> None:
        chicago_dict.update({"analysis_crs": 4326, "state_fips": "x", "buffer_m": -5})
        with pytest.raises(ValidationError) as info:
            RegionConfig.model_validate(chicago_dict)
        assert info.value.error_count() == 3

    def test_missing_required_field(self, chicago_dict: dict[str, Any]) -> None:
        del chicago_dict["center_lonlat"]
        with pytest.raises(ValidationError, match="center_lonlat"):
            RegionConfig.model_validate(chicago_dict)

    def test_config_is_immutable(self, chicago_dict: dict[str, Any]) -> None:
        cfg = RegionConfig.model_validate(chicago_dict)
        with pytest.raises(ValidationError):
            cfg.buffer_m = 5  # type: ignore[misc]


class TestLoader:
    def test_load_by_id_and_by_path(self, write_region: Any, chicago_dict: dict[str, Any]) -> None:
        config_dir = write_region(chicago_dict)
        assert load_region("chicago-il", config_dir).name == "Chicago, Illinois"
        path = config_dir / "regions" / "chicago-il.yaml"
        assert load_region(str(path)).region_id == "chicago-il"

    def test_region_id_must_match_filename(
        self, write_region: Any, chicago_dict: dict[str, Any]
    ) -> None:
        config_dir = write_region(chicago_dict, stem="chicago")
        with pytest.raises(ConfigError, match="must match the file name"):
            load_region("chicago", config_dir)

    def test_unknown_region_lists_available(
        self, write_region: Any, chicago_dict: dict[str, Any]
    ) -> None:
        config_dir = write_region(chicago_dict)
        with pytest.raises(ConfigError, match=r"available: chicago-il"):
            load_region("nowhere", config_dir)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("region_id: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_region(str(bad))

    def test_top_level_must_be_mapping(self, write_region: Any) -> None:
        config_dir = write_region(["not", "a", "mapping"], stem="listy")
        with pytest.raises(ConfigError, match="mapping"):
            load_region("listy", config_dir)

    def test_validation_problems_are_readable(
        self, write_region: Any, chicago_dict: dict[str, Any]
    ) -> None:
        chicago_dict["analysis_crs"] = 4326
        config_dir = write_region(chicago_dict)
        with pytest.raises(ConfigError) as info:
            load_region("chicago-il", config_dir)
        assert info.value.problems[0].startswith("analysis_crs: EPSG:4326")

    def test_find_config_dir_walks_up(
        self, write_region: Any, chicago_dict: dict[str, Any], tmp_path: Path
    ) -> None:
        config_dir = write_region(chicago_dict)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_config_dir(nested) == config_dir

    def test_env_var_overrides(
        self,
        write_region: Any,
        chicago_dict: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_dir = write_region(chicago_dict)
        monkeypatch.setenv(CONFIG_DIR_ENV, str(config_dir))
        assert find_config_dir(Path("/")) == config_dir.resolve()
