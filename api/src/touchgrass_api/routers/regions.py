"""Regions, region detail and methodology."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from touchgrass_api.db import SCHEMA
from touchgrass_api.deps import (
    Db,
    Region,
    RegionCtx,
    area_types,
    cache_headers,
    is_fresh,
    not_modified,
)
from touchgrass_api.models import (
    AreaType,
    BBox,
    Caveat,
    Methodology,
    RegionDetail,
    RegionSummary,
    Source,
    Threshold,
)

router = APIRouter(prefix="/api/v1", tags=["regions"])

_SUMMARY = f"""
    SELECT r.region_id, r.name, a.run_id AS latest_run_id,
           ST_XMin(e) AS west, ST_YMin(e) AS south, ST_XMax(e) AS east, ST_YMax(e) AS north
    FROM {SCHEMA}.regions r
    JOIN LATERAL (SELECT ST_Envelope(boundary) AS e FROM {SCHEMA}.study_boundaries s
                  WHERE s.region_id = r.region_id) b ON true
    LEFT JOIN {SCHEMA}.analysis_runs a ON a.region_id = r.region_id AND a.is_latest
"""


def _summary(row: dict[str, Any]) -> RegionSummary:
    bbox = BBox(west=row["west"], south=row["south"], east=row["east"], north=row["north"])
    return RegionSummary(
        region_id=row["region_id"],
        name=row["name"],
        bbox=bbox,
        center=((bbox.west + bbox.east) / 2, (bbox.south + bbox.north) / 2),
        latest_run_id=row["latest_run_id"],
    )


def _thresholds(region: RegionCtx) -> list[Threshold]:
    return [Threshold(minutes=t, meters=m) for t, m in sorted(region.thresholds_m.items())]


@router.get("/regions", response_model=list[RegionSummary])
def list_regions(conn: Db) -> list[RegionSummary]:
    rows = conn.execute(_SUMMARY + " ORDER BY r.region_id").fetchall()
    return [_summary(r) for r in rows]


@router.get("/regions/{region_id}", response_model=RegionDetail)
def region_detail(region: Region, conn: Db) -> RegionDetail:
    row = conn.execute(_SUMMARY + " WHERE r.region_id = %s", (region.region_id,)).fetchone()
    assert row is not None  # get_region already proved the region exists
    cfg = region.config
    acs = cfg.get("census", {}).get("acs")
    return RegionDetail(
        **_summary(row).model_dump(),
        analysis_crs=region.analysis_crs,
        walk_speed_kmh=float(cfg.get("analysis", {}).get("walk_speed_kmh", 4.8)),
        thresholds=_thresholds(region),
        underserved_minutes=region.underserved_minutes,
        area_types=[
            AreaType(area_type=t, areas=n) for t, n in area_types(conn, region.region_id).items()
        ],
        acs_vintage=f"{acs['year'] - 4}-{acs['year']}" if acs else None,
        osm_snapshot=(cfg.get("osm") or {}).get("snapshot"),
        metrics_available=region.metrics_params is not None,
    )


@router.get("/regions/{region_id}/methodology", response_model=Methodology)
def methodology(
    region: Region, conn: Db, request: Request, response: Response
) -> Methodology | Response:
    if is_fresh(request, region):
        return not_modified(region, request.app.state.settings.cache_max_age_s)
    cache_headers(response, region, request.app.state.settings.cache_max_age_s)
    run = conn.execute(
        f"""SELECT params FROM {SCHEMA}.analysis_runs
            WHERE region_id = %s AND run_id = %s""",
        (region.region_id, region.run_id),
    ).fetchone()
    sources = conn.execute(
        f"""SELECT source_id, url, vintage, to_char(retrieved_at AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS retrieved_at, attribution, license_name,
                   license_url
            FROM {SCHEMA}.datasets WHERE region_id = %s ORDER BY source_id""",
        (region.region_id,),
    ).fetchall()
    caveats = (region.metrics_summary or {}).get("caveats", [])
    return Methodology(
        region_id=region.region_id,
        run_id=region.run_id,
        walk_speed_kmh=float(region.config.get("analysis", {}).get("walk_speed_kmh", 4.8)),
        thresholds=_thresholds(region),
        underserved_minutes=region.underserved_minutes,
        analysis=run["params"] if run else {},
        metrics=region.metrics_params,
        sources=[Source(**s) for s in sources],
        caveats=[Caveat(**c) for c in caveats],
        resident_comparison=(region.metrics_summary or {}).get("resident_comparison"),
    )
