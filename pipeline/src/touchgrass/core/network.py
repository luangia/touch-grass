"""Build the analysis walk graph from filtered OSM XML (DECISIONS.md D-005, D-020).

    .osm XML ──OSMnx──▶ simplified MultiDiGraph (every way walkable both ways)
             ──clip to the analysis buffer (keep edges that cross it)
             ──project to the analysis CRS ──undirected MultiGraph
             ──label connected components (0 = largest)

Simplification merges chains of shape-only nodes into one edge whose ``geometry`` keeps
the curve, so lengths are unchanged but there are ~4x fewer nodes. A MultiGraph is kept
(not a simple Graph) because two different paths can join the same two nodes.

Outputs are GeoDataFrames in the analysis CRS with stable ids and a canonical edge
orientation (``u < v``, geometry running from u to v), so offsets measured along an edge
always mean "metres from u".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from touchgrass.core.crs import STORAGE_EPSG, assert_analysis_crs

CLIP_NEAR_M = 50.0  # a piece with a node this close to the clip line may continue beyond it


@dataclass
class WalkGraph:
    nodes: gpd.GeoDataFrame  # node_id, component, street_count, geometry (analysis CRS)
    edges: gpd.GeoDataFrame  # edge_id, u, v, key, length_m, highway, name, osm_way_ids,
    #                          component, geometry (analysis CRS, oriented u -> v)
    components: list[dict[str, Any]] = field(default_factory=list)  # largest first

    def main(self) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        """Nodes and edges of the largest component (component 0)."""
        return (
            self.nodes[self.nodes["component"] == 0].reset_index(drop=True),
            self.edges[self.edges["component"] == 0].reset_index(drop=True),
        )


def _flat(value: Any) -> str | None:
    """OSMnx stores merged attributes as lists; keep them as sorted ';'-joined text."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, list):
        return ";".join(sorted({str(v) for v in value}))
    return str(value)


def _oriented(geom: LineString, start: tuple[float, float]) -> LineString:
    """``geom`` reversed if needed so that it starts at ``start``."""
    first, last = geom.coords[0], geom.coords[-1]
    d_first = (first[0] - start[0]) ** 2 + (first[1] - start[1]) ** 2
    d_last = (last[0] - start[0]) ** 2 + (last[1] - start[1]) ** 2
    return geom if d_first <= d_last else shapely.reverse(geom)


def build_walk_graph(xml: Path, clip_area: BaseGeometry, analysis_epsg: int) -> WalkGraph:
    """Graph from OSM XML, clipped to ``clip_area`` (EPSG:4326), in the analysis CRS."""
    assert_analysis_crs(analysis_epsg)
    graph = ox.graph_from_xml(xml, bidirectional=True, simplify=True, retain_all=True)
    graph = ox.truncate.truncate_graph_polygon(graph, clip_area, truncate_by_edge=True)
    graph = ox.project_graph(graph, to_crs=f"EPSG:{analysis_epsg}")
    undirected = ox.convert.to_undirected(graph)

    # Components, largest first; ties broken by smallest node id so labels are stable.
    comps = sorted(nx.connected_components(undirected), key=lambda c: (-len(c), min(c)))
    label = {n: i for i, comp in enumerate(comps) for n in comp}

    node_rows = [
        {
            "node_id": int(n),
            "component": label[n],
            "street_count": int(d.get("street_count", 0)),
            "geometry": shapely.Point(d["x"], d["y"]),
        }
        for n, d in undirected.nodes(data=True)
    ]
    xy = {n: (d["x"], d["y"]) for n, d in undirected.nodes(data=True)}
    edge_rows = []
    for a, b, k, d in undirected.edges(keys=True, data=True):
        u, v = (a, b) if a <= b else (b, a)
        geom = d.get("geometry") or LineString([xy[u], xy[v]])
        edge_rows.append(
            {
                "u": int(u),
                "v": int(v),
                "key": int(k),
                "osmnx_length_m": float(d["length"]),
                "highway": _flat(d.get("highway")),
                "name": _flat(d.get("name")),
                "osm_way_ids": _flat(d.get("osmid")),
                "component": label[u],
                "geometry": _oriented(geom, xy[u]),
            }
        )
    crs = f"EPSG:{analysis_epsg}"
    nodes = gpd.GeoDataFrame(node_rows, geometry="geometry", crs=crs)
    nodes = nodes.sort_values("node_id", ignore_index=True)
    edges = gpd.GeoDataFrame(edge_rows, geometry="geometry", crs=crs)
    # Edge weight = length in the analysis CRS (D-008). OSMnx's own `length` is haversine
    # on a sphere (R = 6,371,009 m): at 45° N it reads east-west streets ~0.28% short and
    # north-south ~0.1% long, so it is kept only for comparison.
    edges.insert(edges.columns.get_loc("osmnx_length_m"), "length_m", edges.geometry.length)
    # A stable key: parallel edges between the same nodes are ordered by length, then way.
    edges = edges.sort_values(["u", "v", "length_m", "osm_way_ids"], ignore_index=True)
    edges["key"] = edges.groupby(["u", "v"]).cumcount()
    edges.insert(
        0,
        "edge_id",
        [f"{u}-{v}-{k}" for u, v, k in edges[["u", "v", "key"]].itertuples(index=False)],
    )

    # Pieces the clip line cut: they may connect to the rest only outside the clip area.
    clip_line = gpd.GeoSeries([clip_area], crs=STORAGE_EPSG).to_crs(crs).boundary.iloc[0]
    near_clip = shapely.dwithin(np.asarray(nodes.geometry.values), clip_line, CLIP_NEAR_M)
    clipped = set(nodes.loc[near_clip, "component"])

    components = []
    for i, comp in enumerate(comps):
        members = nodes[nodes["component"] == i]
        centre = gpd.GeoSeries([members.union_all().centroid], crs=crs).to_crs(STORAGE_EPSG)[0]
        components.append(
            {
                "component": i,
                "nodes": len(comp),
                "edges": int((edges["component"] == i).sum()),
                "km": round(float(edges.loc[edges["component"] == i, "length_m"].sum()) / 1000, 3),
                "centroid_lonlat": [round(centre.x, 5), round(centre.y, 5)],
                "touches_clip": i in clipped,
            }
        )
    return WalkGraph(nodes, edges, components)


def length_agreement(edges: gpd.GeoDataFrame) -> dict[str, float]:
    """Projected lengths vs OSMnx's spherical lengths: a units/CRS sanity check (< 1%)."""
    long_enough = edges[edges["length_m"] > 1]
    rel = (long_enough["length_m"] - long_enough["osmnx_length_m"]).abs() / long_enough["length_m"]
    return {"median_rel_diff": float(rel.median()), "max_rel_diff": float(rel.max())}
