"""Stage 7 end to end on the synthetic region: invariants, outputs, determinism, freshness."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from touchgrass.config import RegionConfig
from touchgrass.core.aggregate import CITY_ID
from touchgrass.paths import RegionPaths
from touchgrass.pipeline.analyze import run_analysis
from touchgrass.pipeline.metrics import METRICS_FILES, metrics_freshness_problems, run_metrics
from touchgrass.pipeline.network import run_network
from touchgrass.pipeline.process import MissingDataError
from touchgrass.pipeline.validate import failed

from .test_network import build_network_region

METRICS = {
    "underserved_minutes": 10,
    "min_residents_for_ranking": 15,
    "partition_area_types": ["district"],
    "indicators": [
        {
            "id": "total_population",
            "label": "Population",
            "kind": "count",
            "numerator": ["B01003_001"],
        }
    ],
}


def with_metrics(cfg: RegionConfig, **changes: object) -> RegionConfig:
    data = cfg.model_dump(mode="json")
    data["metrics"] = {**METRICS, **changes}
    return RegionConfig.model_validate(data)


@pytest.fixture
def analyzed(tmp_path: Path) -> tuple[RegionConfig, RegionPaths]:
    cfg, paths = build_network_region(tmp_path)
    run_network(cfg, paths)
    run_analysis(cfg, paths, repo=tmp_path)
    return with_metrics(cfg), paths


def test_metrics_end_to_end(analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path) -> None:
    cfg, paths = analyzed
    result = run_metrics(cfg, paths, repo=tmp_path)
    assert failed(result.checks) == []
    m = result.metrics.set_index("area_id")
    # Blocks: 10 (cell 0), 20 (cell 1, in both neighborhoods), 40 (cell 2).
    assert m.loc[CITY_ID, "pop_total"] == 70
    assert m.loc["neighborhood:1", "pop_total"] + m.loc["neighborhood:2", "pop_total"] == 90
    assert m.loc["district:1", "pop_total"] + m.loc["district:2", "pop_total"] == 70
    assert m.loc["district:1", "ranked"] == False  # noqa: E712  (10 < 15 residents)
    assert m.loc[CITY_ID, "ranked"] == False  # noqa: E712
    # The per-park search agrees with Stage 6: every block is within 10 minutes here.
    assert (result.reach["parks_reachable"] > 0).all()
    assert m.loc[CITY_ID, "underserved_pop"] == 0

    ctx = result.context.set_index(["area_id", "indicator"])
    # Neighborhood 1 = blocks of block group ...001 only (ACS 31 ± 9). Block group ...002 is
    # suppressed in the fixture (-666666666), so areas that use it are undefined, not 0.
    west = ctx.loc[("neighborhood:1", "total_population")]
    assert west["estimate"] == pytest.approx(31) and west["moe"] == pytest.approx(9)
    assert math.isnan(ctx.loc[(CITY_ID, "total_population"), "estimate"])
    assert ctx.loc[(CITY_ID, "total_population"), "reliability"] == "low"

    for name in METRICS_FILES.values():
        assert (result.run_dir / name).is_file()  # type: ignore[operator]
    report = (result.run_dir / METRICS_FILES["report"]).read_text()  # type: ignore[operator]
    assert "## 9. Caveats" in report and "Testville" in report
    assert "+20 against the city" in report  # neighborhoods overlap by block 2's 20 people


def test_partition_check_catches_a_gap(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    # Neighborhoods overlap; force the check on districts after dropping one district's blocks.
    membership = pd.read_parquet(paths.processed / "area_membership.parquet")
    membership[membership["area_id"] != "district:1"].to_parquet(
        paths.processed / "area_membership.parquet", index=False
    )
    result = run_metrics(cfg, paths, repo=tmp_path)
    assert [c.name for c in failed(result.checks)] == ["metrics.partition.district"]


def test_rerun_writes_identical_files(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    a = run_metrics(cfg, paths, repo=tmp_path)
    names = [v for k, v in METRICS_FILES.items() if k not in {"run", "validation"}]
    first = {n: (a.run_dir / n).read_bytes() for n in names}  # type: ignore[operator]
    b = run_metrics(cfg, paths, repo=tmp_path)
    assert {n: (b.run_dir / n).read_bytes() for n in names} == first  # type: ignore[operator]


def test_metrics_go_stale_when_inputs_change(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    result = run_metrics(cfg, paths, repo=tmp_path)
    assert metrics_freshness_problems(paths.processed, result.run_id) == []
    path = paths.processed / "area_membership.parquet"
    pd.read_parquet(path).iloc[:-1].to_parquet(path, index=False)
    problems = metrics_freshness_problems(paths.processed, result.run_id)
    assert problems and "area_membership.parquet" in problems[0]


def test_metrics_need_an_analysis_run(tmp_path: Path) -> None:
    cfg, paths = build_network_region(tmp_path)
    run_network(cfg, paths)
    with pytest.raises(MissingDataError, match="no analysis run"):
        run_metrics(with_metrics(cfg), paths, repo=tmp_path)
    with pytest.raises(MissingDataError, match="no 'metrics' section"):
        run_metrics(cfg, paths, repo=tmp_path)


def test_record_has_inputs_and_summary(
    analyzed: tuple[RegionConfig, RegionPaths], tmp_path: Path
) -> None:
    cfg, paths = analyzed
    result = run_metrics(cfg, paths, repo=tmp_path)
    record = json.loads((result.run_dir / METRICS_FILES["run"]).read_text())  # type: ignore[operator]
    assert {"block_access", "tiger_tabblock20", "area_membership"} <= set(record["inputs"])
    assert record["summary"]["population"] == 70
    assert record["summary"]["areas"] == {"district": 2, "neighborhood": 2}
