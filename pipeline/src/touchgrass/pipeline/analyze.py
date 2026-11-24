"""Stage 6: walking distance from every populated block to its nearest park (offline).

Inputs: the Stage 5 network outputs (must be fresh) and Stage 3 parks. Outputs go to
``data/processed/<region_id>/runs/<run_id>/`` so runs accumulate:

    block_access.parquet       one row per populated block (block point geometry, 4326)
    analysis_run.json          parameters, input checksums, code version, timing, summary
    analysis_validation.json   hard / soft checks
    analysis_routes.geojson    3 sample routes to compare with an independent router

and ``analysis_latest.json`` in the region folder points at the newest run.

The run id is a hash of (parameters, input checksums, code version): rerunning unchanged
work reproduces the same id and identical files; changing anything gives a new id.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, MultiLineString

from touchgrass.config import RegionConfig
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.core.routing import (
    SAME_EDGE,
    block_distances,
    build_routing_graph,
    nearest_access,
)
from touchgrass.paths import RegionPaths
from touchgrass.pipeline.network import (
    NETWORK_FILES,
    load_network_outputs,
    network_freshness_problems,
)
from touchgrass.pipeline.process import MissingDataError
from touchgrass.pipeline.validate import Check, _hard, _info, failed
from touchgrass.sources.http import now_utc, sha256_file

RUNS_DIR = "runs"
LATEST_FILE = "analysis_latest.json"
RUN_FILES = {
    "block_access": "block_access.parquet",
    "run": "analysis_run.json",
    "validation": "analysis_validation.json",
    "routes": "analysis_routes.geojson",
}
DETOUR_MIN_STRAIGHT_M = 50.0  # below this, network/straight ratios are noise
ORACLE_SAMPLE = 50
ENGINE = "scipy.sparse.csgraph.dijkstra(min_only=True)"


@dataclass
class AnalysisResult:
    run_id: str
    block_access: gpd.GeoDataFrame
    record: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    run_dir: Path | None = None


# ----------------------------------------------------------------------- provenance


def code_version(repo: Path) -> dict[str, Any]:
    """Git commit of the pipeline code, plus a hash of any uncommitted changes to it."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        ).stdout

    try:
        commit = git("rev-parse", "HEAD").strip()
        diff = git("diff", "HEAD", "--", "pipeline/src")
        untracked = git("ls-files", "--others", "--exclude-standard", "--", "pipeline/src")
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None, "diff_sha256": None}
    dirty = bool(diff or untracked)
    blob = (
        diff
        + untracked
        + "".join((repo / f).read_text("utf-8", errors="replace") for f in untracked.split() if f)
    )
    return {
        "commit": commit,
        "dirty": dirty,
        "diff_sha256": hashlib.sha256(blob.encode()).hexdigest() if dirty else None,
    }


def _params(cfg: RegionConfig) -> dict[str, Any]:
    assert cfg.network is not None
    return {
        "walk_speed_kmh": cfg.analysis.walk_speed_kmh,
        "thresholds_min": cfg.analysis.thresholds_min,
        "threshold_distances_m": {str(k): v for k, v in cfg.analysis.threshold_distances_m.items()},
        "access_tolerance_m": cfg.network.access_tolerance_m,
        "max_park_gap_m": cfg.network.max_park_gap_m,
        "engine": ENGINE,
        "cutoff_m": None,
        "same_edge_rule": True,
        "straight_line_target": "nearest park polygon",
        "detour_min_straight_m": DETOUR_MIN_STRAIGHT_M,
    }


def make_run_id(params: dict[str, Any], inputs: dict[str, str], code: dict[str, Any]) -> str:
    blob = json.dumps({"params": params, "inputs": inputs, "code": code}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ------------------------------------------------------------------------- analysis


def straight_to_parks(points: gpd.GeoSeries, parks: gpd.GeoDataFrame) -> pd.DataFrame:
    """Euclidean distance (analysis CRS) from each point to the nearest park polygon."""
    geoms = np.asarray(parks.geometry.values)
    pts = np.asarray(points.values)
    tree = shapely.STRtree(geoms)
    p_idx, g_idx = tree.query_nearest(pts, all_matches=True)
    best = pd.DataFrame({"p": p_idx, "g": g_idx}).groupby("p")["g"].min()
    chosen = best.reindex(range(len(pts))).to_numpy()
    return pd.DataFrame(
        {
            "straight_m": shapely.distance(pts, geoms[chosen]),
            "straight_park_id": parks["park_id"].to_numpy()[chosen],
        },
        index=points.index,
    )


def oracle_max_diff(
    edges: pd.DataFrame, access: pd.DataFrame, node_dist: pd.Series, sample: np.ndarray
) -> float:
    """Recompute node distances with NetworkX (independent code) and compare on a sample."""
    g = nx.MultiGraph()
    g.add_weighted_edges_from(zip(edges["u"], edges["v"], edges["length_m"], strict=True))
    source = "virtual-source"
    for u, v, ou, ov in zip(
        access["u"], access["v"], access["offset_u_m"], access["offset_v_m"], strict=True
    ):
        g.add_edge(source, u, weight=ou)
        g.add_edge(source, v, weight=ov)
    nx_dist = nx.single_source_dijkstra_path_length(g, source)
    ours = node_dist.reindex(sample).to_numpy()
    theirs = np.array([nx_dist.get(n, np.inf) for n in sample])
    both_inf = np.isinf(ours) & np.isinf(theirs)
    diff = np.where(both_inf, 0.0, np.abs(ours - theirs))
    return float(np.nanmax(diff)) if len(diff) else 0.0


def run_analysis(
    cfg: RegionConfig,
    paths: RegionPaths,
    repo: Path,
    *,
    progress: Callable[[str], None] = lambda _m: None,
) -> AnalysisResult:
    if cfg.network is None:
        raise MissingDataError(f"{cfg.region_id}: no 'network' section; Stage 6 needs Stage 5")
    out_dir = paths.processed
    stale = network_freshness_problems(out_dir)
    network = load_network_outputs(out_dir)
    if network is None or stale:
        raise MissingDataError(
            "network outputs missing or stale "
            f"({'; '.join(stale) or 'missing'}); run `touchgrass network` first"
        )
    parks = gpd.read_parquet(out_dir / "parks.parquet")
    blocks = gpd.read_parquet(out_dir / "blocks.parquet").set_index("geoid20")
    epsg = cfg.analysis_crs
    speed = cfg.analysis.meters_per_minute

    params = _params(cfg)
    inputs = {
        name: sha256_file(out_dir / f)
        for name, f in {**NETWORK_FILES, "parks": "parks.parquet"}.items()
        if name != "network_nodes"
    }
    code = code_version(repo)
    run_id = make_run_id(params, inputs, code)
    t0 = time.perf_counter()

    edges = pd.DataFrame(network["network_edges"].drop(columns="geometry"))
    access = pd.DataFrame(network["park_access_points"].drop(columns="geometry"))
    origins = pd.DataFrame(network["block_origins"].drop(columns="geometry"))

    progress(f"shortest paths from {len(access):,} access points ({len(edges):,} edges)")
    graph = build_routing_graph(edges, access)
    t_search = time.perf_counter()
    node_dist, node_access = nearest_access(graph)
    search_s = time.perf_counter() - t_search
    dist = block_distances(origins, access, node_dist, node_access)

    progress("straight-line distances and invariants")
    pts = gpd.GeoSeries(
        gpd.points_from_xy(
            blocks.loc[origins["geoid20"], "rep_lon"], blocks.loc[origins["geoid20"], "rep_lat"]
        ),
        crs=STORAGE_EPSG,
    )
    straight = straight_to_parks(pts.to_crs(epsg), parks.to_crs(epsg))
    park_of_access = access.set_index("access_id")["park_id"]
    reachable = np.isfinite(dist["network_m"].to_numpy())
    network_m = np.where(reachable, dist["network_m"], np.nan)
    straight_m = straight["straight_m"].to_numpy()
    usable = reachable & (straight_m > DETOUR_MIN_STRAIGHT_M)
    ratio = np.divide(network_m, straight_m, out=np.full(len(straight_m), np.nan), where=usable)
    ba = gpd.GeoDataFrame(
        {
            "run_id": run_id,
            "geoid20": origins["geoid20"].to_numpy(),
            "pop20": origins["pop20"].to_numpy(),
            "snap_status": origins["snap_status"].to_numpy(),
            "reachable": reachable,
            "network_m": network_m,
            "walk_min": network_m / speed,
            "straight_m": straight_m,
            "straight_park_id": straight["straight_park_id"].to_numpy(),
            "detour_ratio": ratio,
            "nearest_park_id": dist["nearest_access_id"].map(park_of_access).to_numpy(),
            "nearest_access_id": dist["nearest_access_id"].to_numpy(),
            "route_kind": dist["route_kind"].to_numpy(),
        },
        geometry=list(pts),
        crs=STORAGE_EPSG,
    ).sort_values("geoid20", ignore_index=True)

    rng = np.random.default_rng(0)  # fixed seed: the same sample on every run
    candidates = np.unique(np.r_[origins["u"], origins["v"]])
    sample_nodes = rng.choice(candidates, size=min(ORACLE_SAMPLE, len(candidates)), replace=False)
    oracle = oracle_max_diff(edges, access, node_dist, sample_nodes)

    summary = summarize(ba, cfg)
    record = {
        "run_id": run_id,
        "region_id": cfg.region_id,
        "generated_at": now_utc(),
        "params": params,
        "inputs": inputs,
        "code": code,
        "seconds": {"total": round(time.perf_counter() - t0, 2), "search": round(search_s, 3)},
        "counts": {
            "origins": len(ba),
            "access_points": len(access),
            "graph_nodes": graph.n_graph,
            "edges": len(edges),
        },
        "summary": summary,
    }
    checks = analysis_checks(ba, cfg, oracle)
    routes = sample_routes(ba, network, access, node_dist, cfg, epsg)
    run_dir = _write(out_dir, run_id, ba, record, checks, routes)
    return AnalysisResult(run_id, ba, record, checks, run_dir)


def _weighted_share(pop: np.ndarray, mask: np.ndarray) -> float:
    return round(float(pop[mask].sum() / pop.sum()), 4) if pop.sum() else 0.0


def summarize(ba: gpd.GeoDataFrame, cfg: RegionConfig) -> dict[str, Any]:
    pop = ba["pop20"].to_numpy()
    net = ba["network_m"].to_numpy()
    straight = ba["straight_m"].to_numpy()
    within = {
        str(t): {
            "network": _weighted_share(pop, np.nan_to_num(net, nan=np.inf) <= d),
            "straight_line": _weighted_share(pop, straight <= d),
        }
        for t, d in cfg.analysis.threshold_distances_m.items()
    }
    ratio = ba["detour_ratio"].dropna().to_numpy()
    reach = ba["reachable"].to_numpy()
    order = np.argsort(net[reach])
    cum = np.cumsum(pop[reach][order])
    weighted_median = (
        float(net[reach][order][np.searchsorted(cum, cum[-1] / 2)]) if len(cum) else None
    )
    return {
        "population": int(pop.sum()),
        "share_within_minutes": within,
        "network_m": {
            "median_block": round(float(np.nanmedian(net)), 1),
            "median_population_weighted": round(weighted_median, 1) if weighted_median else None,
            "p90_block": round(float(np.nanpercentile(net, 90)), 1),
            "max": round(float(np.nanmax(net)), 1),
        },
        "detour_ratio": {
            "blocks": len(ratio),
            "median": round(float(np.median(ratio)), 3),
            "p90": round(float(np.percentile(ratio, 90)), 3),
            "p99": round(float(np.percentile(ratio, 99)), 3),
        },
        "route_kind": ba["route_kind"].value_counts().to_dict(),
        "unreachable": [
            {"geoid20": g, "pop20": int(p), "snap_status": s}
            for g, p, s in ba.loc[~ba["reachable"], ["geoid20", "pop20", "snap_status"]].itertuples(
                index=False
            )
        ],
    }


def analysis_checks(ba: gpd.GeoDataFrame, cfg: RegionConfig, oracle_diff: float) -> list[Check]:
    assert cfg.network is not None
    # Slack: contact tolerance, plus up to the same again where an entrance node is
    # snapped onto its edge (D-021).
    slack = 2 * cfg.network.access_tolerance_m
    reach = ba[ba["reachable"]]
    short = reach[reach["network_m"] < reach["straight_m"] - slack - 1e-6]
    unexplained = ba[~ba["reachable"] & (ba["snap_status"] != "isolated")]
    time_ok = np.allclose(
        reach["walk_min"] * cfg.analysis.meters_per_minute, reach["network_m"], rtol=1e-9
    )
    return [
        _hard("analysis.every_origin_has_a_row", ba["geoid20"].is_unique, actual=len(ba)),
        _hard(
            "analysis.network_not_shorter_than_straight",
            short.empty,
            f"network_m >= straight_m - {slack:g} m",
            len(short),
            worst=short.assign(by=short["straight_m"] - short["network_m"])
            .nlargest(5, "by")[["geoid20", "network_m", "straight_m"]]
            .round(1)
            .to_dict("records"),
        ),
        _hard(
            "analysis.unreachable_explained",
            unexplained.empty,
            "only 'isolated' origins are unreachable",
            unexplained["geoid20"].tolist(),
        ),
        _hard("analysis.time_matches_distance", bool(time_ok)),
        _hard(
            "analysis.oracle_networkx_agrees",
            oracle_diff < 1e-6,
            "< 1e-6 m on a fixed random sample of nodes",
            oracle_diff,
        ),
        _info("analysis.unreachable", int((~ba["reachable"]).sum())),
    ]


# ------------------------------------------------------------------ sample routes


def _pick_samples(ba: gpd.GeoDataFrame, cfg: RegionConfig) -> dict[str, str]:
    """Contrasting blocks to check by hand: three typical (≥ 20 residents) + the longest walk."""
    c = ba[ba["reachable"] & (ba["pop20"] >= 20)].copy()
    lon, lat = c.geometry.x, c.geometry.y
    cx, cy = cfg.center_lonlat
    picks: dict[str, str] = {}
    picks["near_region_center"] = c.loc[((lon - cx) ** 2 + (lat - cy) ** 2).idxmin(), "geoid20"]
    east = c[lon >= lon.quantile(0.9)]
    picks["east_near_10_min"] = east.loc[
        (east["network_m"] - max(cfg.analysis.threshold_distances_m.values()) * 2 / 3)
        .abs()
        .idxmin(),
        "geoid20",
    ]
    detour = c.dropna(subset=["detour_ratio"])
    picks["largest_detour"] = detour.loc[detour["detour_ratio"].idxmax(), "geoid20"]
    # The single longest walk, any population: the most likely place for a network error.
    reach = ba[ba["reachable"]]
    picks["longest_walk"] = reach.loc[reach["network_m"].idxmax(), "geoid20"]
    return picks


def sample_routes(
    ba: gpd.GeoDataFrame,
    network: dict[str, Any],
    access: pd.DataFrame,
    node_dist: pd.Series,
    cfg: RegionConfig,
    epsg: int,
) -> gpd.GeoDataFrame:
    """Reconstruct the route for 3 sample blocks: block point, path edges, park access point."""
    edges = network["network_edges"].to_crs(epsg)
    origins = network["block_origins"].set_index("geoid20")
    points = network["park_access_points"].set_index("access_id").to_crs(epsg)
    g = nx.Graph()
    best = edges.sort_values("length_m").drop_duplicates(["u", "v"])
    for u, v, w, geom in zip(best["u"], best["v"], best["length_m"], best.geometry, strict=True):
        g.add_edge(u, v, weight=w, geom=geom)
    rows = []
    for label, geoid in _pick_samples(ba, cfg).items():
        b = ba.set_index("geoid20").loc[geoid]
        o = origins.loc[geoid]
        acc = access.set_index("access_id").loc[b["nearest_access_id"]]
        lines = []
        if b["route_kind"] != SAME_EDGE:
            via_u = o["offset_u_m"] + node_dist[o["u"]]
            start = o["u"] if via_u <= o["offset_v_m"] + node_dist[o["v"]] else o["v"]
            # Attach the destination access point temporarily and ask for the path to it.
            target = "access-point"
            g.add_edge(acc["u"], target, weight=acc["offset_u_m"])
            g.add_edge(acc["v"], target, weight=acc["offset_v_m"])
            try:
                path = nx.dijkstra_path(g, start, target, weight="weight")[:-1]
                lines = [g.edges[a, z]["geom"] for a, z in itertools.pairwise(path)]
            except nx.NetworkXNoPath:
                lines = []
            g.remove_node(target)
        rows.append(
            {
                "layer": "route",
                "sample": label,
                "geoid20": geoid,
                "network_m": round(float(b["network_m"]), 1),
                "walk_min": round(float(b["walk_min"]), 1),
                "straight_m": round(float(b["straight_m"]), 1),
                "nearest_park_id": b["nearest_park_id"],
                "geometry": MultiLineString(lines) if lines else LineString(),
            }
        )
        block_pt = gpd.GeoSeries([b.geometry], crs=STORAGE_EPSG).to_crs(epsg).iloc[0]
        rows.append(
            {"layer": "from_block", "sample": label, "geoid20": geoid, "geometry": block_pt}
        )
        rows.append(
            {
                "layer": "to_park_access",
                "sample": label,
                "geoid20": geoid,
                "nearest_park_id": b["nearest_park_id"],
                "geometry": points.geometry[b["nearest_access_id"]],
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=epsg).to_crs(STORAGE_EPSG)


# ------------------------------------------------------------------------ writing


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    raise TypeError(f"not JSON serializable: {type(value)}")


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=_json_default, allow_nan=False) + "\n"


def _write(
    out_dir: Path,
    run_id: str,
    ba: gpd.GeoDataFrame,
    record: dict[str, Any],
    checks: list[Check],
    routes: gpd.GeoDataFrame,
) -> Path:
    run_dir = out_dir / RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    ba.to_parquet(run_dir / RUN_FILES["block_access"], index=False)
    (run_dir / RUN_FILES["run"]).write_text(_dump(record), "utf-8")
    hard = [c for c in checks if c.severity == "hard"]
    validation = {
        "run_id": run_id,
        "summary": {
            "hard_passed": sum(1 for c in hard if c.passed),
            "hard_failed": len(failed(checks)),
        },
        "checks": [asdict(c) for c in checks],
    }
    (run_dir / RUN_FILES["validation"]).write_text(_dump(validation), "utf-8")
    routes_path = run_dir / RUN_FILES["routes"]
    routes_path.unlink(missing_ok=True)
    routes.to_file(routes_path, driver="GeoJSON")
    (out_dir / LATEST_FILE).write_text(_dump({"run_id": run_id}), "utf-8")
    return run_dir


def list_runs(out_dir: Path) -> list[Path]:
    root = out_dir / RUNS_DIR
    return (
        sorted(p for p in root.iterdir() if (p / RUN_FILES["run"]).is_file())
        if root.is_dir()
        else []
    )


def latest_run_id(out_dir: Path) -> str | None:
    path = out_dir / LATEST_FILE
    return json.loads(path.read_text("utf-8"))["run_id"] if path.is_file() else None


def run_freshness_problems(out_dir: Path, run_id: str) -> list[str]:
    """Reasons a run no longer matches the current network/park files."""
    record = json.loads((out_dir / RUNS_DIR / run_id / RUN_FILES["run"]).read_text("utf-8"))
    problems = []
    for name, digest in record["inputs"].items():
        f = {**NETWORK_FILES, "parks": "parks.parquet"}[name]
        if sha256_file(out_dir / f) != digest:
            problems.append(f"{f} changed since run {run_id}")
    validation = json.loads(
        (out_dir / RUNS_DIR / run_id / RUN_FILES["validation"]).read_text("utf-8")
    )
    if validation["summary"]["hard_failed"]:
        problems.append(f"run {run_id} has {validation['summary']['hard_failed']} failed check(s)")
    return problems
