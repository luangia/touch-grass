"""Analysis areas (GeoJSON), per-area metrics, comparison and rankings."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from touchgrass_api.db import SCHEMA, Conn
from touchgrass_api.deps import (
    Db,
    RegionCtx,
    RegionWithMetrics,
    cache_headers,
    check_area_type,
    check_threshold,
    is_fresh,
    not_modified,
)
from touchgrass_api.models import (
    AreaMetrics,
    AreaMetricsResponse,
    Comparison,
    ContextValue,
    Ranking,
    RankingEntry,
    ThresholdShare,
)

router = APIRouter(prefix="/api/v1/regions/{region_id}", tags=["areas"])

GEOJSON = "application/geo+json"
SIMPLIFY_M = 5.0  # D-023 Q5: in the region's analysis CRS (meters)
DECIMALS = 6  # ≈ 0.1 m
MAX_COMPARE = 10


def geojson_geometry(column: str) -> str:
    """SQL: simplify in meters (the region's analysis CRS), back to WGS 84, 6 decimals."""
    return (
        f"ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(ST_Transform({column}, "
        f"%(crs)s::int), %(tol)s::float8), 4326), {DECIMALS})::json"
    )


AreaTypeQuery = Annotated[str, Query(alias="type", pattern=r"^[a-z0-9_]+$", max_length=64)]
ThresholdQuery = Annotated[
    int | None, Query(ge=1, le=120, description="Minutes; default: underserved threshold")
]

_AREAS_GEOJSON = f"""
SELECT json_build_object(
    'type', 'FeatureCollection',
    'region_id', %(region)s::text, 'run_id', %(run)s::text,
    'area_type', %(type)s::text, 'threshold_min', %(t)s::int,
    'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'id', a.area_id,
        'geometry', {geojson_geometry("a.geom")},
        'properties', json_build_object(
            'area_id', a.area_id, 'area_type', a.area_type, 'name', a.name,
            'pop_total', m.pop_total, 'pop_within', t.pop_within, 'pct_within', t.pct_within,
            'pct_within_straight', t.pct_within_straight,
            'underserved_pop', m.underserved_pop, 'underserved_share', m.underserved_share,
            'median_walk_m', m.median_walk_m, 'priority_score', m.priority_score,
            'ranked', m.ranked, 'reachable_park_ha_median', m.reachable_park_ha_median)
    ) ORDER BY a.area_id), '[]'::json))::text AS body
FROM {SCHEMA}.analysis_areas a
LEFT JOIN {SCHEMA}.area_metrics m
       ON m.region_id = a.region_id AND m.area_id = a.area_id AND m.run_id = %(run)s
LEFT JOIN {SCHEMA}.area_thresholds t
       ON t.region_id = m.region_id AND t.run_id = m.run_id AND t.area_id = m.area_id
      AND t.threshold_min = %(t)s
WHERE a.region_id = %(region)s AND a.area_type = %(type)s
"""


@router.get(
    "/areas",
    response_class=Response,
    responses={200: {"content": {GEOJSON: {}}, "description": "GeoJSON FeatureCollection"}},
)
def areas_geojson(
    region: RegionWithMetrics,
    conn: Db,
    request: Request,
    area_type: AreaTypeQuery,
    threshold: ThresholdQuery = None,
) -> Response:
    """Analysis areas of one type with headline metrics at one threshold (WGS 84, simplified)."""
    check_area_type(conn, region.region_id, area_type)
    t = check_threshold(region, threshold)
    max_age = request.app.state.settings.cache_max_age_s
    if is_fresh(request, region):
        return not_modified(region, max_age)
    row = conn.execute(
        _AREAS_GEOJSON,
        {
            "region": region.region_id,
            "run": region.run_id,
            "type": area_type,
            "t": t,
            "crs": region.analysis_crs,
            "tol": SIMPLIFY_M,
        },
    ).fetchone()
    assert row is not None
    response = Response(row["body"], media_type=GEOJSON)
    cache_headers(response, region, max_age)
    return response


# --------------------------------------------------------------------- metrics


_METRIC_COLUMNS = [
    "area_id", "area_type", "name", "pop_total", "blocks", "underserved_pop",
    "underserved_share", "unreachable_pop", "median_walk_m", "priority_score", "ranked",
    "reachable_park_ha_median", "reachable_park_ha_mean", "park_m2_inside",
    "park_m2_inside_per_resident", "block_share_within", "land_share_within",
]  # fmt: skip


def load_metrics(conn: Conn, region: RegionCtx, area_ids: list[str]) -> list[AreaMetrics]:
    """Full metrics for the given areas (in the given order); 404 if any is unknown."""
    params = {"region": region.region_id, "run": region.run_id, "ids": area_ids}
    rows = conn.execute(
        f"""SELECT {", ".join(_METRIC_COLUMNS)} FROM {SCHEMA}.area_metrics
            WHERE region_id = %(region)s AND run_id = %(run)s AND area_id = ANY(%(ids)s)""",
        params,
    ).fetchall()
    found = {r["area_id"]: r for r in rows}
    missing = [a for a in area_ids if a not in found]
    if missing:
        raise HTTPException(404, f"unknown area(s) or no metrics: {missing}")
    shares: dict[str, list[ThresholdShare]] = {a: [] for a in area_ids}
    for r in conn.execute(
        f"""SELECT area_id, threshold_min, threshold_m, pop_within, pct_within,
                   pop_within_straight, pct_within_straight
            FROM {SCHEMA}.area_thresholds
            WHERE region_id = %(region)s AND run_id = %(run)s AND area_id = ANY(%(ids)s)
            ORDER BY area_id, threshold_min""",
        params,
    ).fetchall():
        shares[r.pop("area_id")].append(ThresholdShare(**r))
    context: dict[str, list[ContextValue]] = {a: [] for a in area_ids}
    for r in conn.execute(
        f"""SELECT area_id, indicator, kind, estimate, moe, cv, reliability, numerator,
                   denominator, acs_vintage
            FROM {SCHEMA}.area_context
            WHERE region_id = %(region)s AND run_id = %(run)s AND area_id = ANY(%(ids)s)
            ORDER BY area_id, indicator""",
        params,
    ).fetchall():
        context[r.pop("area_id")].append(ContextValue(**r))
    return [AreaMetrics(**found[a], thresholds=shares[a], context=context[a]) for a in area_ids]


@router.get("/areas/{area_id}/metrics", response_model=AreaMetricsResponse)
def area_metrics(
    region: RegionWithMetrics, conn: Db, area_id: str, request: Request, response: Response
) -> AreaMetricsResponse | Response:
    """All thresholds, per-area values and ACS context for one area (``city`` = whole region)."""
    max_age = request.app.state.settings.cache_max_age_s
    if is_fresh(request, region):
        return not_modified(region, max_age)
    (area,) = load_metrics(conn, region, [area_id])
    cache_headers(response, region, max_age)
    assert region.run_id is not None
    return AreaMetricsResponse(region_id=region.region_id, run_id=region.run_id, area=area)


@router.get("/compare", response_model=Comparison)
def compare(
    region: RegionWithMetrics,
    conn: Db,
    area_ids: Annotated[str, Query(description="Comma-separated area ids (2-10)")],
    threshold: ThresholdQuery = None,
) -> Comparison:
    ids = list(dict.fromkeys(a.strip() for a in area_ids.split(",") if a.strip()))
    if not 2 <= len(ids) <= MAX_COMPARE:
        raise HTTPException(422, f"compare needs 2 to {MAX_COMPARE} distinct area ids")
    t = check_threshold(region, threshold)
    assert region.run_id is not None
    return Comparison(
        region_id=region.region_id,
        run_id=region.run_id,
        threshold_min=t,
        areas=load_metrics(conn, region, ids),
    )


# -------------------------------------------------------------------- rankings


class RankMetric(StrEnum):
    pct_within = "pct_within"
    underserved_pop = "underserved_pop"
    priority_score = "priority_score"
    barrier_gap = "barrier_gap"  # straight-line share - walking share: what barriers cost


# Identifiers can't be SQL parameters, so each metric maps to a FIXED expression; the
# request text never reaches the SQL (lesson §5.6).
_VALUE = {
    RankMetric.pct_within: "t.pct_within",
    RankMetric.underserved_pop: "m.underserved_pop",
    RankMetric.priority_score: "m.priority_score",
    RankMetric.barrier_gap: "(t.pct_within_straight - t.pct_within)",
}
_DEFAULT_ORDER = {
    RankMetric.pct_within: "asc",  # lowest share first = worst served
    RankMetric.underserved_pop: "desc",
    RankMetric.priority_score: "desc",
    RankMetric.barrier_gap: "desc",
}
_FLOORED = {RankMetric.pct_within, RankMetric.priority_score, RankMetric.barrier_gap}  # rates


# Stored only at the underserved threshold; the other two exist at every threshold.
_AT_UNDERSERVED = {RankMetric.underserved_pop, RankMetric.priority_score}


class Order(StrEnum):
    asc = "asc"
    desc = "desc"


@router.get("/rankings", response_model=Ranking)
def rankings(
    region: RegionWithMetrics,
    conn: Db,
    area_type: AreaTypeQuery,
    metric: RankMetric = RankMetric.pct_within,
    threshold: ThresholdQuery = None,
    order: Order | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> Ranking:
    """Areas ordered by an access metric. ACS context is never ranked (D-022 Q7)."""
    check_area_type(conn, region.region_id, area_type)
    t = check_threshold(region, threshold)
    if metric in _AT_UNDERSERVED and t != region.underserved_minutes:
        raise HTTPException(
            422, f"{metric.value} is defined at the {region.underserved_minutes}-minute threshold"
        )
    direction = (order.value if order else _DEFAULT_ORDER[metric]).upper()
    floor = None
    if metric in _FLOORED:
        floor = int(
            (region.metrics_params or {}).get("metrics", {}).get("min_residents_for_ranking", 0)
        )
    value = _VALUE[metric]
    rows = conn.execute(
        f"""SELECT m.area_id, m.name, {value}::float8 AS value, m.pop_total, m.underserved_pop,
                   t.pct_within
            FROM {SCHEMA}.area_metrics m
            JOIN {SCHEMA}.area_thresholds t
              ON t.region_id = m.region_id AND t.run_id = m.run_id AND t.area_id = m.area_id
             AND t.threshold_min = %(t)s
            WHERE m.region_id = %(region)s AND m.run_id = %(run)s AND m.area_type = %(type)s
              AND {value} IS NOT NULL AND (NOT %(floored)s OR m.ranked)
            ORDER BY {value} {direction}, m.area_id
            LIMIT %(limit)s""",
        {
            "region": region.region_id,
            "run": region.run_id,
            "type": area_type,
            "t": t,
            "floored": floor is not None,
            "limit": limit,
        },
    ).fetchall()
    assert region.run_id is not None
    return Ranking(
        region_id=region.region_id,
        run_id=region.run_id,
        area_type=area_type,
        metric=metric.value,
        threshold_min=t,
        order="asc" if direction == "ASC" else "desc",
        min_residents=floor,
        entries=[RankingEntry(rank=i + 1, **_row(r)) for i, r in enumerate(rows)],
    )


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        k: r[k] for k in ("area_id", "name", "value", "pop_total", "underserved_pop", "pct_within")
    }
