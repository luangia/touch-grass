"""Geometry cleaning and measurement helpers shared by every region.

Conventions: inputs and outputs are in EPSG:4326 (storage CRS) unless a function says
otherwise; anything that measures (buffer, area) projects to the region's analysis CRS
first and checks that CRS with :func:`assert_analysis_crs`.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from touchgrass.core.crs import STORAGE_EPSG, assert_analysis_crs


def to_multipolygon(geom: BaseGeometry | None) -> MultiPolygon | None:
    """Repair a geometry and keep only its polygonal parts, as a MultiPolygon.

    ``make_valid`` can turn a self-intersecting polygon into a GeometryCollection that
    also contains stray lines or points; those have no area and are dropped. Returning
    MultiPolygon for everything gives every row the same type (PostGIS columns are typed).
    """
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    polygons: list[Polygon] = []
    for part in getattr(geom, "geoms", [geom]):
        if isinstance(part, Polygon):
            polygons.append(part)
        elif isinstance(part, MultiPolygon):
            polygons.extend(part.geoms)
        # LineString / Point fragments from make_valid are discarded deliberately.
    if not polygons:
        return None
    return MultiPolygon(polygons)


def clean_polygons(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, int]:
    """Repair geometries, drop empty ones. Returns (clean_gdf, number_repaired)."""
    invalid = int((~gdf.geometry.is_valid).sum())
    out = gdf.copy()
    out["geometry"] = [to_multipolygon(g) for g in out.geometry]
    out = out[out.geometry.notna()]
    return gpd.GeoDataFrame(out, geometry="geometry", crs=gdf.crs), invalid


def buffer_in_crs(geom: BaseGeometry, distance_m: float, analysis_epsg: int) -> BaseGeometry:
    """Buffer a storage-CRS geometry by ``distance_m`` meters, measured in the analysis CRS."""
    assert_analysis_crs(analysis_epsg)
    series = gpd.GeoSeries([geom], crs=STORAGE_EPSG).to_crs(analysis_epsg)
    return series.buffer(distance_m).to_crs(STORAGE_EPSG).iloc[0]


def area_m2(gdf: gpd.GeoDataFrame | gpd.GeoSeries, analysis_epsg: int) -> pd.Series:
    """Planar area in square meters, computed in the analysis CRS."""
    assert_analysis_crs(analysis_epsg)
    return gdf.to_crs(analysis_epsg).area


def parse_coordinate(values: pd.Series) -> pd.Series:
    """Census writes internal points as signed strings such as '+45.5231000'."""
    return pd.to_numeric(values.astype(str).str.strip(), errors="raise")


def representative_points(
    polygons: gpd.GeoSeries, lon: pd.Series, lat: pd.Series
) -> tuple[gpd.GeoSeries, pd.Series]:
    """Use a published interior point per polygon; fall back where it isn't inside.

    Returns (points, source) where source is 'census_intpt' or 'point_on_surface'.
    GEOS PointOnSurface (GeoPandas: ``representative_point()``) is guaranteed to lie
    inside its polygon; a centroid is not. Only computed where the given point fails.
    """
    given = gpd.GeoSeries(gpd.points_from_xy(lon, lat), index=polygons.index, crs=polygons.crs)
    inside = polygons.intersects(given, align=False)
    points = given.copy()
    if (~inside).any():
        points[~inside] = polygons[~inside].representative_point()
    source = pd.Series("census_intpt", index=polygons.index).where(inside, "point_on_surface")
    return gpd.GeoSeries(points, crs=polygons.crs), source


def require_crs(gdf: Any, epsg: int, what: str) -> None:
    crs = getattr(gdf, "crs", None)
    if crs is None or crs.to_epsg() != epsg:
        raise ValueError(f"{what}: expected EPSG:{epsg}, got {crs}")
