"""Load one region's validated outputs into PostGIS: atomically and idempotently (D-019 Q1).

    processed GeoParquet ──(re-validate)──▶ per table:
        CREATE TEMP TABLE stage_x (... geometry as bytea ...) ON COMMIT DROP
        COPY stage_x FROM STDIN (FORMAT BINARY)          ← fast bulk path, WKB bytes as-is
        INSERT INTO touchgrass.x SELECT ..., ST_GeomFromWKB(wkb, 4326) FROM stage_x

All of it runs in ONE transaction that first deletes the region (every table cascades from
``regions``). Other sessions keep seeing the previous load until COMMIT (MVCC), and any
error rolls back to it. Afterwards ``ANALYZE`` refreshes the planner's statistics.

Why stage instead of COPYing straight into the tables? COPY can't call functions, and the
conversions belong in SQL where they're visible: WKB → geometry with SRID, lon/lat →
Point, JSON text → jsonb, ``a;b`` → text[]. The column types then enforce SRID and
geometry type, and CHECK constraints enforce the rest.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import psycopg

from touchgrass.config import RegionConfig
from touchgrass.db import SCHEMA
from touchgrass.db.migrate import require_current
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.pipeline.analyze import (
    RUN_FILES,
    RUNS_DIR,
    latest_run_id,
    list_runs,
    run_freshness_problems,
)
from touchgrass.pipeline.metrics import METRICS_FILES, has_metrics, metrics_freshness_problems
from touchgrass.pipeline.network import load_network_outputs, network_freshness_problems
from touchgrass.pipeline.process import load_outputs, load_stats
from touchgrass.pipeline.validate import failed, run_validation
from touchgrass.sources.http import Manifest, manifest_path

Conn = psycopg.Connection[Any]
Cursor = psycopg.Cursor[Any]

# Child-most last; every table has region_id and cascades from regions.
DATA_TABLES = (
    "datasets",
    "study_boundaries",
    "analysis_areas",
    "census_blocks",
    "area_membership",
    "block_groups",
    "acs_estimates",
    "green_candidates",
    "parks",
    "block_origins",
    "park_access_points",
    "analysis_runs",
    "block_access",
    "metrics_runs",
    "area_metrics",
    "area_thresholds",
    "area_context",
    "block_park_reach",
)
_ACS_COLUMN = re.compile(r"^(b\d{5}[a-i]?)_([em])(\d{3})$")


class LoadError(RuntimeError):
    pass


@dataclass
class LoadResult:
    region_id: str
    rows: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)


# ------------------------------------------------------------------ column helpers
# Binary COPY is strict about types (an int8 column needs a Python int, not a float or a
# numpy scalar), so each column is converted explicitly; NaN / NA become NULL.


def _text(s: Any) -> list[str | None]:
    return [None if pd.isna(v) else str(v) for v in s]


def _int(s: Any) -> list[int | None]:
    return [None if pd.isna(v) else int(v) for v in s]


def _float(s: Any) -> list[float | None]:
    return [None if pd.isna(v) else float(v) for v in s]


def _bool(s: Any) -> list[bool]:
    return [bool(v) for v in s]


def _wkb(gdf: Any) -> list[bytes]:
    return list(gdf.geometry.to_wkb())  # ISO WKB, no SRID: the INSERT attaches 4326


def _copy_stage(
    cur: Cursor, stage: str, columns: Sequence[tuple[str, str]], rows: Iterable[Sequence[Any]]
) -> None:
    ddl = ", ".join(f"{name} {pgtype}" for name, pgtype in columns)
    cur.execute(f"CREATE TEMP TABLE {stage} ({ddl}) ON COMMIT DROP")
    names = ", ".join(name for name, _ in columns)
    with cur.copy(f"COPY {stage} ({names}) FROM STDIN (FORMAT BINARY)") as copy:
        copy.set_types([pgtype for _, pgtype in columns])
        for row in rows:
            copy.write_row(row)


def _insert(cur: Cursor, sql: str, region_id: str, expected: int, table: str, **params: Any) -> int:
    cur.execute(sql, {"region": region_id, **params})
    if cur.rowcount != expected:
        raise LoadError(f"{table}: inserted {cur.rowcount} rows, expected {expected}")
    return expected


# ------------------------------------------------------------------- per-table loaders


def _load_datasets(cur: Cursor, region_id: str, manifests: list[Manifest]) -> int:
    _copy_stage(
        cur,
        "stage_datasets",
        [
            ("source_id", "text"),
            ("url", "text"),
            ("stored_file", "text"),
            ("stored_bytes", "int8"),
            ("sha256", "text"),
            ("retrieved_at", "text"),
            ("vintage", "text"),
            ("attribution", "text"),
            ("params", "text"),
        ],
        (
            (
                m.source_id,
                m.url,
                m.stored_file,
                m.stored_bytes,
                m.stored_sha256,
                m.retrieved_at,
                m.vintage,
                m.attribution,
                _json(m.params),
            )
            for m in manifests
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.datasets
              (region_id, source_id, url, stored_file, stored_bytes, sha256, retrieved_at,
               vintage, attribution, params)
            SELECT %(region)s, source_id, url, stored_file, stored_bytes, sha256,
                   retrieved_at::timestamptz, vintage, attribution, params::jsonb
            FROM stage_datasets""",
        region_id,
        len(manifests),
        "datasets",
    )


def _load_study_boundary(cur: Cursor, region_id: str, sb: Any) -> int:
    by_kind = sb.set_index("kind")
    if set(by_kind.index) != {"boundary", "analysis_buffer"}:
        raise LoadError(f"study_boundary kinds {sorted(by_kind.index)}")
    _copy_stage(
        cur,
        "stage_study_boundary",
        [("buffer_m", "float8"), ("boundary", "bytea"), ("analysis_buffer", "bytea")],
        [
            (
                float(by_kind.loc["analysis_buffer", "buffer_m"]),
                by_kind.geometry["boundary"].wkb,
                by_kind.geometry["analysis_buffer"].wkb,
            )
        ],
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.study_boundaries (region_id, buffer_m, boundary, analysis_buffer)
            SELECT %(region)s, buffer_m,
                   ST_GeomFromWKB(boundary, 4326), ST_GeomFromWKB(analysis_buffer, 4326)
            FROM stage_study_boundary""",
        region_id,
        1,
        "study_boundaries",
    )


def _load_areas(cur: Cursor, region_id: str, areas: Any) -> int:
    _copy_stage(
        cur,
        "stage_areas",
        [
            ("area_id", "text"),
            ("area_type", "text"),
            ("source_key", "text"),
            ("name", "text"),
            ("check_population", "int4"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(areas["area_id"]),
            _text(areas["area_type"]),
            _text(areas["source_key"]),
            _text(areas["name"]),
            _int(areas["check_population"]),
            _wkb(areas),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.analysis_areas
              (region_id, area_id, area_type, source_key, name, check_population, geom)
            SELECT %(region)s, area_id, area_type, source_key, name, check_population,
                   ST_GeomFromWKB(wkb, 4326)
            FROM stage_areas""",
        region_id,
        len(areas),
        "analysis_areas",
    )


def _load_blocks(cur: Cursor, region_id: str, blocks: Any) -> int:
    _copy_stage(
        cur,
        "stage_blocks",
        [
            ("geoid20", "text"),
            ("county_fips", "text"),
            ("pop20", "int4"),
            ("housing20", "int4"),
            ("aland20", "int8"),
            ("awater20", "int8"),
            ("area_m2", "float8"),
            ("frac_in_boundary", "float8"),
            ("rep_lon", "float8"),
            ("rep_lat", "float8"),
            ("rep_point_source", "text"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(blocks["geoid20"]),
            _text(blocks["county_fips"]),
            _int(blocks["pop20"]),
            _int(blocks["housing20"]),
            _int(blocks["aland20"]),
            _int(blocks["awater20"]),
            _float(blocks["area_m2"]),
            _float(blocks["frac_in_boundary"]),
            _float(blocks["rep_lon"]),
            _float(blocks["rep_lat"]),
            _text(blocks["rep_point_source"]),
            _wkb(blocks),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.census_blocks
              (region_id, geoid20, county_fips, pop20, housing20, aland20, awater20, area_m2,
               frac_in_boundary, rep_point, rep_point_source, geom)
            SELECT %(region)s, geoid20, county_fips, pop20, housing20, aland20, awater20,
                   area_m2, frac_in_boundary,
                   ST_SetSRID(ST_MakePoint(rep_lon, rep_lat), 4326), rep_point_source,
                   ST_GeomFromWKB(wkb, 4326)
            FROM stage_blocks""",
        region_id,
        len(blocks),
        "census_blocks",
    )


def _load_membership(cur: Cursor, region_id: str, membership: Any) -> int:
    _copy_stage(
        cur,
        "stage_membership",
        [("area_id", "text"), ("geoid20", "text")],
        zip(_text(membership["area_id"]), _text(membership["geoid20"]), strict=True),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.area_membership (region_id, area_id, geoid20)
            SELECT %(region)s, area_id, geoid20 FROM stage_membership""",
        region_id,
        len(membership),
        "area_membership",
    )


def acs_long(block_groups: Any) -> pd.DataFrame:
    """Wide ``b19013_e001`` / ``b19013_m001`` columns → rows (geoid, variable, estimate, moe)."""
    pairs: dict[str, dict[str, str]] = {}
    for column in block_groups.columns:
        match = _ACS_COLUMN.match(column)
        if match:
            table, kind, line = match.groups()
            pairs.setdefault(f"{table}_{line}", {})[kind] = column
    incomplete = sorted(v for v, kinds in pairs.items() if set(kinds) != {"e", "m"})
    if incomplete:
        raise LoadError(f"ACS variables without both estimate and MOE: {incomplete}")
    parts = [
        pd.DataFrame(
            {
                "geoid": block_groups["geoid"].to_numpy(),
                "variable": variable,
                "estimate": block_groups[kinds["e"]].to_numpy(),
                "moe": block_groups[kinds["m"]].to_numpy(),
            }
        )
        for variable, kinds in sorted(pairs.items())
    ]
    if not parts:
        return pd.DataFrame(columns=["geoid", "variable", "estimate", "moe"])
    return pd.concat(parts, ignore_index=True)


def _load_block_groups(cur: Cursor, region_id: str, bgs: Any, vintage: str) -> tuple[int, int]:
    _copy_stage(
        cur,
        "stage_block_groups",
        [("geoid", "text"), ("wkb", "bytea")],
        zip(_text(bgs["geoid"]), _wkb(bgs), strict=True),
    )
    n_bg = _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.block_groups (region_id, geoid, geom)
            SELECT %(region)s, geoid, ST_GeomFromWKB(wkb, 4326) FROM stage_block_groups""",
        region_id,
        len(bgs),
        "block_groups",
    )
    long = acs_long(bgs)
    _copy_stage(
        cur,
        "stage_acs",
        [("geoid", "text"), ("variable", "text"), ("estimate", "float8"), ("moe", "float8")],
        zip(
            _text(long["geoid"]),
            _text(long["variable"]),
            _float(long["estimate"]),
            _float(long["moe"]),
            strict=True,
        ),
    )
    n_acs = _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.acs_estimates (region_id, geoid, variable, estimate, moe, vintage)
            SELECT %(region)s, geoid, variable, estimate, moe, %(vintage)s FROM stage_acs""",
        region_id,
        len(long),
        "acs_estimates",
        vintage=vintage,
    )
    return n_bg, n_acs


def _load_candidates(cur: Cursor, region_id: str, cands: Any) -> int:
    _copy_stage(
        cur,
        "stage_candidates",
        [
            ("osm_type", "text"),
            ("osm_id", "int8"),
            ("name", "text"),
            ("tags", "text"),
            ("included", "bool"),
            ("park_class", "text"),
            ("reason", "text"),
            ("rule_tag", "text"),
            ("area_m2", "float8"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(cands["osm_type"]),
            _int(cands["osm_id"]),
            _text(cands["name"]),
            _text(cands["tags"]),
            _bool(cands["included"]),
            _text(cands["park_class"]),
            _text(cands["reason"]),
            _text(cands["rule_tag"]),
            _float(cands["area_m2"]),
            _wkb(cands),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.green_candidates
              (region_id, osm_type, osm_id, name, tags, included, park_class, reason, rule_tag,
               area_m2, geom)
            SELECT %(region)s, osm_type, osm_id, name, tags::jsonb, included, park_class, reason,
                   rule_tag, area_m2, ST_GeomFromWKB(wkb, 4326)
            FROM stage_candidates""",
        region_id,
        len(cands),
        "green_candidates",
    )


def _load_parks(cur: Cursor, region_id: str, parks: Any) -> int:
    _copy_stage(
        cur,
        "stage_parks",
        [
            ("park_id", "text"),
            ("name", "text"),
            ("park_class", "text"),
            ("area_m2", "float8"),
            ("subtracted_m2", "float8"),
            ("n_members", "int4"),
            ("member_ids", "text"),
            ("member_names", "text"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(parks["park_id"]),
            _text(parks["name"]),
            _text(parks["park_class"]),
            _float(parks["area_m2"]),
            _float(parks["subtracted_m2"]),
            _int(parks["n_members"]),
            _text(parks["member_ids"]),
            _text(parks["member_names"]),
            _wkb(parks),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.parks
              (region_id, park_id, name, park_class, area_m2, subtracted_m2, n_members,
               member_ids, member_names, geom)
            SELECT %(region)s, park_id, name, park_class, area_m2, subtracted_m2, n_members,
                   string_to_array(member_ids, ';'), member_names, ST_GeomFromWKB(wkb, 4326)
            FROM stage_parks""",
        region_id,
        len(parks),
        "parks",
    )


def _load_origins(cur: Cursor, region_id: str, origins: Any) -> int:
    _copy_stage(
        cur,
        "stage_origins",
        [
            ("geoid20", "text"),
            ("edge_id", "text"),
            ("u", "int8"),
            ("v", "int8"),
            ("connector_m", "float8"),
            ("offset_u_m", "float8"),
            ("offset_v_m", "float8"),
            ("nearest_component", "int4"),
            ("flagged", "bool"),
            ("snap_status", "text"),
            ("component", "int4"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(origins["geoid20"]),
            _text(origins["edge_id"]),
            _int(origins["u"]),
            _int(origins["v"]),
            _float(origins["connector_m"]),
            _float(origins["offset_u_m"]),
            _float(origins["offset_v_m"]),
            _int(origins["nearest_component"]),
            _bool(origins["flagged"]),
            _text(origins["snap_status"]),
            _int(origins["component"]),
            _wkb(origins),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.block_origins
              (region_id, geoid20, edge_id, u, v, connector_m, offset_u_m, offset_v_m,
               nearest_component, flagged, snap_status, component, geom)
            SELECT %(region)s, geoid20, edge_id, u, v, connector_m, offset_u_m, offset_v_m,
                   nearest_component, flagged, snap_status, component, ST_GeomFromWKB(wkb, 4326)
            FROM stage_origins""",
        region_id,
        len(origins),
        "block_origins",
    )


def _load_access_points(cur: Cursor, region_id: str, points: Any) -> int:
    _copy_stage(
        cur,
        "stage_access",
        [
            ("access_id", "text"),
            ("park_id", "text"),
            ("method", "text"),
            ("edge_id", "text"),
            ("u", "int8"),
            ("v", "int8"),
            ("offset_u_m", "float8"),
            ("offset_v_m", "float8"),
            ("contact_start_m", "float8"),
            ("contact_end_m", "float8"),
            ("osm_node_id", "int8"),
            ("gap_m", "float8"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(points["access_id"]),
            _text(points["park_id"]),
            _text(points["method"]),
            _text(points["edge_id"]),
            _int(points["u"]),
            _int(points["v"]),
            _float(points["offset_u_m"]),
            _float(points["offset_v_m"]),
            _float(points["contact_start_m"]),
            _float(points["contact_end_m"]),
            _int(points["osm_node_id"]),
            _float(points["gap_m"]),
            _wkb(points),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.park_access_points
              (region_id, access_id, park_id, method, edge_id, u, v, offset_u_m, offset_v_m,
               contact_start_m, contact_end_m, osm_node_id, gap_m, geom)
            SELECT %(region)s, access_id, park_id, method, edge_id, u, v, offset_u_m,
                   offset_v_m, contact_start_m, contact_end_m, osm_node_id, gap_m,
                   ST_GeomFromWKB(wkb, 4326)
            FROM stage_access""",
        region_id,
        len(points),
        "park_access_points",
    )


def _load_runs(cur: Cursor, region_id: str, run_dirs: list[Path], latest: str | None) -> int:
    for run_dir in run_dirs:
        record = json.loads((run_dir / RUN_FILES["run"]).read_text("utf-8"))
        cur.execute(
            f"""INSERT INTO {SCHEMA}.analysis_runs
                  (region_id, run_id, generated_at, params, inputs, code, summary, seconds,
                   is_latest)
                VALUES (%s, %s, %s::timestamptz, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s)""",
            (
                region_id,
                record["run_id"],
                record["generated_at"],
                _json(record["params"]),
                _json(record["inputs"]),
                _json(record["code"]),
                _json(record["summary"]),
                record["seconds"]["total"],
                record["run_id"] == latest,
            ),
        )
    return len(run_dirs)


def _load_block_access(cur: Cursor, region_id: str, run_dirs: list[Path]) -> int:
    frames = [gpd.read_parquet(d / RUN_FILES["block_access"]) for d in run_dirs]
    ba = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ba.empty:
        return 0
    _copy_stage(
        cur,
        "stage_block_access",
        [
            ("run_id", "text"),
            ("geoid20", "text"),
            ("pop20", "int4"),
            ("snap_status", "text"),
            ("reachable", "bool"),
            ("network_m", "float8"),
            ("walk_min", "float8"),
            ("straight_m", "float8"),
            ("straight_park_id", "text"),
            ("detour_ratio", "float8"),
            ("nearest_park_id", "text"),
            ("nearest_access_id", "text"),
            ("route_kind", "text"),
            ("wkb", "bytea"),
        ],
        zip(
            _text(ba["run_id"]),
            _text(ba["geoid20"]),
            _int(ba["pop20"]),
            _text(ba["snap_status"]),
            _bool(ba["reachable"]),
            _float(ba["network_m"]),
            _float(ba["walk_min"]),
            _float(ba["straight_m"]),
            _text(ba["straight_park_id"]),
            _float(ba["detour_ratio"]),
            _text(ba["nearest_park_id"]),
            _text(ba["nearest_access_id"]),
            _text(ba["route_kind"]),
            _wkb(ba),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.block_access
              (region_id, run_id, geoid20, pop20, snap_status, reachable, network_m, walk_min,
               straight_m, straight_park_id, detour_ratio, nearest_park_id, nearest_access_id,
               route_kind, geom)
            SELECT %(region)s, run_id, geoid20, pop20, snap_status, reachable, network_m,
                   walk_min, straight_m, straight_park_id, detour_ratio, nearest_park_id,
                   nearest_access_id, route_kind, ST_GeomFromWKB(wkb, 4326)
            FROM stage_block_access""",
        region_id,
        len(ba),
        "block_access",
    )


# ---------------------------------------------------------------- Stage 7: metrics


def _concat(run_dirs: list[Path], key: str) -> pd.DataFrame:
    frames = [pd.read_parquet(d / METRICS_FILES[key]) for d in run_dirs]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _load_metrics_runs(cur: Cursor, region_id: str, run_dirs: list[Path]) -> int:
    for run_dir in run_dirs:
        record = json.loads((run_dir / METRICS_FILES["run"]).read_text("utf-8"))
        cur.execute(
            f"""INSERT INTO {SCHEMA}.metrics_runs
                  (region_id, run_id, generated_at, params, inputs, code, summary)
                VALUES (%s, %s, %s::timestamptz, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)""",
            (
                region_id,
                record["run_id"],
                record["generated_at"],
                _json(record["params"]),
                _json(record["inputs"]),
                _json(record["code"]),
                _json(record["summary"]),
            ),
        )
    return len(run_dirs)


def _load_area_metrics(cur: Cursor, region_id: str, run_dirs: list[Path]) -> int:
    am = _concat(run_dirs, "area_metrics")
    if am.empty:
        return 0
    floats = [
        "populated_land_m2",
        "underserved_share",
        "median_walk_m",
        "priority_score",
        "block_share_within",
        "land_share_within",
        "reachable_park_ha_mean",
        "reachable_park_ha_median",
        "park_m2_inside",
        "park_m2_inside_per_resident",
    ]
    ints = ["blocks", "pop_total", "unreachable_pop", "underserved_pop"]
    texts = ["run_id", "area_id", "area_type", "name"]
    cols = [*texts, *ints, *floats, "ranked"]
    _copy_stage(
        cur,
        "stage_area_metrics",
        [(c, "text") for c in texts]
        + [(c, "int4") for c in ints]
        + [(c, "float8") for c in floats]
        + [("ranked", "bool")],
        zip(
            *[_text(am[c]) for c in texts],
            *[_int(am[c]) for c in ints],
            *[_float(am[c]) for c in floats],
            _bool(am["ranked"]),
            strict=True,
        ),
    )
    names = ", ".join(cols)
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.area_metrics (region_id, {names})
            SELECT %(region)s, {names} FROM stage_area_metrics""",
        region_id,
        len(am),
        "area_metrics",
    )


def _load_area_thresholds(cur: Cursor, region_id: str, run_dirs: list[Path]) -> int:
    at = _concat(run_dirs, "area_thresholds")
    if at.empty:
        return 0
    _copy_stage(
        cur,
        "stage_area_thresholds",
        [
            ("run_id", "text"),
            ("area_id", "text"),
            ("threshold_min", "int4"),
            ("threshold_m", "float8"),
            ("pop_within", "int4"),
            ("pct_within", "float8"),
        ],
        zip(
            _text(at["run_id"]),
            _text(at["area_id"]),
            _int(at["threshold_min"]),
            _float(at["threshold_m"]),
            _int(at["pop_within"]),
            _float(at["pct_within"]),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.area_thresholds
              (region_id, run_id, area_id, threshold_min, threshold_m, pop_within, pct_within)
            SELECT %(region)s, run_id, area_id, threshold_min, threshold_m, pop_within,
                   pct_within
            FROM stage_area_thresholds""",
        region_id,
        len(at),
        "area_thresholds",
    )


def _load_area_context(cur: Cursor, region_id: str, run_dirs: list[Path]) -> int:
    ac = _concat(run_dirs, "area_context")
    if ac.empty:
        return 0
    texts = ["run_id", "area_id", "indicator", "kind", "reliability", "acs_vintage"]
    floats = [
        "estimate",
        "moe",
        "cv",
        "numerator",
        "numerator_moe",
        "denominator",
        "denominator_moe",
    ]
    _copy_stage(
        cur,
        "stage_area_context",
        [(c, "text") for c in texts] + [(c, "float8") for c in floats],
        zip(*[_text(ac[c]) for c in texts], *[_float(ac[c]) for c in floats], strict=True),
    )
    names = ", ".join([*texts, *floats])
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.area_context (region_id, {names})
            SELECT %(region)s, {names} FROM stage_area_context""",
        region_id,
        len(ac),
        "area_context",
    )


def _load_block_park_reach(cur: Cursor, region_id: str, run_dirs: list[Path]) -> int:
    br = _concat(run_dirs, "block_park_reach")
    if br.empty:
        return 0
    _copy_stage(
        cur,
        "stage_block_park_reach",
        [
            ("run_id", "text"),
            ("geoid20", "text"),
            ("parks_reachable", "int4"),
            ("reachable_park_ha", "float8"),
        ],
        zip(
            _text(br["run_id"]),
            _text(br["geoid20"]),
            _int(br["parks_reachable"]),
            _float(br["reachable_park_ha"]),
            strict=True,
        ),
    )
    return _insert(
        cur,
        f"""INSERT INTO {SCHEMA}.block_park_reach
              (region_id, run_id, geoid20, parks_reachable, reachable_park_ha)
            SELECT %(region)s, run_id, geoid20, parks_reachable, reachable_park_ha
            FROM stage_block_park_reach""",
        region_id,
        len(br),
        "block_park_reach",
    )


# ------------------------------------------------------------------------- top level


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def provenance(cfg: RegionConfig, paths: RegionPaths) -> list[Manifest]:
    """The download manifest of every raw input the region's outputs were built from."""
    manifests, missing = [], []
    for raw in src.all_sources(cfg, paths):
        mpath = manifest_path(raw.path)
        if mpath.is_file():
            manifests.append(Manifest.read(mpath))
        else:
            missing.append(raw.source_id)
    if missing:
        raise LoadError(f"provenance manifests missing for {missing}; run `touchgrass fetch`")
    return sorted(manifests, key=lambda m: m.source_id)


def load_region(
    conn: Conn,
    cfg: RegionConfig,
    paths: RegionPaths,
    progress: Callable[[str], None] | None = None,
) -> LoadResult:
    """Replace ``cfg.region_id``'s rows with its processed outputs, in one transaction."""
    if not conn.autocommit:
        raise LoadError("load_region needs an autocommit connection (it manages the transaction)")
    say = progress or (lambda _m: None)
    require_current(conn)

    frames = load_outputs(paths.processed)
    bad = failed(run_validation(cfg, frames, load_stats(paths.processed)))
    if bad:
        names = ", ".join(c.name for c in bad)
        raise LoadError(f"processed outputs fail validation ({names}); fix before loading")
    manifests = provenance(cfg, paths)
    network = load_network_outputs(paths.processed) if cfg.network is not None else None
    if network is not None:
        stale = network_freshness_problems(paths.processed)
        if stale:
            raise LoadError(f"network outputs not loadable: {'; '.join(stale)}")
    run_dirs = list_runs(paths.processed) if network is not None else []
    latest = latest_run_id(paths.processed) if run_dirs else None
    if latest is not None:
        stale_run = run_freshness_problems(paths.processed, latest)
        if stale_run:
            raise LoadError(
                f"latest analysis run not loadable: {'; '.join(stale_run)}; "
                "run `touchgrass analyze`"
            )
    metric_dirs = [d for d in run_dirs if has_metrics(d)]
    if latest is not None and has_metrics(paths.processed / RUNS_DIR / latest):
        stale_metrics = metrics_freshness_problems(paths.processed, latest)
        if stale_metrics:
            raise LoadError(
                f"metrics of the latest run not loadable: {'; '.join(stale_metrics)}; "
                "run `touchgrass metrics`"
            )
    acs_vintage = f"{cfg.census.acs.year - 4}-{cfg.census.acs.year}"

    result = LoadResult(cfg.region_id)
    region = cfg.region_id

    def step(table: str, fn: Callable[[], int]) -> None:
        t0 = time.perf_counter()
        result.rows[table] = fn()
        result.seconds[table] = time.perf_counter() - t0
        say(f"{table}: {result.rows[table]:,} rows ({result.seconds[table]:.2f} s)")

    started = time.perf_counter()
    with conn.transaction(), conn.cursor() as cur:
        # Deleting the region cascades to every table: the reload starts from nothing.
        cur.execute(f"DELETE FROM {SCHEMA}.regions WHERE region_id = %s", (region,))
        cur.execute(
            f"INSERT INTO {SCHEMA}.regions (region_id, name, analysis_crs, config)"
            " VALUES (%s, %s, %s, %s::jsonb)",
            (region, cfg.name, cfg.analysis_crs, cfg.model_dump_json()),
        )
        step("datasets", lambda: _load_datasets(cur, region, manifests))
        step(
            "study_boundaries", lambda: _load_study_boundary(cur, region, frames["study_boundary"])
        )
        step("analysis_areas", lambda: _load_areas(cur, region, frames["analysis_areas"]))
        step("census_blocks", lambda: _load_blocks(cur, region, frames["blocks"]))
        step("area_membership", lambda: _load_membership(cur, region, frames["area_membership"]))
        counts: dict[str, int] = {}

        def block_groups() -> int:
            counts["block_groups"], counts["acs_estimates"] = _load_block_groups(
                cur, region, frames["block_groups"], acs_vintage
            )
            return counts["block_groups"]

        step("block_groups", block_groups)
        result.rows["acs_estimates"] = counts["acs_estimates"]
        say(f"acs_estimates: {counts['acs_estimates']:,} rows")
        if "parks" in frames:
            step(
                "green_candidates",
                lambda: _load_candidates(cur, region, frames["green_candidates"]),
            )
            step("parks", lambda: _load_parks(cur, region, frames["parks"]))
        if network is not None:
            step("block_origins", lambda: _load_origins(cur, region, network["block_origins"]))
            step(
                "park_access_points",
                lambda: _load_access_points(cur, region, network["park_access_points"]),
            )
        if run_dirs:
            step("analysis_runs", lambda: _load_runs(cur, region, run_dirs, latest))
            step("block_access", lambda: _load_block_access(cur, region, run_dirs))
        if metric_dirs:
            step("metrics_runs", lambda: _load_metrics_runs(cur, region, metric_dirs))
            step("area_metrics", lambda: _load_area_metrics(cur, region, metric_dirs))
            step("area_thresholds", lambda: _load_area_thresholds(cur, region, metric_dirs))
            step("area_context", lambda: _load_area_context(cur, region, metric_dirs))
            step("block_park_reach", lambda: _load_block_park_reach(cur, region, metric_dirs))
    result.seconds["transaction"] = time.perf_counter() - started
    say(f"committed in {result.seconds['transaction']:.2f} s")

    # Fresh statistics so the planner knows the new row counts and value distributions.
    for table in ("regions", *DATA_TABLES):
        conn.execute(f"ANALYZE {SCHEMA}.{table}")
    return result


# ----------------------------------------------------------------- inspection helpers


def row_counts(conn: Conn) -> dict[str, dict[str, int]]:
    """{region_id: {table: rows}} for every loaded region."""
    out: dict[str, dict[str, int]] = {}
    for table in DATA_TABLES:
        rows = conn.execute(
            f"SELECT region_id, count(*) FROM {SCHEMA}.{table} GROUP BY region_id"
        ).fetchall()
        for region_id, n in rows:
            out.setdefault(region_id, {})[table] = n
    regions = conn.execute(f"SELECT region_id FROM {SCHEMA}.regions").fetchall()
    for (region_id,) in regions:
        out.setdefault(region_id, {})
    return out


def content_digests(conn: Conn, region_id: str) -> dict[str, str]:
    """An MD5 of each table's rows for one region (order-independent), to compare loads.

    A row's text form includes geometry as hex EWKB, so any coordinate change shows up.
    ``regions.loaded_at`` is excluded: it is meant to change on every load.
    """
    out: dict[str, str] = {}
    for table in DATA_TABLES:
        row = conn.execute(
            f"""SELECT md5(coalesce(string_agg(r, E'\\n' ORDER BY r), ''))
                FROM (SELECT t::text AS r FROM {SCHEMA}.{table} t WHERE region_id = %s) x""",
            (region_id,),
        ).fetchone()
        out[table] = row[0] if row else ""
    row = conn.execute(
        f"SELECT md5((to_jsonb(r) - 'loaded_at')::text) FROM {SCHEMA}.regions r"
        " WHERE region_id = %s",
        (region_id,),
    ).fetchone()
    out["regions"] = row[0] if row else ""
    return out
