"""Park rules, OSM extraction, overlap merging, and dataset comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import box

from touchgrass.config.models import ParksConfig
from touchgrass.core import compare
from touchgrass.core.parks import (
    ACCESS,
    TOO_SMALL,
    classify,
    classify_candidates,
    merge_into_units,
    missing_sentinels,
    overlap_components,
)
from touchgrass.sources.osm import extract_areas

from .osm_fixture import OsmFixture, cell

UTM10 = 26910

RULES = ParksConfig.model_validate(
    {
        "include_tags": {
            "leisure": ["park", "nature_reserve", "dog_park"],
            "boundary": ["protected_area"],
        },
        "natural_area_tags": {"leisure": ["nature_reserve"], "boundary": ["protected_area"]},
        "exclude_tags": {
            "not_open_space": {"leisure": ["golf_course"], "landuse": ["cemetery"]},
            "land_cover_not_a_park": {"landuse": ["grass", "forest"]},
            "amenity_usually_in_park_or_school": {"leisure": ["playground"]},
        },
        "min_area_m2": 400,
        "sentinel_parks": ["Big Woods"],
    }
)


# ----------------------------------------------------------------------------- rules


class TestClassify:
    @pytest.mark.parametrize(
        ("tags", "included", "park_class", "reason"),
        [
            ({"leisure": "park"}, True, "park", "included"),
            # The "Forest Park pattern": a reserve/protected area with no leisure=park.
            (
                {"leisure": "nature_reserve", "boundary": "protected_area"},
                True,
                "natural_area",
                "included",
            ),
            ({"boundary": "protected_area"}, True, "natural_area", "included"),
            ({"leisure": "park", "access": "private"}, False, None, ACCESS),
            ({"leisure": "park", "access": "no"}, False, None, ACCESS),
            ({"leisure": "park", "access": "permissive"}, True, "park", "included"),
            ({"leisure": "golf_course"}, False, None, "not_open_space"),
            ({"landuse": "cemetery"}, False, None, "not_open_space"),
            ({"landuse": "grass"}, False, None, "land_cover_not_a_park"),
            ({"leisure": "playground"}, False, None, "amenity_usually_in_park_or_school"),
            # An include tag wins over an exclude tag on the same object.
            ({"leisure": "park", "landuse": "grass"}, True, "park", "included"),
        ],
    )
    def test_rules(
        self, tags: dict[str, str], included: bool, park_class: str | None, reason: str
    ) -> None:
        d = classify(tags, RULES)
        assert (d.included, d.park_class, d.reason) == (included, park_class, reason)

    def test_natural_tags_must_be_included(self) -> None:
        with pytest.raises(ValueError, match="not in include_tags"):
            ParksConfig.model_validate(
                {"include_tags": {"leisure": ["park"]}, "natural_area_tags": {"leisure": ["x"]}}
            )

    def test_too_small_is_excluded_after_inclusion(self) -> None:
        tiny = box(-123.0, 45.0, -123.0 + 0.0001, 45.0001)  # ~ 8 m x 11 m
        cands = gpd.GeoDataFrame(
            {
                "osm_type": ["way"],
                "osm_id": [1],
                "name": ["Pocket"],
                "tags": [json.dumps({"leisure": "park"})],
            },
            geometry=[tiny],
            crs=4326,
        )
        out = classify_candidates(cands, RULES, UTM10)
        assert out.loc[0, "reason"] == TOO_SMALL and not out.loc[0, "included"]


# ----------------------------------------------------------------------------- merge


def units_from(boxes: list[tuple[str, Any, str]]) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(
        {
            "osm_type": "way",
            "osm_id": range(1, len(boxes) + 1),
            "name": [b[0] for b in boxes],
            "park_class": [b[2] for b in boxes],
            "area_m2": 0.0,
        },
        geometry=[b[1] for b in boxes],
        crs=4326,
    )
    gdf["area_m2"] = gdf.to_crs(UTM10).area
    return merge_into_units(gdf, UTM10)


class TestMerge:
    def test_overlap_merges_touch_does_not(self) -> None:
        units = units_from(
            [
                ("Reserve", box(*cell(0)), "natural_area"),
                ("Inner Park", box(-123.015, 45.002, -123.012, 45.004), "park"),  # inside cell 0
                ("Neighbour", box(*cell(1)), "park"),  # shares an edge with cell 0
            ]
        )
        assert len(units) == 2
        big = units.loc[units["n_members"] == 2].iloc[0]
        assert big["name"] == "Reserve"  # name of the largest member
        assert big["park_class"] == "natural_area"  # any natural member → natural_area
        assert big["member_names"] == "Inner Park; Reserve"

    def test_overlap_is_transitive(self) -> None:
        # metres: a-b and b-c overlap by 500 m^2 each; a and c never touch
        a, b, c = box(0, 0, 200, 100), box(195, 0, 395, 100), box(390, 0, 590, 100)
        labels = overlap_components(gpd.GeoSeries([a, b, c]).values)
        assert len(set(labels)) == 1

    def test_sentinels(self) -> None:
        units = units_from([("Big Woods", box(*cell(0)), "natural_area")])
        assert missing_sentinels(units, ["big woods", "Nowhere Park"]) == ["Nowhere Park"]


# --------------------------------------------------------------------------- compare


class TestCompare:
    def test_half_overlap(self) -> None:
        a = box(-123.02, 45.0, -123.00, 45.01)  # two cells
        b = box(-123.01, 45.0, -122.99, 45.01)  # shifted by one cell
        agg = compare.area_agreement(a, b, UTM10)
        assert agg["precision"] == pytest.approx(0.5, abs=0.01)
        assert agg["recall"] == pytest.approx(0.5, abs=0.01)
        pieces = compare.discrepancy_pieces(a, b, UTM10)
        assert sorted(pieces["direction"]) == ["a_only", "b_only"]

    def test_feature_coverage_counts_each_feature_once(self) -> None:
        feats = gpd.GeoDataFrame(geometry=[box(*cell(0)), box(*cell(3))], crs=4326)
        cov = compare.feature_coverage(feats, box(*cell(0)), UTM10)
        assert cov == {"features": 2, "covered": 1, "share": 0.5}


# ---------------------------------------------------------------------------- extract


WOODS_OUTER = (-123.02, 45.01, -122.99, 45.04)
WOODS_HOLE = (-123.01, 45.015, -123.00, 45.025)  # strictly inside WOODS_OUTER


def build_osm(path: Path) -> Path:
    f = OsmFixture()
    f.area_way(10, cell(0), leisure="park", name="Plain Park")
    f.area_way(11, cell(1), leisure="park", access="private", name="Private Park")
    f.area_way(12, cell(2), leisure="golf_course", name="Links")
    f.area_way(13, cell(3), landuse="grass")
    f.area_way(14, cell(9, 9), leisure="park", name="Far Away Park")  # outside study area
    f.area_way(15, cell(4), highway="residential")  # not a candidate tag at all
    f.multipolygon(
        100,
        outer=WOODS_OUTER,
        inner=WOODS_HOLE,
        way_ids=(20, 21),
        leisure="nature_reserve",
        name="Big Woods",
    )
    f.broken_relation(101, missing_way=999, leisure="park", name="Ghost Park")
    return f.write(path)


class TestExtract:
    def test_extract_and_classify(self, tmp_path: Path) -> None:
        pbf = build_osm(tmp_path / "fixture.osm.pbf")
        study = box(-123.05, 44.99, -122.95, 45.05)
        cands, stats = extract_areas(pbf, RULES.candidate_tags(), study)

        assert set(cands["name"].dropna()) == {"Plain Park", "Private Park", "Links", "Big Woods"}
        assert stats.broken_relations == [101]
        woods = cands[cands["name"] == "Big Woods"].iloc[0]
        assert woods["osm_type"] == "relation" and woods["osm_id"] == 100
        # The hole is respected: outer 0.03° square minus a 0.01° square inside it.
        assert len(woods.geometry.geoms) == 1 and len(woods.geometry.geoms[0].interiors) == 1
        outer = gpd.GeoSeries([box(*WOODS_OUTER)], crs=4326).to_crs(UTM10).area.iloc[0]
        hole = gpd.GeoSeries([box(*WOODS_HOLE)], crs=4326).to_crs(UTM10).area.iloc[0]
        got = gpd.GeoSeries([woods.geometry], crs=4326).to_crs(UTM10).area.iloc[0]
        assert got == pytest.approx(outer - hole, rel=1e-6)

        decided = classify_candidates(cands, RULES, UTM10).set_index("name")
        assert decided.loc["Plain Park", "included"]
        assert decided.loc["Private Park", "reason"] == ACCESS
        assert decided.loc["Links", "reason"] == "not_open_space"
        assert decided.loc["Big Woods", "park_class"] == "natural_area"


# -------------------------------------------------------------------------- subtract


class TestSubtract:
    def test_hole_cut_dropped_and_untouched(self) -> None:
        from touchgrass.core.parks import subtract_excluded

        units = units_from(
            [
                ("Ring Park", box(*cell(0)), "park"),  # gets a golf course cut out
                ("Fairway Park", box(*cell(1)), "park"),  # entirely covered → dropped
                ("Quiet Park", box(*cell(3)), "park"),  # untouched
            ]
        )
        golf_inner = box(-123.017, 45.003, -123.013, 45.007)
        holes = gpd.GeoDataFrame(geometry=[golf_inner, box(*cell(1))], crs=4326)
        out, stats = subtract_excluded(units, holes, UTM10, min_area_m2=400)

        by_name = out.set_index("name")
        hole_m2 = gpd.GeoSeries([golf_inner], crs=4326).to_crs(UTM10).area.iloc[0]
        assert by_name.loc["Ring Park", "subtracted_m2"] == pytest.approx(hole_m2, rel=1e-6)
        assert len(by_name.loc["Ring Park", "geometry"].geoms[0].interiors) == 1
        assert by_name.loc["Quiet Park", "subtracted_m2"] == 0
        assert "Fairway Park" not in by_name.index
        assert stats["units_cut"] == 1 and len(stats["units_dropped"]) == 1  # type: ignore[arg-type]

    def test_unknown_subtract_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match="not exclude_tags reasons"):
            ParksConfig.model_validate(
                {"include_tags": {"leisure": ["park"]}, "subtract_reasons": ["nope"]}
            )
