"""The committed results reference (D-026 Q6): building, determinism, and the comparison."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from touchgrass.paths import RegionPaths, resolve_data_dir
from touchgrass.pipeline import reference as ref
from touchgrass.pipeline.analyze import run_analysis
from touchgrass.pipeline.metrics import run_metrics
from touchgrass.pipeline.network import run_network
from touchgrass.pipeline.process import MissingDataError

from .conftest import REPO_CONFIG_DIR
from .test_metrics import with_metrics
from .test_network import build_network_region


@pytest.fixture
def synthetic(tmp_path: Path) -> RegionPaths:
    cfg, paths = build_network_region(tmp_path)
    run_network(cfg, paths)
    run_analysis(cfg, paths, repo=tmp_path)
    run_metrics(with_metrics(cfg), paths, repo=tmp_path)
    return paths


def test_reference_records_sources_figures_and_runs(synthetic: RegionPaths) -> None:
    r = ref.build_reference(synthetic)
    assert r["region_id"] == synthetic.region_id
    assert r["sources"], "every raw manifest is listed"
    assert {s["kind"] for s in r["sources"].values()} <= {"dated", "live"}
    assert r["figures"]["population"] == 70
    assert set(r["figures"]["share_within"]) == {"5", "10", "15"}
    assert r["runs"]["run_id"]


def test_reference_is_deterministic(synthetic: RegionPaths) -> None:
    # No timestamps: regenerating from the same results gives the same bytes.
    assert ref.dump(ref.build_reference(synthetic)) == ref.dump(ref.build_reference(synthetic))


def test_missing_run_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MissingDataError, match="touchgrass analyze"):
        ref.build_reference(RegionPaths("nowhere-zz", tmp_path))


def test_comparison_outcomes(synthetic: RegionPaths) -> None:
    base = ref.build_reference(synthetic)
    same = ref.compare(base, copy.deepcopy(base))
    assert same.reproduced and not same.unexplained and same.run_note is None

    # A changed figure with identical sources is a real reproducibility failure.
    drift = copy.deepcopy(base)
    drift["figures"]["underserved_residents"] += 1
    c = ref.compare(base, drift)
    assert c.unexplained
    assert c.changed_figures == [
        f"underserved_residents: {base['figures']['underserved_residents']} → "
        f"{base['figures']['underserved_residents'] + 1}"
    ]

    # The same change with a live source edited upstream is explained, and names the source.
    sid = next(iter(base["sources"]))
    drift["sources"][sid]["sha256"] = "0" * 64
    c = ref.compare(base, drift)
    assert not c.unexplained and not c.reproduced
    assert c.changed_sources == [f"{sid} ({base['sources'][sid]['kind']}, changed)"]

    # A different commit changes the run id only: noted, not a failure.
    later = copy.deepcopy(base)
    later["runs"]["run_id"] = "ffffffffffffffff"
    c = ref.compare(base, later)
    assert c.reproduced and c.run_note is not None


# --------------------------------------------------------- the real, committed reference

REPO = REPO_CONFIG_DIR.parent


def _referenced_regions() -> list[str]:
    out = []
    for path in sorted((REPO / "results").glob(f"*/{ref.REFERENCE_FILE}")):
        paths = RegionPaths(path.parent.name, resolve_data_dir(REPO_CONFIG_DIR))
        try:
            ref.build_reference(paths)
        except (MissingDataError, FileNotFoundError):
            continue
        out.append(path.parent.name)
    return out


REFERENCED = _referenced_regions()


@pytest.mark.data
@pytest.mark.skipif(not REFERENCED, reason="no region has both a reference and a metrics run")
@pytest.mark.parametrize("region_id", REFERENCED)
def test_latest_run_matches_committed_reference(region_id: str) -> None:
    committed = json.loads(ref.reference_path(REPO, region_id).read_text("utf-8"))
    current = ref.build_reference(RegionPaths(region_id, resolve_data_dir(REPO_CONFIG_DIR)))
    c = ref.compare(committed, current)
    assert not c.unexplained, c.changed_figures
    assert c.reproduced, {"sources": c.changed_sources, "figures": c.changed_figures}
