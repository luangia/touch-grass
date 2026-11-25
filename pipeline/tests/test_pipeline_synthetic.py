"""End-to-end ``process`` + ``validate`` on a tiny synthetic region (offline).

Layout (degrees, around lon -123.0 / lat 45.0, inside UTM 10N), each cell 0.01°:

        x: -123.02   -123.01   -123.00   -122.99
    y 45.01 ┌─────────┬─────────┬─────────┐
            │ B1 (10) │ B2 (20) │ B3 (40) │      B4 (5) lies outside the boundary
    y 45.00 └─────────┴─────────┴─────────┘
            boundary = B1 + B2 + B3
            neighborhoods: west = B1+B2 cells, east = B2+B3 cells  (overlap on B2)
            districts:     d1 = B1, d2 = B2+B3   (official pops 10 and 60)

Hand-computed answers: city pop 70; B2 in two neighborhoods; nobody in zero;
neighborhood sum 90 (B2 double counted) ≠ city 70.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box, mapping

from touchgrass.config import RegionConfig
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.pipeline.process import MissingDataError, load_outputs, run_process
from touchgrass.pipeline.validate import failed, run_validation
from touchgrass.sources.http import Manifest, manifest_path, sha256_file, write_json_with_manifest

URL = "https://gis.test/arcgis/rest/services/X/MapServer"
D = 0.01


def cell(i: int) -> Any:
    x0 = -123.02 + i * D
    return box(x0, 45.00, x0 + D, 45.01)


BLOCKS = [  # geoid, cell index, pop, housing
    ("990010001001001", 0, 10, 4),
    ("990010001001002", 1, 20, 8),
    ("990010001002001", 2, 40, 15),
    ("990010001002002", 4, 5, 2),  # outside the boundary
]


def config() -> RegionConfig:
    def area(t: str, key: str, check: str | None = None, overlaps: bool = False) -> dict[str, Any]:
        out = {
            "area_type": t,
            "adapter": "arcgis_feature_layer",
            "url": f"{URL}/{1 if t == 'neighborhood' else 2}",
            "key_field": key,
            "name_field": "NAME",
            "allow_overlaps": overlaps,
            "attribution": "test",
            "license": {"name": "Test licence"},
        }
        if check:
            out["population_check_field"] = check
        return out

    return RegionConfig.model_validate(
        {
            "region_id": "testville-zz",
            "name": "Testville",
            "country": "US",
            "analysis_crs": 26910,
            "center_lonlat": [-123.0, 45.0],
            "state_fips": "99",
            "county_fips": ["001"],
            "buffer_m": 500,
            "study_boundary": {
                "adapter": "arcgis_feature_layer",
                "url": f"{URL}/0",
                "attribution": "test",
                "license": {"name": "Test licence"},
            },
            "analysis_areas": [
                area("neighborhood", "ID", overlaps=True),
                area("district", "DIST", check="POP"),
            ],
            "census": {"decennial_vintage": 2020, "acs": {"year": 2024, "tables": ["B01003"]}},
            "validation": {"expected_population": 70},
        }
    )


def _fc(rows: list[tuple[dict[str, Any], Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": p, "geometry": mapping(g)} for p, g in rows],
    }


def _store_json(raw: src.RawSource, payload: dict[str, Any]) -> None:
    write_json_with_manifest(
        payload, raw.path, {"source_id": raw.source_id, "url": raw.url, "attribution": "test"}
    )


def _store_file(raw: src.RawSource) -> None:
    Manifest(
        source_id=raw.source_id,
        url=raw.url,
        retrieved_at="2026-01-01T00:00:00+00:00",
        stored_file=raw.path.name,
        stored_bytes=raw.path.stat().st_size,
        stored_sha256=sha256_file(raw.path),
        attribution="test",
    ).write(manifest_path(raw.path))


def _zip_shapefile(gdf: gpd.GeoDataFrame, dest: Path) -> None:
    shp_dir = dest.parent / (dest.stem + "_shp")
    shp_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_file(shp_dir / f"{dest.stem}.shp")
    with zipfile.ZipFile(dest, "w") as z:
        for f in shp_dir.iterdir():
            z.write(f, f.name)


def build_raw(cfg: RegionConfig, paths: RegionPaths) -> None:
    boundary = src.boundary_source(cfg, paths)
    _store_json(boundary, _fc([({"NAME": "Testville"}, box(-123.02, 45.00, -122.99, 45.01))]))

    hoods, districts = src.area_sources(cfg, paths)
    west, east = box(-123.02, 45.0, -123.00, 45.01), box(-123.01, 45.0, -122.99, 45.01)
    _store_json(hoods, _fc([({"ID": 1, "NAME": "West"}, west), ({"ID": 2, "NAME": "East"}, east)]))
    _store_json(
        districts,
        _fc(
            [
                ({"DIST": "1", "NAME": "1", "POP": 10}, cell(0)),
                ({"DIST": "2", "NAME": "2", "POP": 60}, box(-123.01, 45.0, -122.99, 45.01)),
            ]
        ),
    )

    tb = src.tiger_source(cfg, paths, "tabblock20")
    blocks = gpd.GeoDataFrame(
        {
            "GEOID20": [b[0] for b in BLOCKS],
            "COUNTYFP20": "001",
            "POP20": [b[2] for b in BLOCKS],
            "HOUSING20": [b[3] for b in BLOCKS],
            "ALAND20": 800_000,
            "AWATER20": 0,
            "INTPTLON20": [f"{cell(b[1]).centroid.x:+.7f}" for b in BLOCKS],
            "INTPTLAT20": [f"{cell(b[1]).centroid.y:+.7f}" for b in BLOCKS],
        },
        geometry=[cell(b[1]) for b in BLOCKS],
        crs=4269,
    )
    _zip_shapefile(blocks, tb.path)
    _store_file(tb)

    bg = src.tiger_source(cfg, paths, "bg")
    bgs = gpd.GeoDataFrame(
        {"GEOID": ["990010001001", "990010001002"], "COUNTYFP": "001"},
        geometry=[box(-123.02, 45.0, -123.00, 45.01), box(-123.00, 45.0, -122.97, 45.01)],
        crs=4269,
    )
    _zip_shapefile(bgs, bg.path)
    _store_file(bg)

    (acs_src,) = src.acs_sources(cfg, paths)
    acs_src.path.parent.mkdir(parents=True, exist_ok=True)
    acs_src.path.write_text(
        "GEO_ID|B01003_E001|B01003_M001\n"
        "1500000US990010001001|31|9\n"
        "1500000US990010001002|-666666666|-222222222\n"
    )
    _store_file(acs_src)


@pytest.fixture
def region(tmp_path: Path) -> tuple[RegionConfig, RegionPaths]:
    cfg = config()
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_raw(cfg, paths)
    return cfg, paths


def test_hand_computed_answers(region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = region
    result = run_process(cfg, paths)
    f = result.frames

    assert f["blocks"]["geoid20"].tolist() == [b[0] for b in BLOCKS[:3]]  # B4 excluded
    assert int(f["blocks"]["pop20"].sum()) == 70

    m = f["area_membership"]
    hood = m[m["area_type"] == "neighborhood"]
    assert hood.groupby("geoid20").size().to_dict() == {
        "990010001001001": 1,
        "990010001001002": 2,  # the overlap block
        "990010001002001": 1,
    }
    pops = f["blocks"].set_index("geoid20")["pop20"]
    assert int(hood["geoid20"].map(pops).sum()) == 90  # ≠ 70: why city totals use blocks

    bgs = f["block_groups"].set_index("geoid")
    assert bgs.loc["990010001001", "b01003_e001"] == 31
    assert pd.isna(bgs.loc["990010001002", "b01003_e001"])  # sentinel → null

    buffer = f["study_boundary"].set_index("kind").loc["analysis_buffer", "geometry"]
    assert buffer.contains(f["study_boundary"].set_index("kind").loc["boundary", "geometry"])

    checks = run_validation(cfg, f, result.stats)
    assert failed(checks) == [], [(c.name, c.expected, c.actual) for c in failed(checks)]


def test_reconciliation_failure_is_reported(region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = region
    _hoods, districts = src.area_sources(cfg, paths)
    payload = json.loads(districts.path.read_text())
    payload["features"][1]["properties"]["POP"] = 61  # official says 61, blocks say 60
    _store_json(districts, payload)
    result = run_process(cfg, paths)
    bad = {c.name: c for c in failed(run_validation(cfg, result.frames, result.stats))}
    assert "district.population_reconciles" in bad
    assert bad["district.population_reconciles"].details["differences"] == {"district:2": -1}


def test_idempotent(region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = region
    run_process(cfg, paths)
    first = load_outputs(paths.processed)
    run_process(cfg, paths)
    second = load_outputs(paths.processed)
    for name in first:
        pd.testing.assert_frame_equal(
            pd.DataFrame(first[name]).astype(
                {"geometry": str} if "geometry" in first[name] else {}
            ),
            pd.DataFrame(second[name]).astype(
                {"geometry": str} if "geometry" in second[name] else {}
            ),
        )


def test_tampered_raw_file_is_refused(region: tuple[RegionConfig, RegionPaths]) -> None:
    cfg, paths = region
    acs_path = src.acs_sources(cfg, paths)[0].path
    acs_path.write_text(acs_path.read_text().replace("|31|", "|32|"))
    with pytest.raises(MissingDataError, match="changed since download"):
        run_process(cfg, paths)
