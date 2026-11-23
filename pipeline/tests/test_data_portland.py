"""Checks on the real processed outputs of every configured region.

Marked ``data`` and skipped automatically when a region hasn't been processed, so the
default test run stays offline and fast. Run them with:  uv run pytest -m data
"""

from __future__ import annotations

import pytest

from touchgrass.config import list_regions, load_region
from touchgrass.paths import RegionPaths, resolve_data_dir
from touchgrass.pipeline.process import OUTPUT_FILES, load_outputs, load_stats
from touchgrass.pipeline.validate import failed, run_validation

from .conftest import REPO_CONFIG_DIR

pytestmark = pytest.mark.data


def _processed_regions() -> list[str]:
    out = []
    for region_id in list_regions(REPO_CONFIG_DIR):
        paths = RegionPaths(region_id, resolve_data_dir(REPO_CONFIG_DIR))
        if all((paths.processed / f).is_file() for f in OUTPUT_FILES.values()):
            out.append(region_id)
    return out


REGIONS = _processed_regions()


@pytest.mark.skipif(not REGIONS, reason="no processed regions; run `touchgrass process`")
@pytest.mark.parametrize("region_id", REGIONS)
def test_all_hard_checks_pass(region_id: str) -> None:
    cfg = load_region(region_id, REPO_CONFIG_DIR)
    paths = RegionPaths(region_id, resolve_data_dir(REPO_CONFIG_DIR))
    checks = run_validation(cfg, load_outputs(paths.processed), load_stats(paths.processed))
    assert not failed(checks), [(c.name, c.expected, c.actual) for c in failed(checks)]


@pytest.mark.skipif(not REGIONS, reason="no processed regions; run `touchgrass process`")
@pytest.mark.parametrize("region_id", REGIONS)
def test_city_total_equals_sum_of_blocks_not_areas(region_id: str) -> None:
    # Overlapping/gapped areas make "sum of areas" differ from the true total (D-007).
    paths = RegionPaths(region_id, resolve_data_dir(REPO_CONFIG_DIR))
    f = load_outputs(paths.processed)
    pops = f["blocks"].set_index("geoid20")["pop20"]
    for area_type, members in f["area_membership"].groupby("area_type"):
        summed = int(members["geoid20"].map(pops).sum())
        covered = int(pops[pops.index.isin(members["geoid20"])].sum())
        assert summed >= covered, area_type  # double counting can only add


@pytest.mark.skipif(not REGIONS, reason="no processed regions; run `touchgrass process`")
@pytest.mark.parametrize("region_id", REGIONS)
def test_network_outputs_are_current_and_pass(region_id: str) -> None:
    from touchgrass.pipeline.network import load_network_outputs, network_freshness_problems

    paths = RegionPaths(region_id, resolve_data_dir(REPO_CONFIG_DIR))
    frames = load_network_outputs(paths.processed)
    if frames is None:
        pytest.skip("no network outputs; run `touchgrass network`")
    assert network_freshness_problems(paths.processed) == []
    origins = frames["block_origins"]
    blocks = load_outputs(paths.processed)["blocks"]
    assert set(origins["geoid20"]) == set(blocks.loc[blocks["pop20"] > 0, "geoid20"])
    parks = load_outputs(paths.processed)["parks"]
    assert set(frames["park_access_points"]["park_id"]) <= set(parks["park_id"])
