"""Park access points: where the walk network reaches each park (D-006, amended by D-020).

Two methods, both producing a position on a network edge (so Stage 6 can seed Dijkstra
from points that are not graph nodes, via a virtual source):

``edge_contact``  every edge that comes within ``tolerance`` of the park's edge. The
                  stretch of the edge inside the park (grown by ``tolerance``) is the
                  contact interval [start, end] in metres from u, so reaching the park
                  costs ``start`` from u or ``length - end`` from v. Paths that cross into
                  the park and sidewalks along it are both covered.
``entrance``      an OSM entrance / gate node within ``tolerance`` of the park's edge,
                  unless its ``access`` tag excludes the public; snapped onto its edge.

``gap``           only for a park with neither: the nearest network within ``max_gap``
                  whose straight gap to the park crosses no water (a set-back natural
                  area reached by an unmapped trail or a street end). The gap length is
                  added to both offsets, so it costs walking distance.

A park with none of these (e.g. a river island: every gap crosses water) is reported as
``no_network_contact``. D-006's old sampled fallback is gone: it bridged rivers.

``offset_u_m`` / ``offset_v_m`` always mean "walking cost from that end of the edge to the
park", so Stage 6 can use them directly as virtual-source weights.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from touchgrass.core.geometry import require_crs
from touchgrass.core.snap import TOUCH_M, snap_to_edges, water_crossing_m

EDGE_CONTACT = "edge_contact"
ENTRANCE = "entrance"
GAP = "gap"
NO_CONTACT = "no_network_contact"
COLUMNS = [
    "access_id",
    "park_id",
    "method",
    "edge_id",
    "u",
    "v",
    "offset_u_m",
    "offset_v_m",
    "contact_start_m",
    "contact_end_m",
    "osm_node_id",
    "gap_m",
    "geometry",
]


@dataclass
class AccessResult:
    points: gpd.GeoDataFrame  # COLUMNS, analysis CRS
    per_park: pd.DataFrame  # park_id, name, edge_contacts, entrances, status
    entrances_excluded_access: int
    entrances_off_network: int


def _edge_contacts(
    parks: gpd.GeoDataFrame, edges: gpd.GeoDataFrame, tolerance: float
) -> gpd.GeoDataFrame:
    rings = gpd.GeoDataFrame(
        {"park_id": parks["park_id"]}, geometry=parks.boundary.buffer(tolerance), crs=parks.crs
    )
    pairs = gpd.sjoin(edges[["geometry"]], rings, predicate="intersects", how="inner")
    if pairs.empty:
        return gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=edges.crs)
    edge_pos = pairs.index.to_numpy()
    park_pos = pairs["index_right"].to_numpy()
    lines = np.asarray(edges.geometry.values)[edge_pos]
    grown = np.asarray(parks.geometry.buffer(tolerance).values)[park_pos]
    inside = shapely.intersection(lines, grown)

    # Contact interval = min / max position along the edge of any point of the contact.
    coords, owner = shapely.get_coordinates(inside, return_index=True)
    along = shapely.line_locate_point(lines[owner], shapely.points(coords))
    span = (
        pd.DataFrame({"pair": owner, "along": along}).groupby("pair")["along"].agg(["min", "max"])
    )
    geom_len = shapely.length(lines)[span.index]
    length = edges["length_m"].to_numpy()[edge_pos][span.index]
    scale = np.where(geom_len > 0, length / geom_len, 0.0)
    start = span["min"].to_numpy() * scale
    end = span["max"].to_numpy() * scale
    mid = shapely.line_interpolate_point(
        lines[span.index], (span["min"] + span["max"]).to_numpy() / 2
    )

    rows = edges.iloc[edge_pos[span.index]].reset_index(drop=True)
    pid = parks["park_id"].to_numpy()[park_pos[span.index]]
    return gpd.GeoDataFrame(
        {
            "access_id": [f"{p}|{e}" for p, e in zip(pid, rows["edge_id"], strict=True)],
            "park_id": pid,
            "method": EDGE_CONTACT,
            "edge_id": rows["edge_id"],
            "u": rows["u"],
            "v": rows["v"],
            "offset_u_m": start,
            "offset_v_m": np.clip(length - end, 0, None),  # no -1e-12 from float noise
            "contact_start_m": start,
            "contact_end_m": end,
            "osm_node_id": pd.array([pd.NA] * len(rows), dtype="Int64"),
        },
        geometry=list(mid),
        crs=edges.crs,
    )


def _entrances(
    parks: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    entrances: gpd.GeoDataFrame,
    tolerance: float,
    exclude_access: list[str],
) -> tuple[gpd.GeoDataFrame, int, int]:
    empty = gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=edges.crs)
    if entrances.empty:
        return empty, 0, 0
    rings = gpd.GeoDataFrame(
        {"park_id": parks["park_id"]}, geometry=parks.boundary.buffer(tolerance), crs=parks.crs
    )
    near = gpd.sjoin(entrances, rings, predicate="within", how="inner").drop(columns="index_right")
    public = ~near["access"].isin(exclude_access)
    excluded = int((~public).sum())
    near = near[public]
    if near.empty:
        return empty, excluded, 0
    snapped = snap_to_edges(near.geometry, edges)
    on_network = snapped["connector_m"].to_numpy() <= tolerance
    off = int((~on_network).sum())
    near, snapped = near[on_network], snapped[on_network]
    return (
        gpd.GeoDataFrame(
            {
                "access_id": [
                    f"{p}|n{n}" for p, n in zip(near["park_id"], near["osm_node_id"], strict=True)
                ],
                "park_id": near["park_id"].to_numpy(),
                "method": ENTRANCE,
                "edge_id": snapped["edge_id"].to_numpy(),
                "u": snapped["u"].to_numpy(),
                "v": snapped["v"].to_numpy(),
                "offset_u_m": snapped["offset_u_m"].to_numpy(),
                "offset_v_m": snapped["offset_v_m"].to_numpy(),
                "contact_start_m": snapped["offset_u_m"].to_numpy(),
                "contact_end_m": snapped["offset_u_m"].to_numpy(),
                "osm_node_id": pd.array(near["osm_node_id"].to_numpy(), dtype="Int64"),
            },
            geometry=list(snapped["snapped"]),
            crs=edges.crs,
        ),
        excluded,
        off,
    )


def _gaps(
    parks: gpd.GeoDataFrame, edges: gpd.GeoDataFrame, water: BaseGeometry | None, max_gap: float
) -> gpd.GeoDataFrame:
    """Dry straight-line gaps for parks that have no contact and no entrance."""
    geoms = np.asarray(edges.geometry.values)
    tree = shapely.STRtree(geoms)
    length = edges["length_m"].to_numpy()
    rows = []
    for park_id, park in zip(parks["park_id"], parks.geometry, strict=True):
        cand = tree.query(park, predicate="dwithin", distance=max_gap)
        dist = shapely.distance(park, geoms[cand])
        for j in cand[np.lexsort((cand, dist))]:
            line = shapely.shortest_line(park, geoms[j])  # starts on the park, ends on the edge
            if water_crossing_m([line], water)[0] > TOUCH_M:
                continue
            p_on = shapely.Point(line.coords[-1])
            along = shapely.line_locate_point(geoms[j], p_on)
            frac = along / geoms[j].length if geoms[j].length > 0 else 0.0
            gap = float(line.length)
            rows.append(
                {
                    "access_id": f"{park_id}|gap|{edges['edge_id'].iat[j]}",
                    "park_id": park_id,
                    "method": GAP,
                    "edge_id": edges["edge_id"].iat[j],
                    "u": edges["u"].iat[j],
                    "v": edges["v"].iat[j],
                    "offset_u_m": frac * length[j] + gap,
                    "offset_v_m": max(length[j] - frac * length[j], 0.0) + gap,
                    "contact_start_m": frac * length[j],
                    "contact_end_m": frac * length[j],
                    "osm_node_id": pd.NA,
                    "gap_m": gap,
                    "geometry": p_on,
                }
            )
            break
    if not rows:
        return gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=edges.crs)
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=edges.crs)
    out["osm_node_id"] = out["osm_node_id"].astype("Int64")
    return out


def park_access_points(
    parks: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    entrances: gpd.GeoDataFrame,
    *,
    tolerance: float,
    exclude_access: list[str],
    water: BaseGeometry | None = None,
    max_gap: float = 0.0,
) -> AccessResult:
    """Access points for every park; all inputs in the same (analysis) CRS."""
    epsg = edges.crs.to_epsg()
    require_crs(parks, epsg, "parks")
    require_crs(entrances, epsg, "entrances")
    parks = parks.reset_index(drop=True)
    contacts = _edge_contacts(parks, edges, tolerance)
    gates, excluded, off = _entrances(parks, edges, entrances, tolerance, exclude_access)
    found = set(contacts["park_id"]) | set(gates["park_id"])
    gaps = (
        _gaps(parks[~parks["park_id"].isin(found)], edges, water, max_gap)
        if max_gap > 0
        else gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=edges.crs)
    )
    for frame in (contacts, gates):
        frame["gap_m"] = 0.0
    frames = [f for f in (contacts, gates, gaps) if not f.empty]
    if frames:
        points = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=edges.crs)
    else:
        points = gpd.GeoDataFrame(columns=COLUMNS, geometry="geometry", crs=edges.crs)
    points = points[COLUMNS].sort_values("access_id", ignore_index=True)

    counts = points.groupby(["park_id", "method"]).size().unstack(fill_value=0)
    per_park = parks[["park_id", "name"]].copy()
    per_park["edge_contacts"] = per_park["park_id"].map(counts.get(EDGE_CONTACT, {})).fillna(0)
    per_park["entrances"] = per_park["park_id"].map(counts.get(ENTRANCE, {})).fillna(0)
    per_park["gap_m"] = per_park["park_id"].map(
        points.loc[points["method"] == GAP].set_index("park_id")["gap_m"]
    )
    per_park[["edge_contacts", "entrances"]] = per_park[["edge_contacts", "entrances"]].astype(int)
    direct = (per_park["edge_contacts"] + per_park["entrances"]) > 0
    per_park["status"] = np.select(
        [direct, per_park["gap_m"].notna()], ["reachable", "reachable_via_gap"], NO_CONTACT
    )
    return AccessResult(points, per_park, excluded, off)
