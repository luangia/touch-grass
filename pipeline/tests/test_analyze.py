"""Stage 6 end to end on the synthetic region: run identity, invariants, outputs."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest

from touchgrass.config import RegionConfig
from touchgrass.paths import RegionPaths
from touchgrass.pipeline.analyze import (
    LATEST_FILE,
    RUN_FILES,
    latest_run_id,
    list_runs,
    run_analysis,
    run_freshness_problems,
)
from touchgrass.pipeline.network import run_network
from touchgrass.pipeline.process import MissingDataError
from touchgrass.pipeline.validate import failed

from .test_network import build_network_region


@pytest.fixture
def analyzed(tmp_path: Path) -> tuple[RegionConfig, RegionPaths]:
    cfg, paths = build_network_region(tmp_path)
    run_network(cfg, paths)
    return cfg, paths


def test_analysis_end_to_end(analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path) -> None:
    cfg, paths = analyzed
    result = run_analysis(cfg, paths, repo=tmp_path)  # not a git repo: code version unknown
    assert failed(result.checks) == []
    ba = result.block_access
    assert len(ba) == 3 and ba["reachable"].all()
    # Every block point is ~55.6 m north of H1, which runs through West Woods: the blocks
    # in cells 0-1 reach it straight away; the third (cell 2) walks east-west to it.
    assert (ba["network_m"] >= ba["straight_m"] - 30).all()
    assert ba["walk_min"].tolist() == pytest.approx((ba["network_m"] / 80).tolist())
    assert set(ba["nearest_park_id"]) == {"osm:w10"}
    assert result.record["code"] == {"commit": None, "dirty": None, "diff_sha256": None}
    for name in RUN_FILES.values():
        assert (paths.processed / "runs" / result.run_id / name).is_file()
    assert latest_run_id(paths.processed) == result.run_id
    json.loads((paths.processed / LATEST_FILE).read_text())


def test_rerun_reproduces_id_and_rows(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    a = run_analysis(cfg, paths, repo=tmp_path)
    first = (a.run_dir / RUN_FILES["block_access"]).read_bytes()  # type: ignore[operator]
    b = run_analysis(cfg, paths, repo=tmp_path)
    assert b.run_id == a.run_id
    assert (b.run_dir / RUN_FILES["block_access"]).read_bytes() == first  # type: ignore[operator]
    assert len(list_runs(paths.processed)) == 1


def test_changed_parameter_gives_a_new_run(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    a = run_analysis(cfg, paths, repo=tmp_path)
    data = cfg.model_dump(mode="json")
    data["analysis"]["walk_speed_kmh"] = 4.0
    slower = run_analysis(RegionConfig.model_validate(data), paths, repo=tmp_path)
    assert slower.run_id != a.run_id and len(list_runs(paths.processed)) == 2
    assert latest_run_id(paths.processed) == slower.run_id
    assert (slower.block_access["walk_min"] > a.block_access["walk_min"]).all()


def test_analysis_refuses_stale_network(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    parks = gpd.read_parquet(paths.processed / "parks.parquet")
    parks.to_parquet(paths.processed / "parks.parquet", index=False, compression="gzip")
    with pytest.raises(MissingDataError, match="stale"):
        run_analysis(cfg, paths, repo=tmp_path)


def test_run_goes_stale_when_inputs_change(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    run = run_analysis(cfg, paths, repo=tmp_path)
    assert run_freshness_problems(paths.processed, run.run_id) == []
    run_network(cfg, paths)  # same inputs → identical files → still fresh
    assert run_freshness_problems(paths.processed, run.run_id) == []
    parks = gpd.read_parquet(paths.processed / "parks.parquet")
    parks.to_parquet(paths.processed / "parks.parquet", index=False, compression="gzip")
    assert run_freshness_problems(paths.processed, run.run_id) == [
        f"parks.parquet changed since run {run.run_id}"
    ]
