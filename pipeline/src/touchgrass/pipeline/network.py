"""Stage 5: walk network, block origins and park access points (offline).

Reads the cached OSM extract (verified against its manifest) and the Stage 2-3 outputs,
and writes to ``data/processed/<region_id>/`` (geometry stored in EPSG:4326, D-008):

    network_nodes.parquet       node_id, component, street_count           main component
    network_edges.parquet       edge_id, u, v, key, length_m, highway, ...  main component
    block_origins.parquet       populated blocks snapped onto the main network
    park_access_points.parquet  where the network reaches each park
    network_report.json         filter, connectivity, snapping and access statistics
    network_validation.json     hard / soft checks
    network_review.geojson      things to look at on a map (flagged blocks, parks without
                                access, sentinel access points, stranded components)

Every output is sorted by its key, so the same inputs give identical files.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from touchgrass.config import RegionConfig
from touchgrass.config.models import NetworkConfig
from touchgrass.core.access import NO_CONTACT, park_access_points
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.core.network import build_walk_graph, length_agreement
from touchgrass.core.snap import snap_to_edges, snap_to_edges_dry, water_crossing_m
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.pipeline.process import MissingDataError, _require
from touchgrass.pipeline.validate import Check, _hard, _info, failed
from touchgrass.sources import osm
from touchgrass.sources.http import Manifest, manifest_path, now_utc, sha256_file

NETWORK_FILES = {
    "network_nodes": "network_nodes.parquet",
    "network_edges": "network_edges.parquet",
    "block_origins": "block_origins.parquet",
    "park_access_points": "park_access_points.parquet",
}
REPORT_FILE = "network_report.json"
VALIDATION_FILE = "network_validation.json"
REVIEW_FILE = "network_review.geojson"
NEAR_M = 100.0  # "within 100 m of the network" (acceptance threshold distance)


@dataclass
class NetworkResult:
    frames: dict[str, Any]
    report: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)


def _network_config(cfg: RegionConfig) -> NetworkConfig:
    if cfg.network is None or cfg.osm is None or cfg.parks is None:
        raise MissingDataError(f"{cfg.region_id}: config has no 'network' section (Stage 5)")
    return cfg.network


def _read(paths: RegionPaths, name: str) -> gpd.GeoDataFrame:
    path = paths.processed / f"{name}.parquet"
    if not path.is_file():
        raise MissingDataError(f"{path} missing; run `touchgrass process` first")
    return gpd.read_parquet(path)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Rows as dicts with NaN/NA as None (JSON has no NaN)."""
    return df.astype(object).where(df.notna(), None).to_dict("records")  # type: ignore[no-any-return]


def _round(x: float, n: int = 1) -> float:
    return round(float(x), n)


# block_origins.snap_status
OK, MOORAGE, ISOLATED, WET = "ok", "moorage", "isolated", "crosses_water"


def build_origins(
    blocks: gpd.GeoDataFrame,
    all_edges: gpd.GeoDataFrame,
    main_edges: gpd.GeoDataFrame,
    epsg: int,
    max_snap_m: float,
    water: BaseGeometry | None,
) -> gpd.GeoDataFrame:
    """Populated blocks' representative points snapped onto the network (D-020).

    ``snap_status``:
      ok             nearest main-network edge reachable without crossing water
      moorage        the point itself is on water (floating homes): its dock is assumed,
                     so it snaps to the nearest main edge regardless
      isolated       no dry route to the main network within ``max_snap_m``: snapped to the
                     nearest dry edge of its own piece (e.g. an island with its own streets)
      crosses_water  no dry edge at all within ``max_snap_m``; plain nearest main edge,
                     flagged for review
    """
    populated = blocks[blocks["pop20"] > 0].sort_values("geoid20", ignore_index=True)
    pts = gpd.GeoSeries(
        gpd.points_from_xy(populated["rep_lon"], populated["rep_lat"]), crs=STORAGE_EPSG
    ).to_crs(epsg)
    comp_of_edge = all_edges.set_index("edge_id")["component"]
    nearest_any = snap_to_edges(pts, all_edges)  # which piece is really closest?
    on_water = (
        shapely.intersects(np.asarray(pts.values), water)
        if water is not None
        else np.zeros(len(pts), bool)
    )
    plain = snap_to_edges(pts, main_edges)
    dry = snap_to_edges_dry(pts, main_edges, water, max_snap_m)
    cols = ["edge_id", "u", "v", "connector_m", "offset_u_m", "offset_v_m", "snapped"]
    chosen = dry[cols].copy()
    status = np.where(dry["dry"], OK, WET).astype(object)

    def take(rows: np.ndarray, source: pd.DataFrame) -> None:
        chosen.iloc[rows, :] = source[cols].to_numpy()

    on = np.flatnonzero(on_water)
    take(on, plain.iloc[on])
    status[on] = MOORAGE
    lost = np.flatnonzero(status == WET)
    if len(lost):
        own = snap_to_edges_dry(pts.iloc[lost], all_edges, water, max_snap_m)
        found = own["dry"].to_numpy()
        take(lost[found], own[found])
        status[lost[found]] = ISOLATED
        take(lost[~found], plain.iloc[lost[~found]])
    return gpd.GeoDataFrame(
        {
            "geoid20": populated["geoid20"],
            "pop20": populated["pop20"],
            "edge_id": chosen["edge_id"],
            "u": chosen["u"].astype("int64"),
            "v": chosen["v"].astype("int64"),
            "connector_m": chosen["connector_m"].astype(float),
            "offset_u_m": chosen["offset_u_m"].astype(float),
            "offset_v_m": chosen["offset_v_m"].astype(float),
            "component": chosen["edge_id"].map(comp_of_edge).to_numpy(),
            "snap_status": status,
            "nearest_component": nearest_any["edge_id"].map(comp_of_edge).to_numpy(),
            "nearest_any_m": nearest_any["connector_m"],
            "flagged": (chosen["connector_m"].astype(float) > max_snap_m) | (status == WET),
        },
        geometry=list(chosen["snapped"]),
        crs=epsg,
    )


def water_union(water: gpd.GeoDataFrame, epsg: int) -> BaseGeometry | None:
    if water.empty:
        return None
    union = shapely.union_all(np.asarray(water.to_crs(epsg).geometry.values))
    shapely.prepare(union)
    return union


def _stranded(origins: gpd.GeoDataFrame, components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    off = origins[origins["nearest_component"] != 0]
    by = off.groupby("nearest_component").agg(blocks=("pop20", "size"), population=("pop20", "sum"))
    info = {c["component"]: c for c in components}
    rows = [
        {
            **info[int(comp)],
            "blocks": int(r.blocks),
            "population": int(r.population),
            "median_extra_walk_m": _round(
                (
                    off.loc[off["nearest_component"] == comp, "connector_m"]
                    - off.loc[off["nearest_component"] == comp, "nearest_any_m"]
                ).median()
            ),
        }
        for comp, r in by.iterrows()
    ]
    return sorted(rows, key=lambda r: (-r["population"], r["component"]))


def _no_access_reasons(
    parks: gpd.GeoDataFrame,
    per_park: pd.DataFrame,
    graph: Any,
    main_edges: gpd.GeoDataFrame,
    water: BaseGeometry | None,
    tolerance: float,
) -> pd.DataFrame:
    """Why each park without access has none: the facts a reviewer needs."""
    missing = per_park.loc[per_park["status"] == NO_CONTACT, ["park_id", "name"]].copy()
    if missing.empty:
        return missing.assign(nearest_network_m=[], gap_over_water_m=[], reason=[])
    geoms = parks.set_index("park_id").geometry.loc[missing["park_id"]]
    clipped = {c["component"] for c in graph.components if c["touches_clip"] and c["component"]}
    other = graph.edges[graph.edges["component"].isin(clipped)]
    rows = []
    for park in geoms:
        j = main_edges.sindex.nearest(park)[1][0]
        line = shapely.shortest_line(park, main_edges.geometry.iloc[j])
        wet = float(water_crossing_m([line], water)[0])
        on_clipped = bool(len(other)) and bool(other.distance(park).min() <= tolerance)
        reason = (
            "network_piece_cut_by_buffer_edge"
            if on_clipped
            else "gap_crosses_water"
            if wet > 0.5
            else "no_network_within_max_gap"
        )
        rows.append((round(float(line.length), 1), round(wet, 1), reason))
    missing[["nearest_network_m", "gap_over_water_m", "reason"]] = rows
    return missing


def run_network(
    cfg: RegionConfig, paths: RegionPaths, *, progress: Callable[[str], None] = lambda _m: None
) -> NetworkResult:
    net = _network_config(cfg)
    epsg = cfg.analysis_crs
    raw = src.osm_source(cfg, paths)
    assert raw is not None
    pbf = _require(raw)
    sb = _read(paths, "study_boundary").set_index("kind")
    buffer = sb.geometry["analysis_buffer"]
    blocks = _read(paths, "blocks")
    parks = _read(paths, "parks")
    t0 = time.perf_counter()

    progress("water areas: one pass over the extract (~35 s)")
    water_gdf, _ = osm.extract_areas(
        pbf, {k: set(v) for k, v in net.water_tags.items()}, buffer, keep="intersects"
    )
    water = water_union(water_gdf, epsg)
    xml = paths.processed / "walk.tmp.osm"  # extension tells osmium the format
    progress("walk ways: one pass over the extract (~30 s)")
    try:
        extract = osm.extract_walk_ways(pbf, buffer.bounds, net.walk_filter, net.entrance_tags, xml)
        progress(f"graph: OSMnx from {extract.ways_kept:,} ways (~40 s)")
        graph = build_walk_graph(xml, buffer, epsg)
    finally:
        xml.unlink(missing_ok=True)
    main_nodes, main_edges = graph.main()

    progress("origins: snapping populated blocks onto the network")
    origins = build_origins(blocks, graph.edges, main_edges, epsg, cfg.analysis.max_snap_m, water)

    progress("park access points")
    assert extract.entrances is not None
    access = park_access_points(
        parks.to_crs(epsg),
        main_edges,
        extract.entrances.to_crs(epsg),
        tolerance=net.access_tolerance_m,
        exclude_access=net.entrance_exclude_access,
        water=water,
        max_gap=net.max_park_gap_m,
    )
    no_access_reasons = _no_access_reasons(
        parks.to_crs(epsg), access.per_park, graph, main_edges, water, net.access_tolerance_m
    )

    pop = origins["pop20"].to_numpy()
    conn = origins["connector_m"].to_numpy()
    status_counts = origins.groupby("snap_status")["pop20"].agg(["size", "sum"])
    stranded = _stranded(origins, graph.components)
    report: dict[str, Any] = {
        "region_id": cfg.region_id,
        "generated_at": now_utc(),
        "inputs": {
            "osm_extract_sha256": Manifest.read(manifest_path(pbf)).stored_sha256,
            "parks_sha256": sha256_file(paths.processed / "parks.parquet"),
            "blocks_sha256": sha256_file(paths.processed / "blocks.parquet"),
        },
        "filter": {
            "ways_kept": extract.ways_kept,
            "ways_readmitted": extract.readmitted_ways,
            "osm_nodes_written": extract.nodes_written,
            "excluded_by_rule": extract.excluded,
        },
        "graph": {
            "components": len(graph.components),
            "nodes_total": len(graph.nodes),
            "main_nodes": len(main_nodes),
            "main_edges": len(main_edges),
            "main_km": _round(main_edges["length_m"].sum() / 1000),
            "main_component_share": round(len(main_nodes) / max(len(graph.nodes), 1), 4),
            "length_agreement": length_agreement(main_edges),
            "largest_other_components": graph.components[1:11],
        },
        "origins": {
            "populated_blocks": len(origins),
            "populated_blocks_in_input": int((blocks["pop20"] > 0).sum()),
            "population": int(pop.sum()),
            "connector_m": {
                "median": _round(np.median(conn)),
                "p95": _round(np.percentile(conn, 95)),
                "max": _round(conn.max()),
            },
            "population_within_100m": round(float(pop[conn <= NEAR_M].sum() / pop.sum()), 4),
            "blocks_within_100m": round(float((conn <= NEAR_M).mean()), 4),
            "flagged_over_max_snap": _records(
                origins.loc[origins["flagged"], ["geoid20", "pop20"]].assign(
                    connector_m=origins.loc[origins["flagged"], "connector_m"].round(1)
                )
            ),
            "by_snap_status": {
                str(k): {"blocks": int(r["size"]), "population": int(r["sum"])}
                for k, r in status_counts.iterrows()
            },
            "not_ok": _records(
                origins.loc[origins["snap_status"] != OK, ["geoid20", "pop20", "snap_status"]]
                .assign(
                    connector_m=origins.loc[origins["snap_status"] != OK, "connector_m"].round(1)
                )
                .sort_values(["snap_status", "geoid20"])
            ),
            "stranded_population": int(sum(s["population"] for s in stranded)),
            "stranded_by_component": stranded,
        },
        "access": {
            "points": len(access.points),
            "by_method": access.points["method"].value_counts().sort_index().to_dict(),
            "parks_reachable": int((access.per_park["status"] == "reachable").sum()),
            "parks_reachable_via_gap": _records(
                access.per_park.loc[
                    access.per_park["status"] == "reachable_via_gap", ["park_id", "name", "gap_m"]
                ].round({"gap_m": 1})
            ),
            "parks_without_access": _records(no_access_reasons),
            "entrances_excluded_access": access.entrances_excluded_access,
            "entrances_off_network": access.entrances_off_network,
        },
        "seconds": _round(time.perf_counter() - t0),
    }
    frames = {
        # Every piece is kept (with its component id): isolated origins snap to their own.
        "network_nodes": graph.nodes.to_crs(STORAGE_EPSG),
        "network_edges": graph.edges.to_crs(STORAGE_EPSG),
        "block_origins": origins.to_crs(STORAGE_EPSG),
        "park_access_points": access.points.to_crs(STORAGE_EPSG),
    }
    checks = network_checks(cfg, net, report, access.per_park, parks)
    review = _review_layers(origins, access, parks, graph, cfg, epsg)
    _write(paths.processed, frames, report, checks, review, cfg)
    return NetworkResult(frames, report, checks)


def network_checks(
    cfg: RegionConfig,
    net: NetworkConfig,
    report: dict[str, Any],
    per_park: pd.DataFrame,
    parks: gpd.GeoDataFrame,
) -> list[Check]:
    g, o, a = report["graph"], report["origins"], report["access"]
    assert cfg.parks is not None
    sentinels = {s.casefold() for s in cfg.parks.sentinel_parks}
    sentinel_rows = per_park[per_park["name"].fillna("").str.casefold().isin(sentinels)]
    unreachable_sentinels = sorted(
        sentinel_rows.loc[sentinel_rows["status"] == NO_CONTACT, "name"].unique()
    )
    return [
        _hard(
            "network.main_component_share",
            g["main_component_share"] >= net.min_main_component_share,
            f">= {net.min_main_component_share}",
            g["main_component_share"],
        ),
        _hard(
            "network.edge_lengths_consistent",
            g["length_agreement"]["max_rel_diff"] < 0.01,
            "< 1% between geodesic length and projected geometry",
            round(g["length_agreement"]["max_rel_diff"], 5),
        ),
        _hard(
            "origins.population_within_100m",
            o["population_within_100m"] >= net.min_population_within_100m,
            f">= {net.min_population_within_100m}",
            o["population_within_100m"],
        ),
        _hard(
            "origins.every_populated_block",
            o["populated_blocks"] == o["populated_blocks_in_input"] > 0,
            o["populated_blocks_in_input"],
            o["populated_blocks"],
        ),
        _hard(
            "access.every_park_accounted",
            len(per_park) == len(parks),
            len(parks),
            len(per_park),
            without_access=len(a["parks_without_access"]),
        ),
        _hard("access.sentinels_reachable", not unreachable_sentinels, [], unreachable_sentinels),
        _info("network.filter", report["filter"]),
        _info("network.connectivity", {k: g[k] for k in ("components", "main_nodes", "main_km")}),
        _info("origins.snap", o["connector_m"], flagged=len(o["flagged_over_max_snap"])),
        _info(
            "origins.stranded",
            o["stranded_population"],
            by_component=o["stranded_by_component"][:10],
        ),
        _info("origins.by_snap_status", o["by_snap_status"], not_ok=o["not_ok"]),
        _info("access.summary", {k: a[k] for k in ("points", "by_method", "parks_reachable")}),
        _info("access.parks_reachable_via_gap", a["parks_reachable_via_gap"]),
        _info("access.parks_without_access", a["parks_without_access"]),
    ]


def _review_layers(
    origins: gpd.GeoDataFrame,
    access: Any,
    parks: gpd.GeoDataFrame,
    graph: Any,
    cfg: RegionConfig,
    epsg: int,
) -> gpd.GeoDataFrame:
    """One GeoJSON with a ``layer`` column, for eyeballing results in any viewer."""
    assert cfg.parks is not None
    layers = []
    flagged = origins[
        origins["flagged"] | (origins["nearest_component"] != 0) | (origins["snap_status"] != OK)
    ]
    layers.append(
        gpd.GeoDataFrame(
            {
                "layer": "origin_flagged_or_stranded",
                "id": flagged["geoid20"],
                "detail": [
                    f"{st}: pop {p}, {c:.0f} m connector"
                    for st, p, c in zip(
                        flagged["snap_status"],
                        flagged["pop20"],
                        flagged["connector_m"],
                        strict=True,
                    )
                ],
            },
            geometry=flagged.geometry,
            crs=epsg,
        )
    )
    none = parks[
        parks["park_id"].isin(
            access.per_park.loc[access.per_park["status"] == NO_CONTACT, "park_id"]
        )
    ]
    layers.append(
        gpd.GeoDataFrame(
            {"layer": "park_without_access", "id": none["park_id"], "detail": none["name"]},
            geometry=none.to_crs(epsg).geometry,
            crs=epsg,
        )
    )
    sentinels = {s.casefold() for s in cfg.parks.sentinel_parks}
    ids = parks.loc[parks["name"].fillna("").str.casefold().isin(sentinels), "park_id"]
    pts = access.points[access.points["park_id"].isin(ids)]
    layers.append(
        gpd.GeoDataFrame(
            {"layer": "sentinel_access_point", "id": pts["access_id"], "detail": pts["method"]},
            geometry=pts.geometry,
            crs=epsg,
        )
    )
    others = graph.nodes[graph.nodes["component"] > 0]
    hulls = others.dissolve("component").convex_hull.buffer(10)
    layers.append(
        gpd.GeoDataFrame(
            {
                "layer": "stranded_component",
                "id": [str(c) for c in hulls.index],
                "detail": [f"{(others['component'] == c).sum()} nodes" for c in hulls.index],
            },
            geometry=hulls.values,
            crs=epsg,
        )
    )
    out = pd.concat(layers, ignore_index=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=epsg).to_crs(STORAGE_EPSG)


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if pd.isna(value):
        return None
    raise TypeError(f"not JSON serializable: {type(value)}")


def _write(
    out_dir: Path,
    frames: dict[str, Any],
    report: dict[str, Any],
    checks: list[Check],
    review: gpd.GeoDataFrame,
    cfg: RegionConfig,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        frame.to_parquet(out_dir / NETWORK_FILES[name], index=False)
    dump = {"indent": 2, "sort_keys": True, "default": _json_default, "allow_nan": False}
    (out_dir / REPORT_FILE).write_text(json.dumps(report, **dump) + "\n", "utf-8")  # type: ignore[arg-type]
    hard = [c for c in checks if c.severity == "hard"]
    validation = {
        "region_id": cfg.region_id,
        "summary": {
            "hard_passed": sum(1 for c in hard if c.passed),
            "hard_failed": len(failed(checks)),
        },
        "checks": [asdict(c) for c in checks],
    }
    (out_dir / VALIDATION_FILE).write_text(json.dumps(validation, **dump) + "\n", "utf-8")  # type: ignore[arg-type]
    review_path = out_dir / REVIEW_FILE
    review_path.unlink(missing_ok=True)
    review.to_file(review_path, driver="GeoJSON")


def load_network_outputs(out_dir: Path) -> dict[str, Any] | None:
    """Network frames if ``touchgrass network`` has been run, else None."""
    paths = {n: out_dir / f for n, f in NETWORK_FILES.items()}
    if not all(p.is_file() for p in paths.values()):
        return None
    return {n: gpd.read_parquet(p) for n, p in paths.items()}


def network_freshness_problems(out_dir: Path) -> list[str]:
    """Reasons the network outputs can't be trusted with the current processed outputs."""
    report_path, validation_path = out_dir / REPORT_FILE, out_dir / VALIDATION_FILE
    if not (report_path.is_file() and validation_path.is_file()):
        return ["network report/validation missing; run `touchgrass network`"]
    report = json.loads(report_path.read_text("utf-8"))
    validation = json.loads(validation_path.read_text("utf-8"))
    problems = []
    for name in ("parks", "blocks"):
        if sha256_file(out_dir / f"{name}.parquet") != report["inputs"][f"{name}_sha256"]:
            problems.append(f"{name}.parquet changed since `touchgrass network` ran")
    if validation["summary"]["hard_failed"]:
        problems.append(f"{validation['summary']['hard_failed']} network hard check(s) failed")
    return problems
