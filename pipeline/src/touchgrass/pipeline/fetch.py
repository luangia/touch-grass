"""Step 1: download every raw input for a region (the only step that uses the network)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from touchgrass.config import RegionConfig
from touchgrass.paths import RegionPaths
from touchgrass.pipeline.sources import RawSource, all_sources
from touchgrass.sources import acs, arcgis, http


@dataclass(frozen=True, slots=True)
class FetchResult:
    source: RawSource
    manifest: http.Manifest
    downloaded: bool

    @property
    def path(self) -> Path:
        return self.source.path


def fetch_one(
    client: httpx.Client, cfg: RegionConfig, src: RawSource, refresh: bool
) -> FetchResult:
    if src.kind == "arcgis":
        manifest, downloaded = arcgis.fetch_layer(
            client,
            src.url,
            src.path,
            source_id=src.source_id,
            attribution=src.attribution,
            where=src.where,
            refresh=refresh,
        )
    elif src.kind == "tiger":
        manifest, downloaded = http.download(
            client,
            src.url,
            src.path,
            source_id=src.source_id,
            attribution=src.attribution,
            vintage=src.vintage,
            refresh=refresh,
        )
    else:  # acs: stream the nationwide file, keep only this region's block groups
        prefixes = acs.block_group_prefixes(cfg.state_fips, cfg.county_fips)
        manifest, downloaded = http.download(
            client,
            src.url,
            src.path,
            source_id=src.source_id,
            attribution=src.attribution,
            vintage=src.vintage,
            line_filter=acs.make_line_filter(prefixes),
            filter_description="header + lines starting with "
            + ", ".join(p.decode() for p in prefixes),
            refresh=refresh,
        )
    return FetchResult(source=src, manifest=manifest, downloaded=downloaded)


def run_fetch(
    cfg: RegionConfig,
    paths: RegionPaths,
    *,
    refresh: bool = False,
    client: httpx.Client | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> list[FetchResult]:
    own_client = client is None
    client = client or http.make_client()
    results = []
    try:
        for src in all_sources(cfg, paths):
            progress(f"→ {src.source_id}: {src.url}")
            result = fetch_one(client, cfg, src, refresh)
            state = "downloaded" if result.downloaded else "cached"
            mb = result.manifest.stored_bytes / 1_048_576
            progress(f"  {state}: {result.path.name} ({mb:.1f} MB)")
            results.append(result)
    finally:
        if own_client:
            client.close()
    return results
