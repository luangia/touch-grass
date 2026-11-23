"""Compare two polygon datasets that describe the same kind of place.

Area-weighted agreement, treating B as the reference:

    precision = area(A ∩ B) / area(A)   how much of A is confirmed by B
    recall    = area(A ∩ B) / area(B)   how much of B is captured by A

Area metrics are dominated by large polygons, so feature-level coverage and the largest
disagreement pieces are reported too. All areas are measured in the analysis CRS.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import shapely
from shapely.geometry.base import BaseGeometry

from touchgrass.core.crs import STORAGE_EPSG, assert_analysis_crs

ACRE_M2 = 4046.8564224


def _projected(geom: BaseGeometry, epsg: int) -> BaseGeometry:
    return gpd.GeoSeries([geom], crs=STORAGE_EPSG).to_crs(epsg).iloc[0]


def area_agreement(a: BaseGeometry, b: BaseGeometry, analysis_epsg: int) -> dict[str, float]:
    assert_analysis_crs(analysis_epsg)
    pa, pb = _projected(a, analysis_epsg), _projected(b, analysis_epsg)
    overlap = shapely.intersection(pa, pb).area
    return {
        "area_a_km2": round(pa.area / 1e6, 4),
        "area_b_km2": round(pb.area / 1e6, 4),
        "overlap_km2": round(overlap / 1e6, 4),
        "precision": round(overlap / pa.area, 4) if pa.area else 0.0,
        "recall": round(overlap / pb.area, 4) if pb.area else 0.0,
    }


def feature_coverage(
    features: gpd.GeoDataFrame,
    other: BaseGeometry,
    analysis_epsg: int,
    min_area_m2: float = ACRE_M2,
    min_share: float = 0.5,
) -> dict[str, Any]:
    """Share of features (≥ min_area_m2) whose area is at least ``min_share`` covered by
    ``other``. Unlike area metrics, every feature counts once regardless of size."""
    proj = features.to_crs(analysis_epsg).geometry
    other_p = _projected(other, analysis_epsg)
    shapely.prepare(other_p)
    big = proj[proj.area >= min_area_m2]
    if big.empty:
        return {"features": 0, "covered": 0, "share": None}
    covered = shapely.area(shapely.intersection(big.values, other_p)) / big.area.values
    n_cov = int((covered >= min_share).sum())
    return {"features": len(big), "covered": n_cov, "share": round(n_cov / len(big), 4)}


def discrepancy_pieces(
    a: BaseGeometry,
    b: BaseGeometry,
    analysis_epsg: int,
    min_area_m2: float = ACRE_M2 / 2,
) -> gpd.GeoDataFrame:
    """Contiguous pieces of A minus B ('a_only') and B minus A ('b_only'), largest first."""
    pa, pb = _projected(a, analysis_epsg), _projected(b, analysis_epsg)
    frames = []
    for direction, diff in (("a_only", pa.difference(pb)), ("b_only", pb.difference(pa))):
        pieces = gpd.GeoSeries([diff], crs=analysis_epsg).explode(index_parts=False)
        pieces = pieces[pieces.area >= min_area_m2]
        frames.append(
            gpd.GeoDataFrame(
                {"direction": direction, "area_m2": pieces.area.values},
                geometry=pieces.values,
                crs=analysis_epsg,
            )
        )
    out = gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True), geometry="geometry", crs=analysis_epsg
    )
    out = out.to_crs(STORAGE_EPSG)
    return out.sort_values(["direction", "area_m2"], ascending=[True, False], ignore_index=True)
