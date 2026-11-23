"""Step 2: raw files → clean, validated GeoParquet (offline; never touches the network).

Outputs in ``data/processed/<region_id>/`` (all geometry in EPSG:4326):

    study_boundary.parquet    kind = boundary | analysis_buffer
    analysis_areas.parquet    area_id, area_type, source_key, name, check_population
    blocks.parquet            2020 blocks whose representative point is inside the boundary
    area_membership.parquet   (area_id, geoid20): many-to-many
    block_groups.parquet      block groups containing study blocks + ACS estimates and MOEs

Every frame is sorted by its key before writing so repeated runs give identical contents.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely

from touchgrass.config import RegionConfig
from touchgrass.config.models import AnalysisAreaSource
from touchgrass.core.assign import assign_points_to_areas, points_in_polygon
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.core.geometry import (
    area_m2,
    buffer_in_crs,
    clean_polygons,
    parse_coordinate,
    representative_points,
    to_multipolygon,
)
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.sources import acs, tiger
from touchgrass.sources.http import cached_manifest

OUTPUT_FILES = {
    "study_boundary": "study_boundary.parquet",
    "analysis_areas": "analysis_areas.parquet",
    "blocks": "blocks.parquet",
    "area_membership": "area_membership.parquet",
    "block_groups": "block_groups.parquet",
}
STATS_FILE = "process_stats.json"


class MissingDataError(RuntimeError):
    pass


@dataclass
class ProcessResult:
    frames: dict[str, Any]  # name -> GeoDataFrame / DataFrame
    stats: dict[str, Any] = field(default_factory=dict)  # facts only process can observe


def _require(raw: src.RawSource) -> Path:
    """Use a raw file only if its manifest checksum still matches (no silent edits)."""
    if cached_manifest(raw.path) is None:
        raise MissingDataError(
            f"{raw.source_id}: {raw.path} missing or changed since download; "
            "run `touchgrass fetch` first"
        )
    return raw.path


def _read_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    # RFC 7946: GeoJSON is always WGS 84 lon/lat; we requested outSR=4326 explicitly.
    return gdf.set_crs(STORAGE_EPSG) if gdf.crs is None else gdf.to_crs(STORAGE_EPSG)


# ------------------------------------------------------------------------------ steps


def build_boundary(cfg: RegionConfig, paths: RegionPaths, stats: dict[str, Any]) -> Any:
    raw = _read_geojson(_require(src.boundary_source(cfg, paths)))
    if raw.empty:
        raise ValueError("study boundary query returned no features")
    clean, repaired = clean_polygons(raw)
    boundary = to_multipolygon(shapely.union_all(clean.geometry.values))
    if boundary is None:
        raise ValueError("study boundary has no polygonal area after cleaning")
    buffered = to_multipolygon(buffer_in_crs(boundary, cfg.buffer_m, cfg.analysis_crs))
    stats["boundary"] = {
        "source_features": len(raw),
        "repaired_geometries": repaired,
        "parts": len(boundary.geoms),
    }
    return gpd.GeoDataFrame(
        {
            "region_id": cfg.region_id,
            "kind": ["boundary", "analysis_buffer"],
            "buffer_m": [0.0, float(cfg.buffer_m)],
        },
        geometry=[boundary, buffered],
        crs=STORAGE_EPSG,
    )


def _build_area_type(
    area: AnalysisAreaSource, raw: gpd.GeoDataFrame, region_id: str
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    fields = [area.key_field, area.name_field]
    if area.population_check_field:
        fields.append(area.population_check_field)
    missing = [f for f in fields if f not in raw.columns]
    if missing:
        raise ValueError(f"{area.area_type}: source lacks fields {missing}")

    clean, repaired = clean_polygons(raw)
    clean = clean.assign(source_key=clean[area.key_field].astype(str))
    duplicate_keys = int(clean["source_key"].duplicated().sum())
    # Several features may share a key (multi-part areas published as separate rows).
    agg: dict[str, Any] = {area.name_field: "first"}
    if area.population_check_field:
        agg[area.population_check_field] = "first"
    dissolved = clean.dissolve(by="source_key", aggfunc=agg).reset_index()
    dissolved["geometry"] = [to_multipolygon(g) for g in dissolved.geometry]

    out = gpd.GeoDataFrame(
        {
            "area_id": area.area_type + ":" + dissolved["source_key"],
            "region_id": region_id,
            "area_type": area.area_type,
            "source_key": dissolved["source_key"],
            "name": dissolved[area.name_field].astype(str),
            "check_population": (
                pd.to_numeric(dissolved[area.population_check_field]).astype("Int64")
                if area.population_check_field
                else pd.Series(pd.NA, index=dissolved.index, dtype="Int64")
            ),
        },
        geometry=dissolved.geometry.values,
        crs=STORAGE_EPSG,
    )
    return out, {
        "source_features": len(raw),
        "areas": len(out),
        "repaired_geometries": repaired,
        "duplicate_source_keys_dissolved": duplicate_keys,
    }


def build_areas(cfg: RegionConfig, paths: RegionPaths, stats: dict[str, Any]) -> Any:
    frames = []
    stats["areas"] = {}
    for area, raw_src in zip(cfg.analysis_areas, src.area_sources(cfg, paths), strict=True):
        raw = _read_geojson(_require(raw_src))
        frame, area_stats = _build_area_type(area, raw, cfg.region_id)
        frames.append(frame)
        stats["areas"][area.area_type] = area_stats
    combined = pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=STORAGE_EPSG).sort_values(
        "area_id", ignore_index=True
    )


def build_blocks(
    cfg: RegionConfig, paths: RegionPaths, boundary: Any, stats: dict[str, Any]
) -> Any:
    raw = tiger.read_counties(
        _require(src.tiger_source(cfg, paths, "tabblock20")), "tabblock20", cfg.county_fips
    )
    clean, repaired = clean_polygons(raw)
    # Interior points are published in the file's own CRS (NAD83); build them there,
    # then reproject points and polygons together.
    points, point_source = representative_points(
        clean.geometry, parse_coordinate(clean["INTPTLON20"]), parse_coordinate(clean["INTPTLAT20"])
    )
    clean = clean.to_crs(STORAGE_EPSG)
    points = points.to_crs(STORAGE_EPSG)

    in_city = points_in_polygon(points, boundary)
    touches = clean.geometry.intersects(boundary)
    straddlers_out = touches & ~in_city  # overlap the boundary but represented outside

    blocks = clean[in_city].copy()
    pts = points[in_city]
    blocks_utm_area = area_m2(blocks, cfg.analysis_crs)
    inside_area = area_m2(
        gpd.GeoSeries(blocks.geometry.intersection(boundary), crs=STORAGE_EPSG), cfg.analysis_crs
    )

    out = gpd.GeoDataFrame(
        {
            "geoid20": blocks["GEOID20"].astype(str),
            "region_id": cfg.region_id,
            "county_fips": blocks["COUNTYFP20"].astype(str),
            "pop20": blocks["POP20"].astype("int64"),
            "housing20": blocks["HOUSING20"].astype("int64"),
            "aland20": blocks["ALAND20"].astype("int64"),
            "awater20": blocks["AWATER20"].astype("int64"),
            "area_m2": blocks_utm_area.values,
            "frac_in_boundary": (inside_area / blocks_utm_area).clip(0, 1).fillna(0).values,
            "rep_lon": pts.x.values,
            "rep_lat": pts.y.values,
            "rep_point_source": point_source[in_city].values,
        },
        geometry=blocks.geometry.values,
        crs=STORAGE_EPSG,
    ).sort_values("geoid20", ignore_index=True)

    stats["blocks"] = {
        "county_blocks_read": len(raw),
        "repaired_geometries": repaired,
        "rep_point_fallbacks_all": int((point_source == "point_on_surface").sum()),
        "excluded_touching_boundary": _overlap_breakdown(
            clean[straddlers_out], boundary, cfg.analysis_crs
        ),
    }
    return out


def _overlap_breakdown(blocks: Any, boundary: Any, analysis_epsg: int) -> dict[str, Any]:
    """Blocks that touch the boundary but whose representative point is outside, bucketed
    by the share of their area inside. Most are digitising slivers (<1%): the boundary
    and Census block lines trace the same streets slightly differently."""
    if blocks.empty:
        return {}
    inside = area_m2(
        gpd.GeoSeries(blocks.geometry.intersection(boundary), crs=STORAGE_EPSG), analysis_epsg
    )
    frac = (inside / area_m2(blocks, analysis_epsg)).fillna(0)
    buckets = {"<1%": (0, 0.01), "1-10%": (0.01, 0.1), "10-50%": (0.1, 0.5), ">=50%": (0.5, 2)}
    out: dict[str, Any] = {}
    for label, (lo, hi) in buckets.items():
        mask = (frac >= lo) & (frac < hi)
        out[label] = {"blocks": int(mask.sum()), "population": int(blocks.loc[mask, "POP20"].sum())}
    return out


def build_membership(blocks: Any, areas: Any) -> pd.DataFrame:
    points = gpd.GeoDataFrame(
        {"geoid20": blocks["geoid20"]},
        geometry=gpd.points_from_xy(blocks["rep_lon"], blocks["rep_lat"]),
        crs=STORAGE_EPSG,
    )
    members = assign_points_to_areas(points, "geoid20", areas, "area_id")
    members = members.merge(areas[["area_id", "area_type"]], on="area_id", how="left")
    return members[["area_id", "area_type", "geoid20"]].sort_values(
        ["area_id", "geoid20"], ignore_index=True
    )


def build_block_groups(
    cfg: RegionConfig, paths: RegionPaths, blocks: Any, stats: dict[str, Any]
) -> Any:
    raw = tiger.read_counties(_require(src.tiger_source(cfg, paths, "bg")), "bg", cfg.county_fips)
    clean, repaired = clean_polygons(raw)
    clean = clean.to_crs(STORAGE_EPSG)
    # Census nesting: a block's first 12 GEOID digits ARE its block group (attribute join).
    wanted = set(blocks["geoid20"].str.slice(0, 12))
    bgs = clean[clean["GEOID"].isin(wanted)]
    out = gpd.GeoDataFrame(
        {"geoid": bgs["GEOID"].astype(str), "region_id": cfg.region_id},
        geometry=bgs.geometry.values,
        crs=STORAGE_EPSG,
    )
    for raw_src in src.acs_sources(cfg, paths):
        table = acs.read_table(_require(raw_src))
        out = out.merge(table, on="geoid", how="left")
    stats["block_groups"] = {
        "county_block_groups_read": len(raw),
        "repaired_geometries": repaired,
        "acs_vintage": f"{cfg.census.acs.year - 4}-{cfg.census.acs.year}",
        "acs_tables": list(cfg.census.acs.tables),
    }
    return gpd.GeoDataFrame(out, geometry="geometry", crs=STORAGE_EPSG).sort_values(
        "geoid", ignore_index=True
    )


# ---------------------------------------------------------------------------- driver


def run_process(
    cfg: RegionConfig,
    paths: RegionPaths,
    *,
    progress: Callable[[str], None] = lambda _msg: None,
) -> ProcessResult:
    stats: dict[str, Any] = {}
    progress("study boundary + buffer")
    boundary_gdf = build_boundary(cfg, paths, stats)
    boundary = boundary_gdf.loc[boundary_gdf["kind"] == "boundary", "geometry"].iloc[0]
    progress("analysis areas")
    areas = build_areas(cfg, paths, stats)
    progress("census blocks (reading TIGER; this is the slow step)")
    blocks = build_blocks(cfg, paths, boundary, stats)
    progress("block → area membership")
    membership = build_membership(blocks, areas)
    progress("block groups + ACS")
    block_groups = build_block_groups(cfg, paths, blocks, stats)

    frames = {
        "study_boundary": boundary_gdf,
        "analysis_areas": areas,
        "blocks": blocks,
        "area_membership": membership,
        "block_groups": block_groups,
    }
    write_outputs(frames, paths.processed)
    (paths.processed / STATS_FILE).write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    return ProcessResult(frames=frames, stats=stats)


def load_stats(out_dir: Path) -> dict[str, Any]:
    path = out_dir / STATS_FILE
    return json.loads(path.read_text("utf-8")) if path.is_file() else {}


def write_outputs(frames: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        frame.to_parquet(out_dir / OUTPUT_FILES[name], index=False)


def load_outputs(out_dir: Path) -> dict[str, Any]:
    frames: dict[str, Any] = {}
    for name, filename in OUTPUT_FILES.items():
        path = out_dir / filename
        if not path.is_file():
            raise MissingDataError(f"{path} not found; run `touchgrass process` first")
        frames[name] = (
            pd.read_parquet(path) if name == "area_membership" else gpd.read_parquet(path)
        )
    return frames
