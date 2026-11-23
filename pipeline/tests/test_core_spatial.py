"""Shared spatial core on tiny hand-made geometries with hand-computed answers."""

from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box

from touchgrass.core.assign import assign_points_to_areas, points_in_polygon
from touchgrass.core.crs import AnalysisCRSError
from touchgrass.core.geometry import (
    area_m2,
    buffer_in_crs,
    clean_polygons,
    parse_coordinate,
    representative_points,
    to_multipolygon,
)

UTM10 = 26910


class TestGeometry:
    def test_bowtie_is_repaired_to_multipolygon(self) -> None:
        bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])  # self-intersecting
        assert not bowtie.is_valid
        fixed = to_multipolygon(bowtie)
        assert isinstance(fixed, MultiPolygon) and fixed.is_valid
        assert fixed.area == pytest.approx(2.0)  # two triangles of area 1

    def test_line_only_geometry_becomes_none(self) -> None:
        sliver = Polygon([(0, 0), (1, 1), (2, 2)])  # zero area
        assert to_multipolygon(sliver) is None

    def test_clean_polygons_counts_repairs(self) -> None:
        gdf = gpd.GeoDataFrame(
            geometry=[box(0, 0, 1, 1), Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])], crs=4326
        )
        clean, repaired = clean_polygons(gdf)
        assert repaired == 1 and len(clean) == 2 and clean.geometry.is_valid.all()

    def test_buffer_is_in_meters(self) -> None:
        # A point buffered by 2,000 m should have area ~ pi r^2, measured in UTM.
        buffered = buffer_in_crs(Point(-122.6765, 45.5231), 2000, UTM10)
        area = area_m2(gpd.GeoSeries([buffered], crs=4326), UTM10).iloc[0]
        assert area == pytest.approx(math.pi * 2000**2, rel=0.01)

    def test_buffer_refuses_geographic_crs(self) -> None:
        with pytest.raises(AnalysisCRSError):
            buffer_in_crs(Point(0, 0), 10, 4326)

    def test_parse_census_coordinates(self) -> None:
        assert parse_coordinate(pd.Series(["+45.5231000", "-122.6765000"])).tolist() == [
            45.5231,
            -122.6765,
        ]

    def test_representative_point_fallback(self) -> None:
        # C-shaped polygon whose "given" point sits in the notch (outside).
        c_shape = Polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3)])
        polys = gpd.GeoSeries([box(0, 0, 1, 1), c_shape], crs=4326)
        pts, source = representative_points(polys, pd.Series([0.5, 2.0]), pd.Series([0.5, 1.5]))
        assert source.tolist() == ["census_intpt", "point_on_surface"]
        assert polys.intersects(pts, align=False).all()
        assert pts.iloc[0].equals(Point(0.5, 0.5))


class TestAssign:
    # Two areas overlapping in x ∈ [1, 2]; a gap beyond x = 3.
    AREAS = gpd.GeoDataFrame(
        {"area_id": ["a", "b"]}, geometry=[box(0, 0, 2, 1), box(1, 0, 3, 1)], crs=4326
    )

    def points(self, coords: list[tuple[float, float]]) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"pid": [f"p{i}" for i in range(len(coords))]},
            geometry=[Point(c) for c in coords],
            crs=4326,
        )

    def test_overlap_gap_and_single(self) -> None:
        pts = self.points([(0.5, 0.5), (1.5, 0.5), (3.5, 0.5)])
        m = assign_points_to_areas(pts, "pid", self.AREAS, "area_id")
        assert sorted(map(tuple, m.values.tolist())) == [("p0", "a"), ("p1", "a"), ("p1", "b")]

    def test_point_on_shared_edge_is_not_dropped(self) -> None:
        areas = gpd.GeoDataFrame(
            {"area_id": ["w", "e"]}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326
        )
        m = assign_points_to_areas(self.points([(1.0, 0.5)]), "pid", areas, "area_id")
        assert sorted(m["area_id"]) == ["e", "w"]  # counted in both, so validation can see it

    def test_crs_mismatch_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="CRS mismatch"):
            assign_points_to_areas(self.points([(0, 0)]).to_crs(3857), "pid", self.AREAS, "area_id")

    def test_points_in_polygon(self) -> None:
        pts = self.points([(0.5, 0.5), (5, 5)]).geometry
        assert points_in_polygon(pts, box(0, 0, 1, 1)).tolist() == [True, False]
