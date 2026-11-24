"""Map layers: parks (GeoJSON) and census blocks (Mapbox Vector Tiles from PostGIS, D-010)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi import Path as PathParam

from touchgrass_api.db import SCHEMA
from touchgrass_api.deps import Db, Region, cache_headers, is_fresh, not_modified
from touchgrass_api.routers.areas import GEOJSON, SIMPLIFY_M, geojson_geometry

router = APIRouter(prefix="/api/v1/regions/{region_id}", tags=["layers"])

MVT = "application/vnd.mapbox-vector-tile"
MIN_ZOOM, MAX_ZOOM = 12, 14  # D-023 Q6: every z12 tile < 50 ms; MapLibre overzooms past 14
EXTENT, BUFFER = 4096, 64

_PARKS = f"""
SELECT json_build_object(
    'type', 'FeatureCollection', 'region_id', %(region)s::text,
    'features', coalesce(json_agg(json_build_object(
        'type', 'Feature', 'id', p.park_id,
        'geometry', {geojson_geometry("p.geom")},
        'properties', json_build_object('park_id', p.park_id, 'name', p.name,
                                        'park_class', p.park_class, 'area_m2', p.area_m2)
    ) ORDER BY p.park_id), '[]'::json))::text AS body
FROM {SCHEMA}.parks p
WHERE p.region_id = %(region)s
"""


@router.get(
    "/parks",
    response_class=Response,
    responses={200: {"content": {GEOJSON: {}}, "description": "GeoJSON FeatureCollection"}},
)
def parks_geojson(region: Region, conn: Db, request: Request) -> Response:
    """Every park (WGS 84, simplified to 5 m)."""
    max_age = request.app.state.settings.cache_max_age_s
    if is_fresh(request, region):
        return not_modified(region, max_age)
    row = conn.execute(
        _PARKS, {"region": region.region_id, "crs": region.analysis_crs, "tol": SIMPLIFY_M}
    ).fetchone()
    assert row is not None
    response = Response(row["body"], media_type=GEOJSON)
    cache_headers(response, region, max_age)
    return response


# The tile envelope is transformed to the storage CRS so the GiST index on geom can be used;
# transforming every block to 3857 before the && test would defeat it (lesson §5.5).
# MATERIALIZED: without it the planner inlines the CTE and pushes `geom IS NOT NULL` into the
# scan, computing ST_AsMVTGeom twice per block (z12: 108 → 69 ms; same features).
_TILE = f"""
WITH env AS (SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS e),
b AS MATERIALIZED (
    SELECT ST_AsMVTGeom(ST_Transform(cb.geom, 3857), env.e, {EXTENT}, {BUFFER}, true) AS geom,
           cb.geoid20, ba.pop20, ba.reachable, ba.network_m, ba.walk_min,
           ba.nearest_park_id, pk.name AS nearest_park_name, r.parks_reachable,
           r.reachable_park_ha
    FROM env, {SCHEMA}.census_blocks cb
    JOIN {SCHEMA}.block_access ba
      ON ba.region_id = cb.region_id AND ba.geoid20 = cb.geoid20 AND ba.run_id = %(run)s
    LEFT JOIN {SCHEMA}.block_park_reach r
      ON r.region_id = ba.region_id AND r.run_id = ba.run_id AND r.geoid20 = ba.geoid20
    -- Unnamed parks have no name: the attribute is then absent from the tile (MVT drops NULLs).
    LEFT JOIN {SCHEMA}.parks pk
      ON pk.region_id = ba.region_id AND pk.park_id = ba.nearest_park_id
    WHERE cb.region_id = %(region)s
      AND cb.geom && ST_Transform(env.e, 4326)
)
SELECT ST_AsMVT(b, 'blocks', {EXTENT}, 'geom') AS tile FROM b WHERE b.geom IS NOT NULL
"""


@router.get(
    "/tiles/blocks/{z}/{x}/{y}.pbf",
    response_class=Response,
    responses={
        200: {"content": {MVT: {}}, "description": "Layer 'blocks': populated census blocks"},
        204: {"description": "No blocks in this tile"},
    },
)
def block_tile(
    region: Region,
    conn: Db,
    request: Request,
    z: Annotated[int, PathParam(ge=MIN_ZOOM, le=MAX_ZOOM)],
    x: Annotated[int, PathParam(ge=0)],
    y: Annotated[int, PathParam(ge=0)],
) -> Response:
    """Populated blocks with walking distance to the nearest park (latest run), z12-14."""
    if x >= 2**z or y >= 2**z:
        raise HTTPException(422, f"tile {z}/{x}/{y} is outside the z{z} grid")
    if region.run_id is None:
        raise HTTPException(404, f"no analysis run for {region.region_id!r}")
    max_age = request.app.state.settings.cache_max_age_s
    if is_fresh(request, region):
        return not_modified(region, max_age)
    row = conn.execute(
        _TILE, {"z": z, "x": x, "y": y, "run": region.run_id, "region": region.region_id}
    ).fetchone()
    tile = bytes(row["tile"]) if row and row["tile"] is not None else b""
    response = Response(tile, media_type=MVT, status_code=200 if tile else 204)
    cache_headers(response, region, max_age)
    return response
