"""Typed schema for ``config/regions/<region_id>.yaml``.

Everything region-specific lives in these files; code reads it from a validated
``RegionConfig`` and never hardcodes it (DECISIONS.md D-004).

Stage 1 scope: identity, CRS, census FIPS codes, buffer, analysis parameters.
Data-source sections (boundary, analysis areas, parks, population) are added in the
stages that implement their adapters, so the schema never accepts unused settings.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator
from pyproj import CRS

from touchgrass.core.crs import assert_analysis_crs, assert_point_in_area_of_use, load_crs

Longitude = Annotated[float, Field(ge=-180, le=180)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
CountyFips = Annotated[str, Field(pattern=r"^\d{3}$")]


class _StrictModel(BaseModel):
    # forbid: unknown keys are almost always typos. frozen: config is read-only once loaded.
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisParams(_StrictModel):
    """Walking-accessibility parameters (DECISIONS.md D-005)."""

    walk_speed_kmh: float = Field(
        default=4.8, gt=0, le=10, description="Assumed walking speed (4.8 km/h = 80 m/min)"
    )
    thresholds_min: list[PositiveInt] = Field(
        default_factory=lambda: [5, 10, 15], min_length=1, description="Walk-time thresholds"
    )
    max_snap_m: float = Field(
        default=500,
        gt=0,
        description="Origins farther than this from the walk network are flagged",
    )

    @field_validator("thresholds_min")
    @classmethod
    def _unique_ascending(cls, v: list[int]) -> list[int]:
        if v != sorted(set(v)):
            raise ValueError(f"must be unique and in ascending order, got {v}")
        return v

    @property
    def meters_per_minute(self) -> float:
        return self.walk_speed_kmh * 1000 / 60

    @property
    def threshold_distances_m(self) -> dict[int, float]:
        """Threshold minutes -> walking distance in meters (derived, never stored)."""
        return {t: t * self.meters_per_minute for t in self.thresholds_min}


class RegionConfig(_StrictModel):
    """One study region (a city or metro area)."""

    region_id: str = Field(
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description="Stable slug used in file names, database rows and URLs, e.g. 'springfield-il'",
    )
    name: str = Field(min_length=1)
    country: Literal["US"] = Field(description="Only US census adapters exist for now")
    analysis_crs: int = Field(description="EPSG code of a projected, meter-based CRS")
    center_lonlat: tuple[Longitude, Latitude] = Field(
        description="Rough (longitude, latitude) of the region; used only to sanity-check "
        "analysis_crs. The map view comes from data, not from this value."
    )
    state_fips: str = Field(pattern=r"^\d{2}$", description="2-digit state FIPS code")
    county_fips: list[CountyFips] = Field(
        min_length=1, description="3-digit county FIPS codes the region overlaps"
    )
    buffer_m: float = Field(
        gt=0,
        le=20_000,
        description="Network/park search buffer beyond the study boundary (edge effects)",
    )
    analysis: AnalysisParams = Field(default_factory=AnalysisParams)

    @field_validator("analysis_crs")
    @classmethod
    def _metric_projected_crs(cls, epsg: int) -> int:
        assert_analysis_crs(epsg)  # raises AnalysisCRSError (a ValueError) with a reason
        return epsg

    @field_validator("county_fips")
    @classmethod
    def _unique_counties(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError(f"contains duplicates: {v}")
        return v

    @model_validator(mode="after")
    def _center_within_crs_area(self) -> Self:
        # Runs only after every field is individually valid, so analysis_crs is known-good.
        lon, lat = self.center_lonlat
        assert_point_in_area_of_use(load_crs(self.analysis_crs), lon, lat)
        return self

    def crs(self) -> CRS:
        """The analysis CRS as a pyproj object."""
        return load_crs(self.analysis_crs)
