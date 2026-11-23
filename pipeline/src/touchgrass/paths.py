"""Where a region's data lives on disk.

    <data_dir>/raw/<region_id>/<source_id>/...     immutable downloads + manifests
    <data_dir>/processed/<region_id>/...           pipeline outputs (GeoParquet, reports)

``data_dir`` resolution: explicit argument → ``TOUCHGRASS_DATA_DIR`` → ``<repo>/data``
(the sibling of the config directory). The data directory is git-ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_ENV = "TOUCHGRASS_DATA_DIR"


@dataclass(frozen=True, slots=True)
class RegionPaths:
    region_id: str
    data_dir: Path

    @property
    def raw(self) -> Path:
        return self.data_dir / "raw" / self.region_id

    @property
    def processed(self) -> Path:
        return self.data_dir / "processed" / self.region_id

    def raw_source(self, source_id: str) -> Path:
        return self.raw / source_id


def resolve_data_dir(config_dir: Path, data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return data_dir.expanduser().resolve()
    env = os.environ.get(DATA_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return (config_dir.parent / "data").resolve()
