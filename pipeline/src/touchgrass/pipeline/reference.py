"""A committed reference for a region's results, and the comparison against it (D-026 Q6).

``results/<region_id>/reference.json`` records, for the latest run:

* ``sources``: every raw file's checksum from its fetch manifest, and whether the source is
  **dated** (an OSM snapshot or a Census vintage: the bytes can't change) or **live** (an
  ArcGIS layer its owner can edit at any time);
* ``figures``: the headline results, rounded so that only real changes show;
* ``runs``: the run id and the commit it was computed on (for information: the run id hashes
  the commit too, so it differs on any later commit even when every figure matches).

Comparing a fresh run against it gives three outcomes:

* everything matches: the results are reproduced;
* figures differ **and** a source's checksum differs: explained, the comparison names the
  source(s) that changed (expected for live layers);
* figures differ while every source matches: **unexplained**, a real reproducibility failure.

The file has no timestamps, so regenerating it from the same results gives the same bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from touchgrass.paths import RegionPaths
from touchgrass.pipeline.analyze import RUN_FILES, RUNS_DIR, latest_run_id
from touchgrass.pipeline.metrics import METRICS_FILES
from touchgrass.pipeline.process import MissingDataError

REFERENCE_FILE = "reference.json"
FORMAT_VERSION = 1


def reference_path(repo: Path, region_id: str) -> Path:
    return repo / "results" / region_id / REFERENCE_FILE


def _r(x: float, digits: int = 4) -> float:
    return round(float(x), digits)


def source_checksums(paths: RegionPaths) -> dict[str, dict[str, Any]]:
    """``{source_id: {file, sha256, kind}}`` from every manifest under the region's raw folder."""
    out: dict[str, dict[str, Any]] = {}
    for mpath in sorted(paths.raw.rglob("*.manifest.json")):
        m = json.loads(mpath.read_text("utf-8"))
        out[m["source_id"]] = {
            "file": m["stored_file"],
            "sha256": m["stored_sha256"],
            "kind": "dated" if m.get("vintage") else "live",
        }
    return dict(sorted(out.items()))


def headline_figures(analysis: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    a, s = analysis["summary"], metrics["summary"]
    figures: dict[str, Any] = {
        "population": int(s["population"]),
        "underserved_residents": int(s["underserved_pop"]),
        "areas": dict(sorted((k, int(v)) for k, v in s["areas"].items())),
        "ranked_areas": int(s["ranked_areas"]),
        "share_within": {
            str(m): {"walking": _r(v["network"]), "straight_line": _r(v["straight_line"])}
            for m, v in sorted(a["share_within_minutes"].items(), key=lambda kv: int(kv[0]))
        },
        "median_walk_m_population_weighted": _r(a["network_m"]["median_population_weighted"], 1),
        "median_detour_ratio": _r(a["detour_ratio"]["median"], 3),
        "unreachable_blocks": len(a["unreachable"]),
    }
    comparison = s.get("resident_comparison")
    if comparison:
        figures["resident_comparison"] = {
            i["indicator"]: {
                "beyond": _r(i["beyond"]["estimate"]),
                "within": _r(i["within"]["estimate"]),
                "difference": _r(i["difference"]),
                "difference_moe": _r(i["difference_moe"]),
                "clear": bool(i["clear"]),
            }
            for i in sorted(comparison["indicators"], key=lambda i: str(i["indicator"]))
        }
    return figures


def build_reference(paths: RegionPaths) -> dict[str, Any]:
    """The reference for the region's latest run (analysis + metrics must both exist)."""
    run_id = latest_run_id(paths.processed)
    if run_id is None:
        raise MissingDataError(f"no analysis run for {paths.region_id}: run `touchgrass analyze`")
    run_dir = paths.processed / RUNS_DIR / run_id
    metrics_file = run_dir / METRICS_FILES["run"]
    if not metrics_file.is_file():
        raise MissingDataError(f"run {run_id} has no metrics: run `touchgrass metrics`")
    analysis = json.loads((run_dir / RUN_FILES["run"]).read_text("utf-8"))
    metrics = json.loads(metrics_file.read_text("utf-8"))
    return {
        "format": FORMAT_VERSION,
        "region_id": paths.region_id,
        "sources": source_checksums(paths),
        "figures": headline_figures(analysis, metrics),
        "runs": {
            "run_id": run_id,
            "analysis_commit": analysis["code"]["commit"],
            "analysis_dirty": analysis["code"]["dirty"],
            "metrics_commit": metrics["code"]["commit"],
            "metrics_dirty": metrics["code"]["dirty"],
        },
    }


def dump(reference: dict[str, Any]) -> str:
    return json.dumps(reference, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


@dataclass
class Comparison:
    changed_sources: list[str] = field(default_factory=list)  # "id (live|dated)"
    changed_figures: list[str] = field(default_factory=list)  # "key: was → now"
    run_note: str | None = None

    @property
    def reproduced(self) -> bool:
        return not self.changed_sources and not self.changed_figures

    @property
    def unexplained(self) -> bool:
        """Figures changed although every source is byte-identical."""
        return bool(self.changed_figures) and not self.changed_sources


def compare(reference: dict[str, Any], current: dict[str, Any]) -> Comparison:
    c = Comparison()
    ref_src, cur_src = reference["sources"], current["sources"]
    for sid in sorted(set(ref_src) | set(cur_src)):
        r, n = ref_src.get(sid), cur_src.get(sid)
        if r is None or n is None or r["sha256"] != n["sha256"]:
            kind = (r or n or {}).get("kind", "?")
            state = "missing" if n is None else "new" if r is None else "changed"
            c.changed_sources.append(f"{sid} ({kind}, {state})")
    ref_f, cur_f = _flatten(reference["figures"]), _flatten(current["figures"])
    for key in sorted(set(ref_f) | set(cur_f)):
        if ref_f.get(key) != cur_f.get(key):
            c.changed_figures.append(f"{key}: {ref_f.get(key)} → {cur_f.get(key)}")
    if reference["runs"]["run_id"] != current["runs"]["run_id"]:
        c.run_note = (
            f"run id {current['runs']['run_id']} differs from the reference "
            f"{reference['runs']['run_id']}: expected on a different commit, "
            "since the run id hashes the code version too"
        )
    return c
