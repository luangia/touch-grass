"""Network distance from every block to its nearest park (DECISIONS.md D-005, D-021).

One multi-source Dijkstra over the walk graph:

    graph nodes 0..n-1        (OSM nodes; every piece, so isolated origins can be routed)
    access nodes n..n+k-1     one per park access point, joined to its edge's ends u and v
                              with weights offset_u_m / offset_v_m (cost to the park)

``scipy.sparse.csgraph.dijkstra(indices=access_nodes, min_only=True)`` returns, for every
node, the distance to the nearest access node and *which* one it was. A block's distance
is then the shortest of: via u, via v, or straight along its own edge to an access
stretch on that edge (the "same-edge" case).

Two SciPy traps handled here:
  * duplicate (row, col) entries are SUMMED when a sparse matrix is built, so parallel
    edges are reduced to their minimum first;
  * an explicitly stored 0 is an edge (a contact that starts exactly at a node), but
    ``eliminate_zeros()`` would silently delete it. It is never called.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

VIA_NODE = "via_node"
SAME_EDGE = "same_edge"


@dataclass
class RoutingGraph:
    matrix: csr_matrix  # symmetric weights, (n + k) x (n + k)
    nodes: pd.Index  # OSM node id of graph node i
    access_ids: np.ndarray  # access id of access node n + j
    access_parks: np.ndarray  # park id of access node n + j

    @property
    def n_graph(self) -> int:
        return len(self.nodes)


def build_routing_graph(edges: pd.DataFrame, access: pd.DataFrame) -> RoutingGraph:
    """``edges``: u, v, length_m. ``access``: access_id, park_id, u, v, offset_u_m, offset_v_m."""
    if (edges["length_m"] < 0).any() or (access[["offset_u_m", "offset_v_m"]] < 0).any().any():
        raise ValueError("negative weight: Dijkstra requires weights >= 0")
    nodes = pd.Index(np.unique(np.r_[edges["u"].to_numpy(), edges["v"].to_numpy()]))
    n, k = len(nodes), len(access)
    if (
        nodes.get_indexer(access["u"]).min(initial=0) < 0
        or nodes.get_indexer(access["v"]).min(initial=0) < 0
    ):
        raise ValueError("an access point refers to a node that is not in the graph")
    a = np.arange(n, n + k)
    rows = np.r_[nodes.get_indexer(edges["u"]), a, a]
    cols = np.r_[
        nodes.get_indexer(edges["v"]),
        nodes.get_indexer(access["u"]),
        nodes.get_indexer(access["v"]),
    ]
    weights = np.r_[edges["length_m"], access["offset_u_m"], access["offset_v_m"]].astype(float)
    r, c, w = _undirected(rows, cols, weights)
    matrix = csr_matrix((w, (r, c)), shape=(n + k, n + k))
    return RoutingGraph(matrix, nodes, access["access_id"].to_numpy(), access["park_id"].to_numpy())


def _undirected(
    rows: np.ndarray, cols: np.ndarray, weights: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Store each link once as (low, high), keep the cheapest parallel, then mirror."""
    lo, hi = np.minimum(rows, cols), np.maximum(rows, cols)
    reduced = (
        pd.DataFrame({"lo": lo, "hi": hi, "w": weights})
        .groupby(["lo", "hi"], as_index=False, sort=True)["w"]
        .min()
    )
    reduced = reduced[reduced["lo"] != reduced["hi"]]  # self-loops never shorten a path
    return (
        np.r_[reduced["lo"], reduced["hi"]],
        np.r_[reduced["hi"], reduced["lo"]],
        np.r_[reduced["w"], reduced["w"]],
    )


def nearest_access(graph: RoutingGraph) -> tuple[pd.Series, pd.Series]:
    """Per OSM node: distance (m) to the nearest access point, and that access id."""
    n = graph.n_graph
    k = len(graph.access_ids)
    if k == 0:
        inf = pd.Series(np.inf, index=graph.nodes)
        return inf, pd.Series(None, index=graph.nodes, dtype=object)
    dist, _pred, src = dijkstra(
        graph.matrix,
        directed=False,
        indices=np.arange(n, n + k),
        min_only=True,
        return_predecessors=True,
    )
    reached = src[:n] >= 0
    ids = np.full(n, None, dtype=object)
    ids[reached] = graph.access_ids[src[:n][reached] - n]
    return pd.Series(dist[:n], index=graph.nodes), pd.Series(ids, index=graph.nodes, dtype=object)


def block_distances(
    origins: pd.DataFrame, access: pd.DataFrame, node_dist: pd.Series, node_access: pd.Series
) -> pd.DataFrame:
    """Network distance for each origin: connector + min(via u, via v, along its own edge).

    ``origins``: geoid20, edge_id, u, v, connector_m, offset_u_m, offset_v_m.
    Returns geoid20, network_m (inf if unreachable), route_kind, nearest_access_id.
    """
    du = node_dist.reindex(origins["u"]).to_numpy()
    dv = node_dist.reindex(origins["v"]).to_numpy()
    via_u = origins["offset_u_m"].to_numpy() + du
    via_v = origins["offset_v_m"].to_numpy() + dv
    use_u = via_u <= via_v
    best = np.where(use_u, via_u, via_v)
    best_access = np.where(
        use_u,
        node_access.reindex(origins["u"]).to_numpy(),
        node_access.reindex(origins["v"]).to_numpy(),
    )
    kind = np.full(len(origins), VIA_NODE, dtype=object)

    # Same edge: walk along the block's own edge to an access stretch [start, end] on it.
    pairs = (
        origins[["edge_id", "offset_u_m"]]
        .reset_index(names="row")
        .merge(
            access[["edge_id", "access_id", "contact_start_m", "contact_end_m", "gap_m"]],
            on="edge_id",
        )
    )
    if len(pairs):
        p = pairs["offset_u_m"].to_numpy()
        along = np.maximum(
            0.0, np.maximum(pairs["contact_start_m"] - p, p - pairs["contact_end_m"])
        )
        pairs["direct"] = along + pairs["gap_m"].fillna(0.0).to_numpy()
        pairs = pairs.sort_values(["row", "direct", "access_id"])
        first = pairs.drop_duplicates("row")
        rows = first["row"].to_numpy()
        shorter = first["direct"].to_numpy() < best[rows]
        best[rows[shorter]] = first["direct"].to_numpy()[shorter]
        best_access[rows[shorter]] = first["access_id"].to_numpy()[shorter]
        kind[rows[shorter]] = SAME_EDGE

    network = origins["connector_m"].to_numpy() + best
    reachable = np.isfinite(network)
    best_access[~reachable] = None
    kind[~reachable] = None
    return pd.DataFrame(
        {
            "geoid20": origins["geoid20"].to_numpy(),
            "network_m": network,
            "route_kind": kind,
            "nearest_access_id": best_access,
        }
    )


def parks_within(
    edges: pd.DataFrame, access: pd.DataFrame, origins: pd.DataFrame, limit_m: float
) -> pd.DataFrame:
    """Every (block, park) pair with a walk of at most ``limit_m`` (DECISIONS.md D-022 Q4).

    One bounded multi-source search per park, from that park's access points only.
    Other parks' access nodes must NOT be in the graph: a contact-stretch access node
    joins its edge's two ends with offset_u + offset_v < edge length (the stretch is
    skipped), so as a pass-through node it would be a shortcut. In the nearest-park
    search that is harmless (every access node is a source at distance 0); here it is not.

    Returns geoid20, park_id, network_m, sorted.
    """
    nodes = pd.Index(np.unique(np.r_[edges["u"].to_numpy(), edges["v"].to_numpy()]))
    n = len(nodes)
    base_r, base_c, base_w = _undirected(
        nodes.get_indexer(edges["u"]),
        nodes.get_indexer(edges["v"]),
        edges["length_m"].to_numpy(dtype=float),
    )
    out = []
    for park_id, acc in access.groupby("park_id", sort=True):
        k = len(acc)
        a = np.arange(n, n + k)
        # Access links get the same reduction (a loop edge has u == v: one cell, not two).
        # They never collide with base cells: access nodes are numbered from n.
        ar, ac, aw = _undirected(
            np.r_[a, a],
            np.r_[nodes.get_indexer(acc["u"]), nodes.get_indexer(acc["v"])],
            np.r_[acc["offset_u_m"], acc["offset_v_m"]].astype(float),
        )
        r, c, w = np.r_[base_r, ar], np.r_[base_c, ac], np.r_[base_w, aw]
        matrix = csr_matrix((w, (r, c)), shape=(n + k, n + k))
        dist = dijkstra(matrix, directed=False, indices=a, min_only=True, limit=limit_m)
        node_dist = pd.Series(dist[:n], index=nodes)
        node_park = pd.Series(np.where(np.isfinite(dist[:n]), park_id, None), index=nodes)
        d = block_distances(origins, acc, node_dist, node_park)
        hit = d["network_m"].to_numpy() <= limit_m
        out.append(
            pd.DataFrame(
                {
                    "geoid20": d["geoid20"].to_numpy()[hit],
                    "park_id": park_id,
                    "network_m": d["network_m"].to_numpy()[hit],
                }
            )
        )
    cols = ["geoid20", "park_id", "network_m"]
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True)[cols].sort_values(
        ["geoid20", "park_id"], ignore_index=True
    )
