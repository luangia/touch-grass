"""End-to-end ``process`` with parks on the synthetic region (offline).

Adds to the Stage 2 synthetic region (see test_pipeline_synthetic):
  OSM:  "West Woods" nature reserve over cells 0-1, a private park (cell 2),
        a golf course, and "Corner Park" (a leisure=park inside West Woods → merged).
  ORCA: one reference park covering cells 1-2.
Hand-computed: one unit (West Woods + Corner Park), class natural_area;
precision ≈ 0.5 (cell 1 of cells 0-1 confirmed), recall ≈ 0.5 (cell 1 of cells 1-2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import box

from touchgrass.config import RegionConfig
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.pipeline.process import load_outputs, run_process
from touchgrass.pipeline.validate import failed, run_validation

from .osm_fixture import OsmFixture, cell
from .test_pipeline_synthetic import _fc, _store_file, _store_json, build_raw, config

ORCA_URL = "https://gis.test/arcgis/rest/services/Orca/FeatureServer/5"


def parks_config(sentinels: list[str]) -> RegionConfig:
    data = config().model_dump(mode="json")
    data["osm"] = {"geofabrik_path": "north-america/us/testville", "snapshot": "261101"}
    data["parks"] = {
        "include_tags": {"leisure": ["park", "nature_reserve"]},
        "natural_area_tags": {"leisure": ["nature_reserve"]},
        "exclude_tags": {"not_open_space": {"leisure": ["golf_course"]}},
        "validation_source": {
            "adapter": "arcgis_feature_layer",
            "url": ORCA_URL,
            "attribution": "test",
            "license": {"name": "Test licence"},
        },
        "sentinel_parks": sentinels,
    }
    return RegionConfig.model_validate(data)


def build_park_raw(cfg: RegionConfig, paths: RegionPaths) -> None:
    build_raw(cfg, paths)
    f = OsmFixture()
    f.area_way(10, (-123.02, 45.0, -123.00, 45.01), leisure="nature_reserve", name="West Woods")
    f.area_way(11, (-123.015, 45.002, -123.012, 45.004), leisure="park", name="Corner Park")
    f.area_way(12, cell(2), leisure="park", access="private", name="Members Lawn")
    # Just south of the boundary but inside the 500 m buffer (rep point ~44.9985 N).
    f.area_way(13, (-123.02, 44.997, -123.01, 45.0), leisure="golf_course", name="Links")
    osm_raw = src.osm_source(cfg, paths)
    assert osm_raw is not None
    f.write(osm_raw.path)
    _store_file(osm_raw)

    ref = src.park_validation_source(cfg, paths)
    assert ref is not None
    _store_json(ref, _fc([({"SITENAME": "Reference Park"}, box(-123.01, 45.0, -122.99, 45.01))]))


@pytest.fixture
def region(tmp_path: Path) -> tuple[RegionConfig, RegionPaths]:
    cfg = parks_config(["West Woods", "Corner Park"])
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    return cfg, paths


def test_parks_end_to_end(region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = region
    result = run_process(cfg, paths)
    f = result.frames

    reasons = f["green_candidates"].set_index("name")["reason"].to_dict()
    assert reasons == {
        "West Woods": "included",
        "Corner Park": "included",
        "Members Lawn": "access_restricted",
        "Links": "not_open_space",
    }

    units = f["parks"]
    assert len(units) == 1
    unit = units.iloc[0]
    assert (unit["name"], unit["park_class"], unit["n_members"]) == (
        "West Woods",
        "natural_area",
        2,
    )
    assert unit["park_id"] == "osm:w10"

    agreement = result.stats["parks"]["validation"]["area"]
    assert agreement["precision"] == pytest.approx(0.5, abs=0.01)
    assert agreement["recall"] == pytest.approx(0.5, abs=0.01)
    disc = f["park_discrepancies"].set_index("direction")["name"].to_dict()
    assert disc == {"a_only": "West Woods", "b_only": "Reference Park"}

    assert failed(run_validation(cfg, f, result.stats)) == []


def test_missing_sentinel_fails_validation(tmp_path: Path) -> None:
    cfg = parks_config(["West Woods", "Forgotten Park"])
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    result = run_process(cfg, paths)
    bad = {c.name: c for c in failed(run_validation(cfg, result.frames, result.stats))}
    assert bad["parks.sentinels_present"].actual == ["Forgotten Park"]


def test_parks_outputs_round_trip(region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = region
    run_process(cfg, paths)
    frames = load_outputs(paths.processed)
    assert {"green_candidates", "parks", "park_discrepancies"} <= frames.keys()


def test_zoo_inside_park_is_cut_out(tmp_path: Path) -> None:
    cfg = parks_config(["West Woods"])
    data = cfg.model_dump(mode="json")
    data["parks"]["exclude_tags"]["not_open_space"]["tourism"] = ["zoo"]
    data["parks"]["subtract_reasons"] = ["not_open_space"]
    cfg = RegionConfig.model_validate(data)
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    # Rewrite the OSM fixture with a zoo inside West Woods.
    f = OsmFixture()
    f.area_way(10, (-123.02, 45.0, -123.00, 45.01), leisure="nature_reserve", name="West Woods")
    f.area_way(16, (-123.018, 45.002, -123.014, 45.006), tourism="zoo", name="Test Zoo")
    osm_raw = src.osm_source(cfg, paths)
    assert osm_raw is not None
    f.write(osm_raw.path)
    _store_file(osm_raw)

    result = run_process(cfg, paths)
    unit = result.frames["parks"].iloc[0]
    assert unit["subtracted_m2"] > 0
    assert len(unit.geometry.geoms[0].interiors) == 1  # the zoo is now a hole
    assert result.stats["parks"]["subtraction"]["units_cut"] == 1
    assert failed(run_validation(cfg, result.frames, result.stats)) == []


def _with_closed(osm: str, name: str) -> RegionConfig:
    data = parks_config(["West Woods"]).model_dump(mode="json")
    data["parks"]["closed_to_public"] = [{"osm": osm, "name": name, "source": "test"}]
    return RegionConfig.model_validate(data)


def test_closed_park_is_excluded_with_reason(tmp_path: Path) -> None:
    cfg = _with_closed("w11", "Corner Park")
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    result = run_process(cfg, paths)
    reasons = result.frames["green_candidates"].set_index("name")["reason"]
    assert reasons["Corner Park"] == "closed_to_public"
    unit = result.frames["parks"].iloc[0]
    assert unit["n_members"] == 1  # West Woods alone now
    assert failed(run_validation(cfg, result.frames, result.stats)) == []


@pytest.mark.parametrize(("osm", "name"), [("w999", "Gone Park"), ("w11", "Wrong Name")])
def test_stale_closed_entry_fails_validation(tmp_path: Path, osm: str, name: str) -> None:
    cfg = _with_closed(osm, name)
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    result = run_process(cfg, paths)
    bad = {c.name for c in failed(run_validation(cfg, result.frames, result.stats))}
    assert bad == {"parks.closed_list_matched"}


def test_closed_list_rejects_duplicates() -> None:
    data = parks_config([]).model_dump(mode="json")
    entry = {"osm": "w1", "name": "X", "source": "t"}
    data["parks"]["closed_to_public"] = [entry, entry]
    with pytest.raises(ValueError, match="twice"):
        RegionConfig.model_validate(data)
