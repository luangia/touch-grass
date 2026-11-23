"""Snap points onto network edges (DECISIONS.md D-020 Q4).

For a point X and its nearest edge (u, v) with nearest point P on that edge:

    connector_m = |X - P|                       straight walk from X to the street
    offset_u_m  = distance along the edge from u to P
    offset_v_m  = edge length - offset_u_m

so the network distance from X is ``connector + min(offset_u + D(u), offset_v + D(v))``.
Snapping to the nearest *node* instead would add the walk to the corner (tens of metres
on a typical block, more on long blocks).

Search: an STRtree over edge geometries (bounding-box filter, exact distance refine).
Ties are broken by the edge order given, so results are deterministic.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from touchgrass.core.geometry import require_crs


def snap_to_edges(points: gpd.GeoSeries, edges: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per point: edge_id, u, v, connector_m, offset_u_m, offset_v_m, snapped.

    ``points`` and ``edges`` must share a projected (metric) CRS; edges are oriented u→v.
    ``snapped`` is the shapely Point P on the edge.
    """
    require_crs(points, edges.crs.to_epsg(), "points to snap")
    if len(edges) == 0:
        raise ValueError("no edges to snap to")
    geoms = np.asarray(edges.geometry.values)
    pts = np.asarray(points.values)
    tree = shapely.STRtree(geoms)
    pt_idx, edge_idx = tree.query_nearest(pts, all_matches=True)
    # Lowest edge index wins a tie (several edges exactly as near, e.g. at a node).
    best = pd.DataFrame({"p": pt_idx, "e": edge_idx}).groupby("p")["e"].min()
    chosen = best.reindex(range(len(pts))).to_numpy()

    lines = geoms[chosen]
    along = shapely.line_locate_point(lines, pts)  # metres from the geometry's start (u)
    frac = np.where(shapely.length(lines) > 0, along / shapely.length(lines), 0.0)
    length = edges["length_m"].to_numpy()[chosen]
    offset_u = frac * length  # scaled to the edge's geodesic length used as its weight
    snapped = shapely.line_interpolate_point(lines, along)
    return pd.DataFrame(
        {
            "edge_id": edges["edge_id"].to_numpy()[chosen],
            "u": edges["u"].to_numpy()[chosen],
            "v": edges["v"].to_numpy()[chosen],
            "connector_m": shapely.distance(pts, snapped),
            "offset_u_m": offset_u,
            "offset_v_m": np.clip(length - offset_u, 0, None),
            "snapped": snapped,
        },
        index=points.index,
    )


# Crossings shorter than this are numerical touches (a street drawn along a shore), not
# water. It is far below the width of any real channel to an island.
TOUCH_M = 0.5


def water_crossing_m(lines: Any, water: BaseGeometry | None) -> np.ndarray:
    """Length of each line that lies over ``water`` (0 where there is no water layer)."""
    lines = np.asarray(lines)
    if water is None or water.is_empty:
        return np.zeros(len(lines))
    return np.asarray(shapely.length(shapely.intersection(lines, water)))


def snap_to_edges_dry(
    points: gpd.GeoSeries,
    edges: gpd.GeoDataFrame,
    water: BaseGeometry | None,
    max_m: float,
) -> pd.DataFrame:
    """Like :func:`snap_to_edges`, but the straight connector may not cross water.

    For the few points whose nearest edge is across water, the nearest edge within
    ``max_m`` reachable over dry land is used instead. Column ``dry`` is False where no
    such edge exists (the row then holds the plain nearest edge, so the caller decides).
    """
    out = snap_to_edges(points, edges)
    pts = np.asarray(points.values)
    connectors = shapely.shortest_line(pts, np.asarray(list(out["snapped"])))
    wet = water_crossing_m(connectors, water) > TOUCH_M
    out["dry"] = ~wet
    if not wet.any():
        return out
    geoms = np.asarray(edges.geometry.values)
    tree = shapely.STRtree(geoms)
    length = edges["length_m"].to_numpy()
    for i in np.flatnonzero(wet):
        cand = tree.query(pts[i], predicate="dwithin", distance=max_m)
        dist = shapely.distance(pts[i], geoms[cand])
        for j in cand[np.lexsort((cand, dist))]:  # nearest first; ties by edge order
            p_on = shapely.line_interpolate_point(
                geoms[j], shapely.line_locate_point(geoms[j], pts[i])
            )
            line = shapely.LineString([pts[i], p_on])
            if water_crossing_m([line], water)[0] > TOUCH_M:
                continue
            along = shapely.line_locate_point(geoms[j], p_on)
            frac = along / geoms[j].length if geoms[j].length > 0 else 0.0
            row = out.index[i]
            out.loc[row, ["edge_id", "u", "v"]] = [
                edges["edge_id"].iat[j],
                edges["u"].iat[j],
                edges["v"].iat[j],
            ]
            out.loc[row, ["connector_m", "offset_u_m", "offset_v_m"]] = [
                line.length,
                frac * length[j],
                max(length[j] - frac * length[j], 0.0),
            ]
            out.at[row, "snapped"] = p_on
            out.at[row, "dry"] = True
            break
    return out
