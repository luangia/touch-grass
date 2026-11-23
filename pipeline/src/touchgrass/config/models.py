"""Typed schema for ``config/regions/<region_id>.yaml``.

Everything region-specific lives in these files; code reads it from a validated
``RegionConfig`` and never hardcodes it (DECISIONS.md D-004).

Sections are added in the stage that implements the code consuming them, so the schema
never accepts settings that nothing uses:
  Stage 1: identity, CRS, census FIPS codes, buffer, analysis parameters.
  Stage 2: study boundary, analysis areas, census vintages/tables, validation targets.
  Stage 3: OSM extract (osm) and park classification rules (parks).
  Stage 5: walk network and park access points (network).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator
from pyproj import CRS

from touchgrass.core.crs import assert_analysis_crs, assert_point_in_area_of_use, load_crs

Longitude = Annotated[float, Field(ge=-180, le=180)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
CountyFips = Annotated[str, Field(pattern=r"^\d{3}$")]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(_[a-z0-9]+)*$")]
AcsTable = Annotated[str, Field(pattern=r"^B\d{5}[A-I]?$")]


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


# ---------------------------------------------------------------------------- sources


class ArcGISLayerSource(_StrictModel):
    """One layer of an Esri ArcGIS Server (MapServer or FeatureServer) REST service.

    ``adapter`` is a tag: when other adapter types exist this becomes a discriminated union.
    """

    adapter: Literal["arcgis_feature_layer"]
    url: str = Field(
        pattern=r"^https://\S+/(MapServer|FeatureServer)/\d+$",
        description="Layer URL ending in /MapServer/<id> or /FeatureServer/<id>",
    )
    where: str = Field(default="1=1", min_length=1, description="Server-side attribute filter")
    attribution: str = Field(min_length=1, description="Required credit line for the data")


class AnalysisAreaSource(ArcGISLayerSource):
    """A set of polygons that results are reported by (neighborhoods, districts, ...)."""

    area_type: Slug = Field(description="Generic type, e.g. 'neighborhood'")
    key_field: str = Field(min_length=1, description="Source field with a stable unique id")
    name_field: str = Field(min_length=1, description="Source field with a display name")
    allow_overlaps: bool = Field(
        default=False, description="May one block belong to several areas of this type?"
    )
    population_check_field: str | None = Field(
        default=None,
        description="Source field holding the area's official population, used to reconcile "
        "against summed block populations",
    )


class AcsConfig(_StrictModel):
    year: int = Field(ge=2009, le=2100, description="Final year of the ACS 5-year period")
    tables: list[AcsTable] = Field(min_length=1, description="Detailed tables, e.g. B19013")

    @field_validator("tables")
    @classmethod
    def _unique_tables(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError(f"contains duplicates: {v}")
        return v


class CensusConfig(_StrictModel):
    decennial_vintage: Literal[2020] = Field(
        description="Census blocks and their counts (TIGER/Line tabblock20 POP20/HOUSING20)"
    )
    acs: AcsConfig


class ValidationConfig(_StrictModel):
    expected_population: PositiveInt | None = Field(
        default=None, description="Official population of the study area, for reconciliation"
    )
    population_tolerance: float = Field(
        default=0.01, ge=0, le=0.2, description="Allowed relative difference (0.01 = 1%)"
    )


# ------------------------------------------------------------------------ parks (Stage 3)

TagRule = dict[Annotated[str, Field(min_length=1)], list[Annotated[str, Field(min_length=1)]]]


class OsmConfig(_StrictModel):
    """A dated OpenStreetMap extract from Geofabrik (DECISIONS.md D-017)."""

    geofabrik_path: str = Field(
        pattern=r"^[a-z0-9-]+(/[a-z0-9-]+)*$",
        description="Extract path on download.geofabrik.de, e.g. 'north-america/us/oregon'",
    )
    snapshot: str = Field(
        pattern=r"^\d{6}$", description="Snapshot date YYMMDD, e.g. '261101' = 1 Nov 2026"
    )

    @property
    def extract_name(self) -> str:
        return self.geofabrik_path.rsplit("/", 1)[-1]


class TagVeto(_StrictModel):
    """Overrules an include tag: a candidate with any ``tags`` match is excluded, unless it
    also has an ``unless`` match. The value ``"*"`` matches any value of that key."""

    tags: TagRule = Field(min_length=1)
    unless: TagRule = Field(default_factory=dict)


class ClosedPark(_StrictModel):
    """A green space known to be closed to the public although OSM doesn't say so."""

    osm: str = Field(pattern=r"^[nwr][1-9][0-9]*$", description="OSM id, e.g. 'w1365628608'")
    name: str = Field(min_length=1, description="For humans; checked against the data")
    source: str = Field(min_length=1, description="How we know (field check, agency notice)")


class ParksConfig(_StrictModel):
    """Rules deciding which OSM areas are public green space (DECISIONS.md D-018).

    Order of evaluation for each candidate area:
      1. has an ``include_tags`` tag → excluded if ``access`` ∈ ``exclude_access``, then
         excluded by the first matching ``veto_tags`` group (D-018 amendment 2), else
         included (class ``natural_area`` if it has a ``natural_area_tags`` tag);
      2. otherwise the first ``exclude_tags`` group whose tags match gives the reason;
      3. included candidates smaller than ``min_area_m2`` are excluded as too small, and
         those listed in ``closed_to_public`` are excluded with that reason;
      4. after merging, areas excluded for a ``subtract_reasons`` reason are cut out of
         the units that enclose them (units left below ``min_area_m2`` are dropped).
    """

    include_tags: TagRule = Field(min_length=1)
    exclude_tags: dict[Slug, TagRule] = Field(
        default_factory=dict,
        description="Reason → tags. Matching candidates are recorded with that reason",
    )
    exclude_access: list[str] = Field(default_factory=lambda: ["private", "no"])
    veto_tags: dict[Slug, TagVeto] = Field(
        default_factory=dict,
        description="Reason → veto. Overrules an include tag, e.g. a building or a heritage "
        "site that OSM tags as a protected area",
    )
    natural_area_tags: TagRule = Field(default_factory=dict)
    min_area_m2: float = Field(default=400, ge=0)
    validation_source: ArcGISLayerSource | None = Field(
        default=None, description="Authoritative park polygons to compare against"
    )
    subtract_reasons: list[Slug] = Field(
        default_factory=list,
        description="Exclude reasons whose areas are cut out of any park that encloses them "
        "(e.g. a golf course drawn inside a park outline)",
    )
    closed_to_public: list[ClosedPark] = Field(
        default_factory=list,
        description="Known closures OSM doesn't record; better fixed in OSM (access=no) too",
    )
    sentinel_parks: list[str] = Field(
        default_factory=list,
        description="Names that must appear among included parks (catches rule mistakes)",
    )

    @model_validator(mode="after")
    def _subtract_reasons_exist(self) -> Self:
        unknown = set(self.subtract_reasons) - set(self.exclude_tags)
        if unknown:
            raise ValueError(f"subtract_reasons {sorted(unknown)} are not exclude_tags reasons")
        return self

    @model_validator(mode="after")
    def _veto_reasons_are_distinct(self) -> Self:
        clash = set(self.veto_tags) & (
            set(self.exclude_tags)
            | {"included", "too_small", "access_restricted", "closed_to_public"}
        )
        if clash:
            raise ValueError(f"veto_tags reasons {sorted(clash)} clash with other reasons")
        return self

    @model_validator(mode="after")
    def _closed_unique(self) -> Self:
        ids = [c.osm for c in self.closed_to_public]
        if len(ids) != len(set(ids)):
            raise ValueError(f"closed_to_public lists an OSM id twice: {ids}")
        if "closed_to_public" in self.exclude_tags:
            raise ValueError("'closed_to_public' is a built-in reason, not an exclude_tags key")
        return self

    @model_validator(mode="after")
    def _natural_tags_are_included(self) -> Self:
        for key, values in self.natural_area_tags.items():
            missing = set(values) - set(self.include_tags.get(key, []))
            if missing:
                raise ValueError(f"natural_area_tags {key}={sorted(missing)} not in include_tags")
        return self

    def candidate_tags(self) -> dict[str, set[str]]:
        """Every key=value worth recording as a candidate (included or excluded)."""
        out: dict[str, set[str]] = {}
        for rule in (self.include_tags, *self.exclude_tags.values()):
            for key, values in rule.items():
                out.setdefault(key, set()).update(values)
        return out


# ---------------------------------------------------------------------------- network

# OSMnx 2.1 `walk` filter (osmnx._overpass._get_network_filter("walk")), restated here so
# the rules are visible, testable and overridable per region (DECISIONS.md D-020).
OSMNX_WALK_EXCLUDE_HIGHWAY = (
    "abandoned|bus_guideway|construction|cycleway|motor|no|planned|platform|proposed|"
    "raceway|razed|rest_area|services"
)


def _osmnx_walk_exclude_tags() -> TagRule:
    return {
        "area": ["yes"],
        "access": ["private"],
        "foot": ["no"],
        "service": ["private"],
        "sidewalk": ["separate"],
        "sidewalk:both": ["separate"],
        "sidewalk:left": ["separate"],
        "sidewalk:right": ["separate"],
    }


class WalkFilter(_StrictModel):
    """Which OSM ``highway=*`` ways are walkable.

    A way is walkable when it has a ``highway`` tag, no ``exclude_tags`` match, and either
    its ``highway`` value does not match ``exclude_highway`` (a regex searched anywhere in
    the value, as Overpass ``!~`` does) or it satisfies one of ``readmit`` (every key in
    that entry matches). ``readmit`` cannot override ``exclude_tags``.
    """

    exclude_highway: str = Field(default=OSMNX_WALK_EXCLUDE_HIGHWAY, min_length=1)
    exclude_tags: TagRule = Field(default_factory=_osmnx_walk_exclude_tags)
    readmit: list[TagRule] = Field(
        default_factory=list,
        description="Ways excluded by exclude_highway that are walkable after all, e.g. "
        "shared-use paths: {highway: [cycleway], foot: [designated, yes]}",
    )

    @field_validator("exclude_highway")
    @classmethod
    def _valid_regex(cls, v: str) -> str:
        re.compile(v)
        return v

    @field_validator("readmit")
    @classmethod
    def _readmit_names_highway(cls, v: list[TagRule]) -> list[TagRule]:
        for rule in v:
            if "highway" not in rule:
                raise ValueError(f"each readmit rule must constrain 'highway', got {rule}")
        return v


class NetworkConfig(_StrictModel):
    """Walk network and park access points (DECISIONS.md D-005, D-006, D-020)."""

    walk_filter: WalkFilter = Field(default_factory=WalkFilter)
    access_tolerance_m: float = Field(
        default=15,
        gt=0,
        le=100,
        description="Network within this distance of a park edge counts as reaching the park",
    )
    entrance_tags: TagRule = Field(
        default_factory=lambda: {"entrance": ["*"], "barrier": ["gate"]},
        description="OSM nodes treated as park entrances when near a park edge",
    )
    entrance_exclude_access: list[str] = Field(default_factory=lambda: ["private", "no"])
    water_tags: TagRule = Field(
        default_factory=lambda: {
            "natural": ["water"],
            "waterway": ["riverbank"],
            "landuse": ["reservoir", "basin"],
        },
        description="OSM areas a straight-line connector must not cross (rivers, lakes)",
    )
    max_park_gap_m: float = Field(
        default=100,
        ge=0,
        le=500,
        description="A park with no network within access_tolerance_m connects to the "
        "nearest network within this distance, if the gap crosses no water",
    )
    min_main_component_share: float = Field(default=0.95, gt=0, le=1)
    min_population_within_100m: float = Field(default=0.95, gt=0, le=1)


# ----------------------------------------------------------------------------- region


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
    study_boundary: ArcGISLayerSource
    analysis_areas: list[AnalysisAreaSource] = Field(min_length=1)
    census: CensusConfig
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    osm: OsmConfig | None = None
    parks: ParksConfig | None = None
    network: NetworkConfig | None = None

    @model_validator(mode="after")
    def _network_needs_parks(self) -> Self:
        if self.network is not None and self.parks is None:
            raise ValueError("'network' requires 'parks' (access points are attached to parks)")
        return self

    @model_validator(mode="after")
    def _parks_need_osm(self) -> Self:
        if self.parks is not None and self.osm is None:
            raise ValueError("'parks' requires an 'osm' section (parks are extracted from OSM)")
        return self

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

    @field_validator("analysis_areas")
    @classmethod
    def _unique_area_types(cls, v: list[AnalysisAreaSource]) -> list[AnalysisAreaSource]:
        types = [a.area_type for a in v]
        if len(types) != len(set(types)):
            raise ValueError(f"area_type values must be unique, got {types}")
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
