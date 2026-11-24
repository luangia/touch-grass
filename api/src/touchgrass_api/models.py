"""Response models: the API's contract with the map (validated on the way out).

GeoJSON and vector tiles are built in SQL and returned as raw bytes; everything else goes
through these models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BBox(_Model):
    west: float
    south: float
    east: float
    north: float


class RegionSummary(_Model):
    region_id: str
    name: str
    bbox: BBox = Field(description="Study boundary extent, WGS 84 degrees")
    center: tuple[float, float] = Field(description="(longitude, latitude) of the bbox center")
    latest_run_id: str | None


class Threshold(_Model):
    minutes: int
    meters: float


class AreaType(_Model):
    area_type: str
    areas: int


class RegionDetail(RegionSummary):
    analysis_crs: int
    walk_speed_kmh: float
    thresholds: list[Threshold]
    underserved_minutes: int
    area_types: list[AreaType]
    acs_vintage: str | None
    osm_snapshot: str | None
    metrics_available: bool


class ThresholdShare(_Model):
    threshold_min: int
    threshold_m: float
    pop_within: int
    pct_within: float


class ContextValue(_Model):
    indicator: str
    kind: Literal["count", "share"]
    estimate: float | None = Field(description="A fraction in [0, 1] for kind = share")
    moe: float | None = Field(description="90% margin of error")
    cv: float | None
    reliability: Literal["high", "medium", "low"]
    numerator: float | None
    denominator: float | None
    acs_vintage: str


class AreaMetrics(_Model):
    area_id: str
    area_type: str
    name: str
    pop_total: int
    blocks: int
    underserved_pop: int
    underserved_share: float
    unreachable_pop: int
    median_walk_m: float | None = Field(description="Population-weighted median walk")
    priority_score: float | None = Field(
        description="Underserved residents per km² of populated-block land (D-011)"
    )
    ranked: bool = Field(description="Large enough for share and priority rankings (D-022)")
    reachable_park_ha_median: float
    reachable_park_ha_mean: float
    park_m2_inside: float
    park_m2_inside_per_resident: float
    block_share_within: float
    land_share_within: float | None
    thresholds: list[ThresholdShare]
    context: list[ContextValue]


class AreaMetricsResponse(_Model):
    region_id: str
    run_id: str
    area: AreaMetrics


class Comparison(_Model):
    region_id: str
    run_id: str
    threshold_min: int
    areas: list[AreaMetrics]


class RankingEntry(_Model):
    rank: int
    area_id: str
    name: str
    value: float
    pop_total: int
    underserved_pop: int
    pct_within: float


class Ranking(_Model):
    region_id: str
    run_id: str
    area_type: str
    metric: str
    threshold_min: int
    order: Literal["asc", "desc"]
    min_residents: int | None = Field(description="Floor applied to this ranking, if any")
    entries: list[RankingEntry]


class Source(_Model):
    source_id: str
    url: str
    vintage: str | None
    retrieved_at: str
    attribution: str


class Caveat(_Model):
    id: str
    title: str
    text: str


class Methodology(_Model):
    region_id: str
    run_id: str | None
    walk_speed_kmh: float
    thresholds: list[Threshold]
    underserved_minutes: int
    analysis: dict[str, object] = Field(description="Parameters of the latest analysis run")
    metrics: dict[str, object] | None = Field(description="Parameters of its area metrics")
    sources: list[Source]
    caveats: list[Caveat]


class Health(_Model):
    status: Literal["ok", "unavailable"]
    database: str
    schema_version: int | None
    expected_schema_version: int
