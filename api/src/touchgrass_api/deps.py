"""Shared request dependencies: a pooled connection, the region (404), its latest run.

FastAPI resolves these per request and caches each within that request, so a route that
needs both ``get_conn`` and ``get_region`` gets one connection, not two.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response
from fastapi import Path as PathParam

from touchgrass_api.db import SCHEMA, Conn

# Part of every ETag. A cached response is valid only for the same run AND the same response
# format: bump this whenever an endpoint's output changes for an unchanged run (e.g. a new tile
# attribute), or browsers keep serving the old version (found in Stage 9: tiles without park
# names after nearest_park_name was added).
RESPONSE_VERSION = 2

RegionId = Annotated[str, PathParam(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)]


def get_conn(request: Request) -> Iterator[Conn]:
    with request.app.state.pool.connection() as conn:
        yield conn


@dataclass(frozen=True)
class RegionCtx:
    region_id: str
    name: str
    analysis_crs: int
    config: dict[str, Any]
    run_id: str | None  # latest analysis run (D-023 Q4)
    metrics_params: dict[str, Any] | None  # that run's metrics record, if computed
    metrics_summary: dict[str, Any] | None

    @property
    def thresholds_m(self) -> dict[int, float]:
        """Threshold minutes → meters: from the metrics run if present, else the config."""
        if self.metrics_params:
            return {int(k): float(v) for k, v in self.metrics_params["thresholds_m"].items()}
        a = self.config.get("analysis", {})
        speed = float(a.get("walk_speed_kmh", 4.8)) * 1000 / 60
        return {int(t): t * speed for t in a.get("thresholds_min", [])}

    @property
    def underserved_minutes(self) -> int:
        metrics = (self.metrics_params or {}).get("metrics") or self.config.get("metrics") or {}
        return int(metrics.get("underserved_minutes", 10))

    @property
    def etag(self) -> str:
        return f'"{self.region_id}-{self.run_id or "none"}-v{RESPONSE_VERSION}"'


def get_region(region_id: RegionId, conn: Annotated[Conn, Depends(get_conn)]) -> RegionCtx:
    row = conn.execute(
        f"""SELECT r.region_id, r.name, r.analysis_crs, r.config, a.run_id,
                   m.params AS metrics_params, m.summary AS metrics_summary
            FROM {SCHEMA}.regions r
            LEFT JOIN {SCHEMA}.analysis_runs a ON a.region_id = r.region_id AND a.is_latest
            LEFT JOIN {SCHEMA}.metrics_runs m
                   ON m.region_id = a.region_id AND m.run_id = a.run_id
            WHERE r.region_id = %s""",
        (region_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"unknown region {region_id!r}")
    return RegionCtx(**row)


def require_metrics(region: Annotated[RegionCtx, Depends(get_region)]) -> RegionCtx:
    if region.run_id is None or region.metrics_params is None:
        raise HTTPException(404, f"no area metrics for {region.region_id!r}'s latest run")
    return region


def check_threshold(region: RegionCtx, threshold: int | None) -> int:
    t = region.underserved_minutes if threshold is None else threshold
    if t not in region.thresholds_m:
        raise HTTPException(
            422, f"threshold must be one of {sorted(region.thresholds_m)} minutes, got {t}"
        )
    return t


def area_types(conn: Conn, region_id: str) -> dict[str, int]:
    rows = conn.execute(
        f"""SELECT area_type, count(*) AS n FROM {SCHEMA}.analysis_areas
            WHERE region_id = %s GROUP BY area_type ORDER BY area_type""",
        (region_id,),
    ).fetchall()
    return {r["area_type"]: r["n"] for r in rows}


def check_area_type(conn: Conn, region_id: str, area_type: str) -> str:
    types = area_types(conn, region_id)
    if area_type not in types:
        raise HTTPException(404, f"unknown area type {area_type!r}; available: {sorted(types)}")
    return area_type


def cache_headers(response: Response, region: RegionCtx, max_age: int) -> None:
    """Data only changes when a new run is loaded, so the run id is the ETag (D-023 Q7)."""
    response.headers["ETag"] = region.etag
    response.headers["Cache-Control"] = f"public, max-age={max_age}"


def is_fresh(request: Request, region: RegionCtx) -> bool:
    """The client already holds this run's version: answer 304 Not Modified."""
    return region.etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]


def not_modified(region: RegionCtx, max_age: int) -> Response:
    response = Response(status_code=304)
    cache_headers(response, region, max_age)
    return response


Db = Annotated[Conn, Depends(get_conn)]
Region = Annotated[RegionCtx, Depends(get_region)]
RegionWithMetrics = Annotated[RegionCtx, Depends(require_metrics)]
