"""The list of raw inputs a region needs, and where each is stored.

Shared by ``fetch`` (which downloads them) and ``process`` (which reads them), so both
agree on file locations without either hardcoding paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from touchgrass.config import RegionConfig
from touchgrass.paths import RegionPaths
from touchgrass.sources import acs, tiger

SourceKind = Literal["arcgis", "tiger", "acs"]


@dataclass(frozen=True, slots=True)
class RawSource:
    source_id: str
    kind: SourceKind
    url: str
    path: Path
    attribution: str
    where: str = "1=1"  # arcgis only
    vintage: str | None = None


def boundary_source(cfg: RegionConfig, paths: RegionPaths) -> RawSource:
    sid = "study_boundary"
    return RawSource(
        source_id=sid,
        kind="arcgis",
        url=cfg.study_boundary.url,
        path=paths.raw_source(sid) / f"{sid}.geojson",
        attribution=cfg.study_boundary.attribution,
        where=cfg.study_boundary.where,
    )


def area_sources(cfg: RegionConfig, paths: RegionPaths) -> list[RawSource]:
    out = []
    for area in cfg.analysis_areas:
        sid = f"areas_{area.area_type}"
        out.append(
            RawSource(
                source_id=sid,
                kind="arcgis",
                url=area.url,
                path=paths.raw_source(sid) / f"{sid}.geojson",
                attribution=area.attribution,
                where=area.where,
            )
        )
    return out


def tiger_source(cfg: RegionConfig, paths: RegionPaths, layer: tiger.TigerLayer) -> RawSource:
    vintage = cfg.census.decennial_vintage
    return RawSource(
        source_id=f"tiger_{layer}",
        kind="tiger",
        url=tiger.tiger_url(vintage, cfg.state_fips, layer),
        path=paths.raw_source("tiger") / tiger.tiger_filename(vintage, cfg.state_fips, layer),
        attribution=tiger.TIGER_ATTRIBUTION,
        vintage=str(vintage),
    )


def acs_sources(cfg: RegionConfig, paths: RegionPaths) -> list[RawSource]:
    year = cfg.census.acs.year
    return [
        RawSource(
            source_id=f"acs_{table.lower()}",
            kind="acs",
            url=acs.acs_url(year, table),
            path=paths.raw_source("acs") / acs.acs_filename(year, table),
            attribution=acs.ACS_ATTRIBUTION,
            vintage=f"{year - 4}-{year}",
        )
        for table in cfg.census.acs.tables
    ]


def all_sources(cfg: RegionConfig, paths: RegionPaths) -> list[RawSource]:
    return [
        boundary_source(cfg, paths),
        *area_sources(cfg, paths),
        tiger_source(cfg, paths, "tabblock20"),
        tiger_source(cfg, paths, "bg"),
        *acs_sources(cfg, paths),
    ]
