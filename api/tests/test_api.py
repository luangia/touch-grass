"""Endpoint contracts, errors, caching and safety against the seeded test database."""

from __future__ import annotations

import math
from typing import Any

import mapbox_vector_tile
import psycopg
import pytest
from conftest import REGION, Seeded, url
from fastapi.testclient import TestClient

from touchgrass_api import db, deps, main
from touchgrass_api.models import AreaMetricsResponse, Comparison, Methodology, Ranking
from touchgrass_api.settings import Settings


def tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


# ------------------------------------------------------------------- health / startup


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "database": "reachable",
        "schema_version": db.EXPECTED_SCHEMA_VERSION,
        "expected_schema_version": db.EXPECTED_SCHEMA_VERSION,
    }


def test_startup_refuses_another_schema_version(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "EXPECTED_SCHEMA_VERSION", 99)
    with (
        pytest.raises(db.SchemaMismatchError, match="expects 99"),
        TestClient(main.create_app(Settings(conninfo=seeded.conninfo))),
    ):
        pass


def test_health_reports_a_schema_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "EXPECTED_SCHEMA_VERSION", 99)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


def test_sessions_are_read_only(client: TestClient) -> None:
    pool = client.app.state.pool  # type: ignore[attr-defined]
    with (
        pool.connection() as conn,
        pytest.raises(psycopg.errors.ReadOnlySqlTransaction),
    ):
        conn.execute("DELETE FROM touchgrass.regions")


# -------------------------------------------------------------------------- regions


def test_regions_list_and_detail(client: TestClient, seeded: Seeded) -> None:
    (region,) = client.get("/api/v1/regions").json()
    assert region["region_id"] == REGION and region["latest_run_id"] == seeded.run_id
    b = region["bbox"]
    assert -180 <= b["west"] < b["east"] <= 180 and -90 <= b["south"] < b["north"] <= 90
    detail = client.get(url("")).json()
    assert [t["minutes"] for t in detail["thresholds"]] == [5, 10, 15]
    assert detail["thresholds"][1]["meters"] == pytest.approx(800)
    assert {a["area_type"]: a["areas"] for a in detail["area_types"]} == {
        "district": 2,
        "neighborhood": 2,
    }
    assert detail["metrics_available"] is True and detail["underserved_minutes"] == 10


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/v1/regions/nowhere-xx", 404),
        ("/api/v1/regions/Bad_Id!", 422),
        ("/api/v1/regions/nowhere-xx/areas?type=neighborhood", 404),
        (url("/areas?type=ward"), 404),
        (url("/areas"), 422),  # type is required
        (url("/areas?type=neighborhood&threshold=7"), 422),
        (url("/areas/nowhere/metrics"), 404),
        (url("/areas/x';DROP TABLE regions;--/metrics"), 404),  # a value, never SQL
        (url("/compare?area_ids=city"), 422),
        (url("/compare?area_ids=" + ",".join(f"a{i}" for i in range(11))), 422),
        (url("/rankings?type=neighborhood&metric=1;DROP"), 422),
        (url("/rankings?type=neighborhood&metric=underserved_pop&threshold=5"), 422),
        (url("/rankings?type=neighborhood&limit=0"), 422),
        (url("/tiles/blocks/11/0/0.pbf"), 422),
        (url("/tiles/blocks/15/0/0.pbf"), 422),
        (url("/tiles/blocks/12/4096/0.pbf"), 422),
    ],
)
def test_errors(client: TestClient, path: str, status: int) -> None:
    assert client.get(path).status_code == status


# ---------------------------------------------------------------------------- areas


def _check_feature_collection(fc: dict[str, Any], n: int) -> None:
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == n
    for f in fc["features"]:
        assert f["type"] == "Feature" and isinstance(f["id"], str)
        assert f["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        ring = f["geometry"]["coordinates"][0]
        ring = ring[0] if f["geometry"]["type"] == "MultiPolygon" else ring
        lon, lat = ring[0]
        assert -124 < lon < -122 and 44 < lat < 46  # RFC 7946: longitude first


def test_areas_geojson(client: TestClient, seeded: Seeded) -> None:
    r = client.get(url("/areas?type=neighborhood"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/geo+json"
    fc = r.json()
    _check_feature_collection(fc, 2)
    assert fc["run_id"] == seeded.run_id and fc["threshold_min"] == 10
    props = {f["id"]: f["properties"] for f in fc["features"]}
    assert set(props) == {"neighborhood:1", "neighborhood:2"}
    assert props["neighborhood:1"]["pop_total"] == 30  # blocks of 10 + 20 people
    five = client.get(url("/areas?type=neighborhood&threshold=5")).json()
    assert five["threshold_min"] == 5
    for f in five["features"]:
        assert f["properties"]["pct_within"] <= props[f["id"]]["pct_within"]


def test_area_metrics_and_city(client: TestClient) -> None:
    body = AreaMetricsResponse.model_validate(client.get(url("/areas/city/metrics")).json())
    city = body.area
    assert city.area_type == "city" and city.pop_total == 70
    assert [t.threshold_min for t in city.thresholds] == [5, 10, 15]
    assert [c.indicator for c in city.context] == ["total_population"]
    hood = client.get(url("/areas/neighborhood:1/metrics")).json()["area"]
    assert hood["context"][0]["estimate"] == pytest.approx(31)  # block group ...001, all of it


def test_compare(client: TestClient) -> None:
    r = client.get(url("/compare?area_ids=neighborhood:2,city,neighborhood:2"))
    body = Comparison.model_validate(r.json())
    assert [a.area_id for a in body.areas] == ["neighborhood:2", "city"]  # order kept, deduped
    assert client.get(url("/compare?area_ids=city,nowhere")).status_code == 404


def test_rankings_apply_the_floor_only_to_rates(client: TestClient) -> None:
    share = Ranking.model_validate(client.get(url("/rankings?type=district")).json())
    assert share.min_residents == 15 and share.order == "asc"
    assert [e.area_id for e in share.entries] == ["district:2"]  # district:1 has 10 residents
    count = Ranking.model_validate(
        client.get(url("/rankings?type=district&metric=underserved_pop")).json()
    )
    assert count.min_residents is None and count.order == "desc"
    assert {e.area_id for e in count.entries} == {"district:1", "district:2"}
    assert [e.rank for e in count.entries] == [1, 2]
    flipped = client.get(url("/rankings?type=district&metric=underserved_pop&order=asc")).json()
    assert flipped["order"] == "asc"


# ---------------------------------------------------------------------------- layers


def test_parks_geojson(client: TestClient, seeded: Seeded) -> None:
    fc = client.get(url("/parks")).json()
    _check_feature_collection(fc, seeded.parks)
    assert {"park_id", "name", "park_class", "area_m2"} <= set(fc["features"][0]["properties"])


def test_block_tile_over_the_city(client: TestClient, seeded: Seeded) -> None:
    x, y = tile_of(-123.005, 45.005, 12)
    r = client.get(url(f"/tiles/blocks/12/{x}/{y}.pbf"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    layers = mapbox_vector_tile.decode(r.content)
    assert set(layers) == {"blocks"}
    props = [f["properties"] for f in layers["blocks"]["features"]]
    assert sorted(p["geoid20"] for p in props) == seeded.populated_blocks
    assert all(p["network_m"] > 0 and p["walk_min"] > 0 for p in props)
    assert all(p["parks_reachable"] >= 1 for p in props)
    # The nearest park is named in the tile (the synthetic one is "West Woods").
    assert {p["nearest_park_name"] for p in props} == {"West Woods"}


def test_tile_outside_the_region_is_empty(client: TestClient) -> None:
    x, y = tile_of(-100.0, 40.0, 12)
    r = client.get(url(f"/tiles/blocks/12/{x}/{y}.pbf"))
    assert r.status_code == 204 and r.content == b""


# ----------------------------------------------------------- methodology, HTTP extras


def test_methodology(client: TestClient, seeded: Seeded) -> None:
    m = Methodology.model_validate(client.get(url("/methodology")).json())
    assert m.run_id == seeded.run_id
    assert {c.id for c in m.caveats} >= {"walk_network", "context_not_cause", "survey_error"}
    assert m.sources and all(s.attribution for s in m.sources)
    assert m.analysis["walk_speed_kmh"] == 4.8


def test_etag_and_not_modified(client: TestClient, seeded: Seeded) -> None:
    r = client.get(url("/parks"))
    etag = r.headers["etag"]
    assert seeded.run_id in etag and "max-age" in r.headers["cache-control"]
    # A new response format must invalidate old copies even for the same run.
    assert etag.endswith(f'-v{deps.RESPONSE_VERSION}"')
    again = client.get(url("/parks"), headers={"if-none-match": etag})
    assert again.status_code == 304 and again.content == b""
    assert client.get(url("/parks"), headers={"if-none-match": '"stale"'}).status_code == 200


def test_gzip_only_above_the_size_floor(client: TestClient) -> None:
    big = client.get("/openapi.json", headers={"accept-encoding": "gzip"})
    assert len(big.content) > 1000 and big.headers.get("content-encoding") == "gzip"
    small = client.get(url("/parks"), headers={"accept-encoding": "gzip"})
    assert len(small.content) < 1000 and "content-encoding" not in small.headers


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    ok = client.get("/api/v1/regions", headers={"origin": "http://localhost:5173"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/v1/regions", headers={"origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/health",
        "/api/v1/regions",
        "/api/v1/regions/{region_id}",
        "/api/v1/regions/{region_id}/methodology",
        "/api/v1/regions/{region_id}/areas",
        "/api/v1/regions/{region_id}/areas/{area_id}/metrics",
        "/api/v1/regions/{region_id}/compare",
        "/api/v1/regions/{region_id}/rankings",
        "/api/v1/regions/{region_id}/parks",
        "/api/v1/regions/{region_id}/tiles/blocks/{z}/{x}/{y}.pbf",
    }
