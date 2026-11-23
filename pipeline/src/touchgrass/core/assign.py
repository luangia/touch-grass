"""Assign points (block representative points) to polygons (boundaries, analysis areas).

Spatial join = **filter** (STRtree: which polygons' bounding boxes contain the point?)
+ **refine** (exact GEOS point-in-polygon test). GeoPandas builds the tree inside sjoin.

Predicate choice: ``intersects`` (interior *or* boundary) rather than ``within``
(interior only), so a point lying exactly on a shared edge is never silently dropped.
It is reported as belonging to both areas, which validation then flags for area types
that must not overlap.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry


def points_in_polygon(points: gpd.GeoSeries, polygon: BaseGeometry) -> pd.Series:
    """Boolean mask: which points intersect one (large, complex) polygon.

    ``prepare`` builds GEOS's internal index on the polygon's edges once, so each of the
    many point tests is ~O(log n) in the polygon's vertex count instead of O(n).
    """
    shapely.prepare(polygon)
    return pd.Series(shapely.intersects(polygon, points.values), index=points.index)


def assign_points_to_areas(
    points: gpd.GeoDataFrame, point_id: str, areas: gpd.GeoDataFrame, area_id: str
) -> pd.DataFrame:
    """Many-to-many membership table with columns (point_id, area_id).

    A point inside two overlapping areas yields two rows; a point in no area yields none.
    """
    if points.crs != areas.crs:
        raise ValueError(f"CRS mismatch: points {points.crs} vs areas {areas.crs}")
    joined = gpd.sjoin(
        points[[point_id, "geometry"]],
        areas[[area_id, "geometry"]],
        how="inner",
        predicate="intersects",
    )
    return (
        joined[[point_id, area_id]]
        .drop_duplicates()
        .sort_values([area_id, point_id])
        .reset_index(drop=True)
    )
