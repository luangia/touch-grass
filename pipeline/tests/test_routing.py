"""Shortest paths to the nearest park: hand-computed graphs, SciPy traps, and an oracle."""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from touchgrass.core.routing import (
    SAME_EDGE,
    VIA_NODE,
    block_distances,
    build_routing_graph,
    nearest_access,
    parks_within,
)

#   A ──200── B ──100── C ──50── D        E ──10── F   (separate piece)
#   access "p1" on C-D: contact [0, 20] from C → offsets C: 0, D: 30
EDGES = pd.DataFrame(
    {
        "edge_id": ["1-2-0", "2-3-0", "3-4-0", "5-6-0"],
        "u": [1, 2, 3, 5],
        "v": [2, 3, 4, 6],
        "length_m": [200.0, 100.0, 50.0, 10.0],
    }
)
ACCESS = pd.DataFrame(
    {
        "access_id": ["p1|3-4-0#0"],
        "park_id": ["p1"],
        "edge_id": ["3-4-0"],
        "u": [3],
        "v": [4],
        "offset_u_m": [0.0],  # an explicit zero: must stay an edge in the sparse matrix
        "offset_v_m": [30.0],
        "contact_start_m": [0.0],
        "contact_end_m": [20.0],
        "gap_m": [0.0],
    }
)


def origin(
    edge_id: str, u: int, v: int, length: float, pos: float, connector: float
) -> dict[str, object]:
    return {
        "geoid20": f"9900100010010{len(edge_id):02d}",
        "edge_id": edge_id,
        "u": u,
        "v": v,
        "connector_m": connector,
        "offset_u_m": pos,
        "offset_v_m": length - pos,
    }


def run(
    origins: list[dict[str, object]], edges: pd.DataFrame = EDGES, access: pd.DataFrame = ACCESS
) -> pd.DataFrame:
    g = build_routing_graph(edges, access)
    dist, src = nearest_access(g)
    return block_distances(pd.DataFrame(origins), access, dist, src)


def test_node_distances_hand_computed() -> None:
    dist, src = nearest_access(build_routing_graph(EDGES, ACCESS))
    assert dist.to_dict() == {1: 300.0, 2: 100.0, 3: 0.0, 4: 30.0, 5: np.inf, 6: np.inf}
    assert src[1] == "p1|3-4-0#0" and src[5] is None


def test_block_via_nearer_end() -> None:
    # On A-B, 10 m from A, 5 m connector: via A = 10 + 300, via B = 190 + 100 → 290 + 5.
    out = run([origin("1-2-0", 1, 2, 200, 10, 5)]).iloc[0]
    assert out["network_m"] == pytest.approx(295)
    assert out["route_kind"] == VIA_NODE and out["nearest_access_id"] == "p1|3-4-0#0"


def test_same_edge_walks_straight_to_the_contact() -> None:
    # On C-D at 40 m from C: contact is [0, 20] → 20 m along the edge (via C would be 40).
    out = run([origin("3-4-0", 3, 4, 50, 40, 2)]).iloc[0]
    assert out["network_m"] == pytest.approx(22)
    assert out["route_kind"] == SAME_EDGE
    # Inside the contact stretch: only the connector.
    inside = run([origin("3-4-0", 3, 4, 50, 10, 2)]).iloc[0]
    assert inside["network_m"] == pytest.approx(2)


def test_unreachable_piece() -> None:
    out = run([origin("5-6-0", 5, 6, 10, 5, 1)]).iloc[0]
    assert np.isinf(out["network_m"]) and out["route_kind"] is None
    assert out["nearest_access_id"] is None


def test_parallel_edges_use_the_shorter_not_the_sum() -> None:
    # SciPy sums duplicate entries: 100 + 30 would become a 130 m edge B-C.
    edges = pd.concat(
        [EDGES, pd.DataFrame({"edge_id": ["2-3-1"], "u": [2], "v": [3], "length_m": [30.0]})]
    )
    dist, _ = nearest_access(build_routing_graph(edges, ACCESS))
    assert dist[2] == 30.0


def test_nearest_of_two_parks() -> None:
    other = pd.DataFrame(
        {
            "access_id": ["p2|1-2-0#0"],
            "park_id": ["p2"],
            "edge_id": ["1-2-0"],
            "u": [1],
            "v": [2],
            "offset_u_m": [5.0],
            "offset_v_m": [195.0],
            "contact_start_m": [5.0],
            "contact_end_m": [5.0],
            "gap_m": [0.0],
        }
    )
    dist, src = nearest_access(build_routing_graph(EDGES, pd.concat([ACCESS, other])))
    assert (dist[1], src[1]) == (5.0, "p2|1-2-0#0")
    assert (dist[2], src[2]) == (100.0, "p1|3-4-0#0")  # 100 to p1 beats 195 to p2


def test_negative_weights_are_rejected() -> None:
    bad = ACCESS.assign(offset_v_m=-1.0)
    with pytest.raises(ValueError, match="negative weight"):
        build_routing_graph(EDGES, bad)


def test_scipy_matches_networkx_on_a_random_graph() -> None:
    rng = np.random.default_rng(42)
    g = nx.gnm_random_graph(300, 900, seed=7)
    edges = pd.DataFrame(
        [(min(u, v), max(u, v), float(rng.uniform(1, 100))) for u, v in g.edges()],
        columns=["u", "v", "length_m"],
    )
    edges["edge_id"] = [f"{u}-{v}-0" for u, v in zip(edges["u"], edges["v"], strict=True)]
    picks = edges.sample(8, random_state=1)
    access = pd.DataFrame(
        {
            "access_id": [f"a{i}" for i in range(8)],
            "park_id": [f"p{i % 3}" for i in range(8)],
            "edge_id": picks["edge_id"].to_numpy(),
            "u": picks["u"].to_numpy(),
            "v": picks["v"].to_numpy(),
            "offset_u_m": rng.uniform(0, 5, 8),
            "offset_v_m": rng.uniform(0, 5, 8),
            "contact_start_m": 0.0,
            "contact_end_m": 0.0,
            "gap_m": 0.0,
        }
    )
    ours, _ = nearest_access(build_routing_graph(edges, access))
    h = nx.Graph()
    h.add_weighted_edges_from(zip(edges["u"], edges["v"], edges["length_m"], strict=True))
    for u, v, ou, ov in zip(
        access["u"], access["v"], access["offset_u_m"], access["offset_v_m"], strict=True
    ):
        for node, w in ((u, ou), (v, ov)):
            if not h.has_edge("S", node) or h["S"][node]["weight"] > w:
                h.add_edge("S", node, weight=w)
    theirs = nx.single_source_dijkstra_path_length(h, "S")
    for node, d in ours.items():
        assert d == pytest.approx(theirs.get(node, np.inf))


# A second park whose contact stretch covers most of B-C: [10, 90] of 100 m.
P2 = pd.DataFrame(
    {
        "access_id": ["p2|2-3-0#0"],
        "park_id": ["p2"],
        "edge_id": ["2-3-0"],
        "u": [2],
        "v": [3],
        "offset_u_m": [10.0],
        "offset_v_m": [10.0],
        "contact_start_m": [10.0],
        "contact_end_m": [90.0],
        "gap_m": [0.0],
    }
)


def test_parks_within_per_park_distances() -> None:
    at_a = pd.DataFrame([origin("1-2-0", 1, 2, 200, 0, 0)])
    out = parks_within(EDGES, pd.concat([ACCESS, P2]), at_a, 1000).set_index("park_id")
    assert out.loc["p2", "network_m"] == pytest.approx(210)  # A → B, then 10 m into p2
    # p1 is 300 m away (A-B-C). Another park's access node joins B and C for 20 m, but
    # walking through it is not walking B-C: the per-park graph must not contain it.
    assert out.loc["p1", "network_m"] == pytest.approx(300)


def test_parks_within_respects_the_limit_and_matches_nearest() -> None:
    origins = pd.DataFrame(
        [origin("1-2-0", 1, 2, 200, 10, 5), origin("3-4-0", 3, 4, 50, 40, 2)]
    ).assign(geoid20=["g1", "g2"])
    access = pd.concat([ACCESS, P2])
    within = parks_within(EDGES, access, origins, 250)
    assert within[["geoid20", "park_id"]].values.tolist() == [
        ["g1", "p2"],
        ["g2", "p1"],
        ["g2", "p2"],
    ]
    nearest = run(origins.to_dict("records"), access=access).set_index("geoid20")["network_m"]
    best = within.groupby("geoid20")["network_m"].min()
    assert best.to_dict() == pytest.approx(nearest.to_dict())
    assert parks_within(EDGES, access.iloc[0:0], origins, 250).empty
