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
from touchgrass.sources import acs, osm, tiger

SourceKind = Literal["arcgis", "tiger", "acs", "osm"]


@dataclass(frozen=True, slots=True)
class RawSource:
    source_id: str
    kind: SourceKind
    url: str
    path: Path
    attribution: str
    license_name: str
    license_url: str | None
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
        license_name=cfg.study_boundary.license.name,
        license_url=cfg.study_boundary.license.url,
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
                license_name=area.license.name,
                license_url=area.license.url,
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
        license_name=tiger.TIGER_LICENSE[0],
        license_url=tiger.TIGER_LICENSE[1],
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
            license_name=acs.ACS_LICENSE[0],
            license_url=acs.ACS_LICENSE[1],
            vintage=f"{year - 4}-{year}",
        )
        for table in cfg.census.acs.tables
    ]


def osm_source(cfg: RegionConfig, paths: RegionPaths) -> RawSource | None:
    if cfg.osm is None:
        return None
    return RawSource(
        source_id="osm_extract",
        kind="osm",
        url=osm.extract_url(cfg.osm),
        path=paths.raw_source("osm") / osm.extract_filename(cfg.osm),
        attribution=osm.OSM_ATTRIBUTION,
        license_name=osm.OSM_LICENSE[0],
        license_url=osm.OSM_LICENSE[1],
        vintage=cfg.osm.snapshot,
    )


def park_validation_source(cfg: RegionConfig, paths: RegionPaths) -> RawSource | None:
    if cfg.parks is None or cfg.parks.validation_source is None:
        return None
    v = cfg.parks.validation_source
    sid = "parks_validation"
    return RawSource(
        source_id=sid,
        kind="arcgis",
        url=v.url,
        path=paths.raw_source(sid) / f"{sid}.geojson",
        attribution=v.attribution,
        license_name=v.license.name,
        license_url=v.license.url,
        where=v.where,
    )


def all_sources(cfg: RegionConfig, paths: RegionPaths) -> list[RawSource]:
    optional = [osm_source(cfg, paths), park_validation_source(cfg, paths)]
    return [
        boundary_source(cfg, paths),
        *area_sources(cfg, paths),
        tiger_source(cfg, paths, "tabblock20"),
        tiger_source(cfg, paths, "bg"),
        *acs_sources(cfg, paths),
        *(s for s in optional if s is not None),
    ]
