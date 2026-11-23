"""Walk filter, graph building, snapping, park access points, and the network pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from touchgrass.config import RegionConfig
from touchgrass.config.models import WalkFilter
from touchgrass.core.access import EDGE_CONTACT, ENTRANCE, GAP, NO_CONTACT, park_access_points
from touchgrass.core.network import build_walk_graph
from touchgrass.core.snap import snap_to_edges, snap_to_edges_dry
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.pipeline.network import (
    ISOLATED,
    MOORAGE,
    NETWORK_FILES,
    OK,
    build_origins,
    network_freshness_problems,
    run_network,
)
from touchgrass.pipeline.process import run_process
from touchgrass.pipeline.validate import failed
from touchgrass.sources.osm import extract_walk_ways, walk_exclusion

from .network_fixture import add_walk_network
from .osm_fixture import OsmFixture
from .test_pipeline_parks import build_park_raw, parks_config
from .test_pipeline_synthetic import _store_file

UTM10 = 26910
SHARED_USE = [{"highway": ["cycleway"], "foot": ["designated", "yes", "permissive"]}]
ENTRANCES = {"entrance": ["*"], "barrier": ["gate"]}


# ------------------------------------------------------------------------ walk filter


@pytest.mark.parametrize(
    ("tags", "readmit", "reason"),
    [
        ({"highway": "footway"}, False, None),
        ({"highway": "residential"}, False, None),  # no sidewalk tag: still walkable
        ({"highway": "cycleway"}, False, "highway=cycleway"),
        ({"highway": "cycleway", "foot": "designated"}, False, "highway=cycleway"),
        ({"highway": "cycleway", "foot": "designated"}, True, None),  # shared-use path
        ({"highway": "cycleway"}, True, "highway=cycleway"),  # no foot tag: stays out
        # readmit never overrides an exclude tag
        (
            {"highway": "cycleway", "foot": "designated", "access": "private"},
            True,
            "access=private",
        ),
        ({"highway": "motorway_link"}, True, "highway=motorway_link"),
        ({"highway": "residential", "sidewalk": "separate"}, False, "sidewalk=separate"),
        ({"highway": "service", "service": "private"}, False, "service=private"),
        ({"highway": "pedestrian", "area": "yes"}, False, "area=yes"),
        ({"name": "no highway"}, False, "no_highway"),
    ],
)
def test_walk_exclusion(tags: dict[str, str], readmit: bool, reason: str | None) -> None:
    walk = WalkFilter(readmit=SHARED_USE if readmit else [])
    assert walk_exclusion(tags, walk) == reason


def test_readmit_must_constrain_highway() -> None:
    with pytest.raises(ValueError, match="must constrain 'highway'"):
        WalkFilter(readmit=[{"foot": ["yes"]}])


# ---------------------------------------------------------------------------- graph


def _network_pbf(path: Path) -> Path:
    f = OsmFixture()
    add_walk_network(f)
    return f.write(path)


CLIP = box(-123.03, 44.99, -122.98, 45.02)


def _graph(tmp_path: Path, readmit: bool) -> Any:
    pbf = _network_pbf(tmp_path / "net.osm.pbf")
    xml = tmp_path / "walk.osm"
    walk = WalkFilter(readmit=SHARED_USE if readmit else [])
    extract = extract_walk_ways(pbf, CLIP.bounds, walk, ENTRANCES, xml)
    return extract, build_walk_graph(xml, CLIP, UTM10)


def test_extract_counts_exclusions_and_entrances(tmp_path: Path) -> None:
    extract, _ = _graph(tmp_path, readmit=True)
    # H0-H2 + V1-V4 + island + South St + the shared-use cycleway
    assert extract.ways_kept == 10 and extract.readmitted_ways == 1
    assert {k: v["ways"] for k, v in extract.excluded.items()} == {
        "sidewalk=separate": 1,
        "access=private": 1,
    }
    assert sorted(extract.entrances["kind"]) == ["barrier=gate", "entrance=main"]


def test_shared_use_path_connects_the_south_street(tmp_path: Path) -> None:
    _, with_path = _graph(tmp_path / "a", readmit=True)
    _, without = _graph(tmp_path / "b", readmit=False)
    # With the cycleway: main grid + South St are one piece; only the island is separate.
    assert len(with_path.components) == 2 and with_path.components[1]["nodes"] == 2
    # Without it, South St is stranded too (the Hayden Island effect).
    assert len(without.components) == 3


def test_edges_are_canonical(tmp_path: Path) -> None:
    _, g = _graph(tmp_path, readmit=True)
    e = g.edges
    assert (e["u"] <= e["v"]).all()
    assert e["edge_id"].is_unique
    nodes = g.nodes.set_index("node_id").geometry
    starts = gpd.GeoSeries([Point(geom.coords[0]) for geom in e.geometry], crs=UTM10)
    assert (starts.distance(nodes.loc[e["u"]].reset_index(drop=True)) < 1e-6).all()
    # Weights are analysis-CRS lengths; OSMnx's spherical lengths differ by < 0.3% at 45° N.
    assert (e.geometry.length == e["length_m"]).all()
    assert ((e["length_m"] - e["osmnx_length_m"]).abs() / e["length_m"]).max() < 0.003


def test_graph_is_deterministic(tmp_path: Path) -> None:
    _, a = _graph(tmp_path / "a", readmit=True)
    _, b = _graph(tmp_path / "b", readmit=True)
    pd.testing.assert_frame_equal(a.edges.to_wkb(), b.edges.to_wkb())


# ------------------------------------------------------------------------- snapping


def _edges(lines: list[LineString]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "edge_id": [f"{i}-{i + 1}-0" for i in range(len(lines))],
            "u": range(len(lines)),
            "v": range(1, len(lines) + 1),
            "length_m": [ln.length for ln in lines],
        },
        geometry=lines,
        crs=UTM10,
    )


X0, Y0 = 500_000.0, 5_000_000.0


def test_snap_hand_computed() -> None:
    edges = _edges([LineString([(X0, Y0), (X0 + 100, Y0)])])
    snapped = snap_to_edges(gpd.GeoSeries([Point(X0 + 30, Y0 + 20)], crs=UTM10), edges).iloc[0]
    assert snapped["connector_m"] == pytest.approx(20)
    assert snapped["offset_u_m"] == pytest.approx(30)
    assert snapped["offset_v_m"] == pytest.approx(70)


def test_snap_picks_nearest_edge_not_nearest_node() -> None:
    # Node of edge 1 is closer than any node of edge 0, but edge 0's middle is closer.
    edges = _edges(
        [
            LineString([(X0, Y0), (X0 + 200, Y0)]),
            LineString([(X0 + 100, Y0 + 60), (X0 + 100, Y0 + 300)]),
        ]
    )
    pt = gpd.GeoSeries([Point(X0 + 100, Y0 + 25)], crs=UTM10)
    assert snap_to_edges(pt, edges).iloc[0]["edge_id"] == "0-1-0"


def test_snap_rejects_unprojected_points() -> None:
    edges = _edges([LineString([(X0, Y0), (X0 + 100, Y0)])])
    with pytest.raises(ValueError, match="EPSG:26910"):
        snap_to_edges(gpd.GeoSeries([Point(-123, 45)], crs=4326), edges)


# ------------------------------------------------------------------- access points


def test_access_points() -> None:
    park = box(X0 + 100, Y0 - 50, X0 + 200, Y0 + 50)
    island = box(X0 + 1000, Y0 + 1000, X0 + 1100, Y0 + 1100)
    parks = gpd.GeoDataFrame(
        {"park_id": ["osm:w1", "osm:w2"], "name": ["Crossed Park", "Island Park"]},
        geometry=[park, island],
        crs=UTM10,
    )
    # One street runs straight through the park: contact from x=85 to x=215 (15 m tolerance).
    edges = _edges([LineString([(X0, Y0), (X0 + 300, Y0)])])
    gates = gpd.GeoDataFrame(
        {
            "osm_node_id": [7, 8],
            "kind": ["entrance=main", "barrier=gate"],
            "access": [None, "private"],
        },
        geometry=[Point(X0 + 100, Y0), Point(X0 + 200, Y0)],
        crs=UTM10,
    )
    res = park_access_points(parks, edges, gates, tolerance=15, exclude_access=["private"])
    contact = res.points[res.points["method"] == EDGE_CONTACT].iloc[0]
    assert contact["contact_start_m"] == pytest.approx(85)
    assert contact["contact_end_m"] == pytest.approx(215)
    assert contact["offset_u_m"] == pytest.approx(85) and contact["offset_v_m"] == pytest.approx(85)
    entrance = res.points[res.points["method"] == ENTRANCE].iloc[0]
    assert entrance["osm_node_id"] == 7 and entrance["offset_u_m"] == pytest.approx(100)
    assert res.entrances_excluded_access == 1  # the private gate
    status = res.per_park.set_index("name")["status"]
    assert status["Crossed Park"] == "reachable" and status["Island Park"] == NO_CONTACT


# ------------------------------------------------------------------- end to end


def network_config(**overrides: Any) -> RegionConfig:
    data = parks_config(["West Woods"]).model_dump(mode="json")
    data["network"] = {
        "walk_filter": {"readmit": SHARED_USE},
        "min_main_component_share": 0.8,  # the fixture's island is 2 of ~20 nodes
        **overrides,
    }
    return RegionConfig.model_validate(data)


def build_network_region(tmp_path: Path, **overrides: Any) -> tuple[RegionConfig, RegionPaths]:
    cfg = network_config(**overrides)
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    # Rewrite the OSM extract: Stage 3's park areas plus the walk network.
    f = OsmFixture()
    f.area_way(10, (-123.02, 45.0, -123.00, 45.01), leisure="nature_reserve", name="West Woods")
    f.area_way(11, (-123.015, 45.002, -123.012, 45.004), leisure="park", name="Corner Park")
    add_walk_network(f)
    raw = src.osm_source(cfg, paths)
    assert raw is not None
    f.write(raw.path)
    _store_file(raw)
    run_process(cfg, paths)
    return cfg, paths


@pytest.fixture
def network_region(tmp_path: Path) -> tuple[RegionConfig, RegionPaths]:
    return build_network_region(tmp_path)


def test_network_end_to_end(network_region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = network_region
    result = run_network(cfg, paths)
    assert failed(result.checks) == []

    origins = result.frames["block_origins"]
    assert len(origins) == 3  # the three populated blocks inside the boundary
    # Each block point is 0.0005° of latitude (≈ 55.6 m) north of street H1.
    assert origins["connector_m"].tolist() == pytest.approx([55.6] * 3, abs=0.5)
    assert (origins["nearest_component"] == 0).all()

    access = result.frames["park_access_points"]
    assert set(access["method"]) == {EDGE_CONTACT, ENTRANCE}
    assert access.loc[access["method"] == ENTRANCE, "osm_node_id"].tolist() == [
        _node_id(paths, "entrance")
    ]
    report = result.report
    assert report["access"]["entrances_excluded_access"] == 1
    assert report["graph"]["components"] == 2
    for name in NETWORK_FILES.values():
        assert (paths.processed / name).is_file()
    review = gpd.read_file(paths.processed / "network_review.geojson")
    assert "stranded_component" in set(review["layer"])
    json.loads((paths.processed / "network_report.json").read_text())  # valid JSON (no NaN)


def _node_id(paths: RegionPaths, kind: str) -> int:
    # The fixture creates the entrance node while building V3; read it back from the PBF.
    import osmium

    raw = next(p for p in paths.raw.rglob("*.osm.pbf"))
    for obj in osmium.FileProcessor(str(raw)).with_filter(osmium.filter.KeyFilter(kind)):
        return int(obj.id)
    raise AssertionError(kind)


def test_network_rerun_is_identical(network_region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = network_region
    run_network(cfg, paths)
    first = {n: (paths.processed / f).read_bytes() for n, f in NETWORK_FILES.items()}
    run_network(cfg, paths)
    assert {n: (paths.processed / f).read_bytes() for n, f in NETWORK_FILES.items()} == first


def test_stale_network_is_detected(network_region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = network_region
    run_network(cfg, paths)
    assert network_freshness_problems(paths.processed) == []
    run_process(cfg, paths)  # same inputs → same parks file → still fresh
    assert network_freshness_problems(paths.processed) == []
    parks = gpd.read_parquet(paths.processed / "parks.parquet")
    parks.loc[0, "name"] = "Renamed Woods"
    parks.to_parquet(paths.processed / "parks.parquet", index=False)
    assert network_freshness_problems(paths.processed) == [
        "parks.parquet changed since `touchgrass network` ran"
    ]


def test_failed_population_check(tmp_path: Path) -> None:
    cfg, paths = build_network_region(tmp_path, min_population_within_100m=0.95)
    data = cfg.model_dump(mode="json")
    data["analysis"]["max_snap_m"] = 10  # everyone is "flagged" but the 100 m check is separate
    cfg = RegionConfig.model_validate(data)
    result = run_network(cfg, paths)
    assert result.frames["block_origins"]["flagged"].all()
    assert failed(result.checks) == []  # 55.6 m < 100 m: still passes


def test_network_requires_parks() -> None:
    data = parks_config([]).model_dump(mode="json")
    data["parks"] = None
    data["network"] = {}
    with pytest.raises(ValueError, match="'network' requires 'parks'"):
        RegionConfig.model_validate(data)


# ------------------------------------------------------------- water-aware rules (D-020)


RIVER = box(X0 - 1000, Y0 + 5, X0 + 1000, Y0 + 25)  # a 20 m wide strip north of the origin


def test_dry_snap_skips_an_edge_across_the_river() -> None:
    across = LineString([(X0 - 100, Y0 + 30), (X0 + 100, Y0 + 30)])  # 30 m, over the river
    behind = LineString([(X0 - 100, Y0 - 80), (X0 + 100, Y0 - 80)])  # 80 m, dry
    pt = gpd.GeoSeries([Point(X0, Y0)], crs=UTM10)
    both = snap_to_edges_dry(pt, _edges([across, behind]), RIVER, max_m=500).iloc[0]
    assert (both["edge_id"], both["dry"]) == ("1-2-0", True)
    assert both["connector_m"] == pytest.approx(80)
    only_wet = snap_to_edges_dry(pt, _edges([across]), RIVER, max_m=500).iloc[0]
    assert not only_wet["dry"]  # caller decides what that means


def test_park_gap_connects_set_back_park_but_not_island() -> None:
    street = LineString([(X0, Y0), (X0 + 300, Y0)])
    # A triangle pointing at the street, so the nearest point is unique: its apex, 40 m away.
    set_back = Polygon([(X0 + 150, Y0 - 40), (X0 + 100, Y0 - 140), (X0 + 200, Y0 - 140)])
    island = box(X0 + 100, Y0 + 30, X0 + 200, Y0 + 130)  # 30 m away, but across the river
    parks = gpd.GeoDataFrame(
        {"park_id": ["osm:w1", "osm:w2"], "name": ["Ravine", "Island"]},
        geometry=[set_back, island],
        crs=UTM10,
    )
    river = box(X0 - 1000, Y0 + 5, X0 + 1000, Y0 + 28)
    empty = gpd.GeoDataFrame({"osm_node_id": [], "kind": [], "access": []}, geometry=[], crs=UTM10)
    res = park_access_points(
        parks, _edges([street]), empty, tolerance=15, exclude_access=[], water=river, max_gap=100
    )
    gap = res.points.iloc[0]
    assert (gap["park_id"], gap["method"]) == ("osm:w1", GAP)
    assert gap["gap_m"] == pytest.approx(40)
    # Offsets include the gap: walking cost from each end to the park.
    assert gap["offset_u_m"] == pytest.approx(150 + 40)
    assert gap["offset_v_m"] == pytest.approx(150 + 40)
    status = res.per_park.set_index("name")["status"]
    assert status["Ravine"] == "reachable_via_gap" and status["Island"] == NO_CONTACT


def test_origin_statuses() -> None:
    main = LineString([(X0 - 300, Y0 - 50), (X0 + 300, Y0 - 50)])  # south bank street
    island_street = LineString([(X0 - 50, Y0 + 200), (X0 + 50, Y0 + 200)])
    edges = _edges([main, island_street])
    edges["component"] = [0, 1]
    river = box(X0 - 1000, Y0, X0 + 1000, Y0 + 150).union(
        box(X0 - 1000, Y0 + 250, X0 + 1000, Y0 + 400)
    )  # water on both sides of the island (Y0+150 .. Y0+250 is land)
    pts_utm = [Point(X0, Y0 - 20), Point(X0 + 10, Y0 + 60), Point(X0, Y0 + 210)]
    lonlat = gpd.GeoSeries(pts_utm, crs=UTM10).to_crs(4326)
    blocks = pd.DataFrame(
        {
            "geoid20": ["990010001001001", "990010001001002", "990010001001003"],
            "pop20": [10, 5, 19],
            "rep_lon": lonlat.x,
            "rep_lat": lonlat.y,
        }
    )
    o = build_origins(blocks, edges, edges[edges["component"] == 0], UTM10, 500, river)
    o = o.set_index("geoid20")
    assert o["snap_status"].tolist() == [OK, MOORAGE, ISOLATED]
    assert o["component"].tolist() == [0, 0, 1]
    assert o.loc["990010001001003", "connector_m"] == pytest.approx(10, abs=0.01)


def test_extract_areas_intersects_mode(tmp_path: Path) -> None:
    from touchgrass.sources.osm import extract_areas

    f = OsmFixture()
    # A long "river": its representative point lies far east of the small study area.
    f.area_way(1, (-123.05, 45.0, -122.0, 45.002), natural="water")
    pbf = f.write(tmp_path / "river.osm.pbf")
    study = box(-123.04, 44.99, -123.03, 45.01)
    by_point, _ = extract_areas(pbf, {"natural": {"water"}}, study)
    by_overlap, _ = extract_areas(pbf, {"natural": {"water"}}, study, keep="intersects")
    assert len(by_point) == 0 and len(by_overlap) == 1


def test_clip_touching_pieces_are_marked(tmp_path: Path) -> None:
    _, g = _graph(tmp_path, readmit=True)
    # CLIP is far from every fixture street, so nothing touches it here...
    assert not any(c["touches_clip"] for c in g.components)
    # ...but a clip line drawn through the grid touches the main piece.
    pbf = tmp_path / "net.osm.pbf"
    xml = tmp_path / "walk2.osm"
    extract_walk_ways(pbf, CLIP.bounds, WalkFilter(readmit=SHARED_USE), ENTRANCES, xml)
    tight = box(-123.025, 44.99, -123.005, 45.02)
    g2 = build_walk_graph(xml, tight, UTM10)
    assert g2.components[0]["touches_clip"]
