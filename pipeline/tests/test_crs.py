"""CRS rules: the single most common source of silent GIS bugs."""

from __future__ import annotations

import math

import pytest
from pyproj import Transformer

from touchgrass.core.crs import (
    AnalysisCRSError,
    LonLatBounds,
    assert_analysis_crs,
    assert_point_in_area_of_use,
    load_crs,
    make_transformer,
)

# Downtown Portland. Expected UTM 10N values computed independently with PROJ
# (see docs/learning/stage-01-foundations.md) and sanity-checked by hand:
#   easting  ≈ 500 km (false easting) + 0.3235° x ~78 km/° ≈ 525 km
#   northing ≈ 45.52° x ~110.7 km/°                       ≈ 5,041 km
LON, LAT = -122.6765, 45.5231
EXPECTED_E, EXPECTED_N = 525_263.68, 5_041_113.74


class TestAssertAnalysisCRS:
    @pytest.mark.parametrize(
        "epsg",
        [
            26910,  # NAD83 / UTM 10N: our Portland CRS
            32610,  # WGS 84 / UTM 10N
            5070,  # NAD83 / Conus Albers: projected, meters, not Mercator
        ],
    )
    def test_accepts_projected_metric(self, epsg: int) -> None:
        assert assert_analysis_crs(epsg).is_projected

    @pytest.mark.parametrize(
        ("epsg", "reason"),
        [
            (4326, "geographic"),  # WGS 84 lon/lat in degrees
            (4269, "geographic"),  # NAD83 lon/lat, what TIGER/Line ships in
            (2269, "meters"),  # NAD83 / Oregon North, in feet
            (3857, "Mercator"),  # Web Mercator: meters, but area x2 at 45°N
            (3395, "Mercator"),  # World Mercator
            (999_999, "not a known CRS"),
        ],
    )
    def test_rejects_with_reason(self, epsg: int, reason: str) -> None:
        with pytest.raises(AnalysisCRSError, match=reason):
            assert_analysis_crs(epsg)

    def test_error_is_a_value_error(self) -> None:
        # Pydantic only turns ValueError/AssertionError into validation errors.
        assert issubclass(AnalysisCRSError, ValueError)


class TestTransformer:
    def test_lonlat_to_utm(self) -> None:
        e, n = make_transformer(4326, 26910).transform(LON, LAT)
        assert e == pytest.approx(EXPECTED_E, abs=0.5)
        assert n == pytest.approx(EXPECTED_N, abs=0.5)

    def test_round_trip(self) -> None:
        fwd = make_transformer(4326, 26910)
        back = make_transformer(26910, 4326)
        lon, lat = back.transform(*fwd.transform(LON, LAT))
        assert lon == pytest.approx(LON, abs=1e-8)
        assert lat == pytest.approx(LAT, abs=1e-8)

    def test_axis_order_trap_without_always_xy(self) -> None:
        # Documents *why* make_transformer exists: EPSG:4326 is officially (lat, lon).
        # Passing (lon, lat) to a default transformer reads -122.7 as a latitude.
        naive = Transformer.from_crs(4326, 26910)
        e, _n = naive.transform(LON, LAT)
        assert not (math.isfinite(e) and abs(e - EXPECTED_E) < 1_000)

    def test_degrees_are_not_a_distance(self) -> None:
        # 0.01° east-west and 0.01° north-south are very different lengths at 45.5°N.
        t = make_transformer(4326, 26910)
        x0, y0 = t.transform(LON, LAT)
        x_east, y_east = t.transform(LON + 0.01, LAT)
        x_north, y_north = t.transform(LON, LAT + 0.01)
        east_m = math.dist((x0, y0), (x_east, y_east))
        north_m = math.dist((x0, y0), (x_north, y_north))
        # Spherical approximation: 0.01° x 111.32 km x cos(lat) vs 0.01° x ~111.1 km.
        assert east_m == pytest.approx(0.01 * 111_320 * math.cos(math.radians(LAT)), rel=0.01)
        assert north_m == pytest.approx(1_111, rel=0.01)
        assert north_m / east_m > 1.35


class TestAreaOfUse:
    def test_portland_inside_utm_10n(self) -> None:
        assert_point_in_area_of_use(load_crs(26910), LON, LAT)

    def test_portland_outside_utm_18n(self) -> None:
        # UTM 18N covers the US East Coast (78°W to 72°W): a wrong-zone config.
        with pytest.raises(AnalysisCRSError, match="outside the area of use"):
            assert_point_in_area_of_use(load_crs(26918), LON, LAT)

    def test_bounds_contains(self) -> None:
        b = LonLatBounds(west=-126, south=30, east=-120, north=80)
        assert b.contains(-123, 45)
        assert not b.contains(-119, 45)
        assert not b.contains(-123, 20)

    def test_bounds_crossing_antimeridian(self) -> None:
        b = LonLatBounds(west=170, south=-30, east=-170, north=0)
        assert b.contains(179, -10)
        assert b.contains(-175, -10)
        assert not b.contains(0, -10)
