"""No region constants in API code; latency on the real loaded database (``-m data``)."""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from touchgrass_api.main import create_app

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "api" / "src" / "touchgrass_api"


def test_api_source_names_no_region() -> None:
    from touchgrass.config import list_regions, load_region

    tokens: set[str] = set()
    for region_id in list_regions(REPO / "config"):
        cfg = load_region(region_id, REPO / "config")
        tokens |= {cfg.region_id, str(cfg.analysis_crs), cfg.name.split(",")[0].strip().lower()}
    offenders = [
        f"{p.relative_to(SRC)}: {t!r}"
        for p in SRC.rglob("*.py")
        for t in tokens
        if t in p.read_text("utf-8").lower()
    ]
    assert not offenders, offenders


@pytest.fixture
def real_client() -> Iterator[TestClient]:
    try:
        with TestClient(create_app()) as c:
            if not c.get("/api/v1/regions").json():
                pytest.skip("no regions loaded in the dev database")
            yield c
    except Exception as err:  # unreachable DB, wrong schema: nothing to measure
        pytest.skip(f"dev database not usable: {err}")


@pytest.mark.data
def test_area_list_p95_under_500_ms(real_client: TestClient) -> None:
    for region in real_client.get("/api/v1/regions").json():
        rid = region["region_id"]
        types = real_client.get(f"/api/v1/regions/{rid}").json()["area_types"]
        for t in types:
            path = f"/api/v1/regions/{rid}/areas?type={t['area_type']}"
            times = []
            for _ in range(20):
                start = time.perf_counter()
                assert real_client.get(path).status_code == 200
                times.append((time.perf_counter() - start) * 1000)
            p95 = statistics.quantiles(times, n=20)[-1]
            assert p95 < 500, f"{path}: p95 {p95:.0f} ms"
