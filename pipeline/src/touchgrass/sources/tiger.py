"""US Census Bureau TIGER/Line shapefiles.

URLs are derived from (vintage, state FIPS, layer), so no region configures them by hand.
Files are state-based zip archives in NAD83 (EPSG:4269). The 2020 block file
(``tabblock20``) also carries the 2020 Census counts POP20 and HOUSING20.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import pyogrio

TIGER_BASE = "https://www2.census.gov/geo/tiger"
TIGER_CRS = 4269  # NAD83 geographic
TIGER_ATTRIBUTION = "U.S. Census Bureau, TIGER/Line Shapefiles"

TigerLayer = Literal["tabblock20", "bg"]
_LAYER_DIRS: dict[str, str] = {"tabblock20": "TABBLOCK20", "bg": "BG"}
# Field-name suffix used by each layer for its county code (2020 vintage).
_COUNTY_FIELD: dict[str, str] = {"tabblock20": "COUNTYFP20", "bg": "COUNTYFP"}


def tiger_filename(vintage: int, state_fips: str, layer: TigerLayer) -> str:
    return f"tl_{vintage}_{state_fips}_{layer}.zip"


def tiger_url(vintage: int, state_fips: str, layer: TigerLayer) -> str:
    return (
        f"{TIGER_BASE}/TIGER{vintage}/{_LAYER_DIRS[layer]}/"
        f"{tiger_filename(vintage, state_fips, layer)}"
    )


def read_counties(path: Path, layer: TigerLayer, county_fips: list[str]) -> gpd.GeoDataFrame:
    """Read only the given counties. GDAL applies the filter while reading, so the full
    state is never held in memory."""
    field = _COUNTY_FIELD[layer]
    in_list = ", ".join(f"'{c}'" for c in county_fips)  # FIPS are validated as 3 digits
    gdf = pyogrio.read_dataframe(path, where=f"{field} IN ({in_list})")
    if gdf.crs is None or gdf.crs.to_epsg() != TIGER_CRS:
        raise ValueError(f"{path.name}: expected EPSG:{TIGER_CRS}, got {gdf.crs}")
    return gdf


def read_block_population(path: Path, county_fips: list[str]) -> pd.DataFrame:
    """GEOID20 and POP20 of every 2020 block in the given counties, without geometry.

    Used for the *full* 2020 population of block groups that straddle the study boundary
    (ACS allocation denominators, DECISIONS.md D-022 Q5). Attribute-only: about 1 s.
    """
    in_list = ", ".join(f"'{c}'" for c in county_fips)
    df = pyogrio.read_dataframe(
        path,
        columns=["GEOID20", "COUNTYFP20", "POP20"],  # the WHERE field must be read too
        read_geometry=False,
        where=f"COUNTYFP20 IN ({in_list})",
    )
    return pd.DataFrame(
        {"geoid20": df["GEOID20"].astype(str), "pop20": df["POP20"].astype("int64")}
    )
