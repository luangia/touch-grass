"""American Community Survey 5-year estimates from the key-free table-based summary file.

Each table is one nationwide, pipe-delimited file, e.g. ``acsdt5y2024-b19013.dat``:

    GEO_ID|B19013_E001|B19013_M001
    1500000US410510001011|87431|38950

``GEO_ID`` = summary level (``150`` = block group) + ``0000US`` + the 12-digit GEOID.
``_E`` columns are estimates and ``_M`` columns are 90% margins of error.

Annotation sentinels such as ``-666666666`` mean "no value", never a number. They are
converted to nulls here, at the boundary, so no later step can average them by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

ACS_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file"
ACS_LICENSE = ("Public domain (U.S. Government work)", None)
ACS_ATTRIBUTION = "U.S. Census Bureau, American Community Survey 5-Year Estimates"
BLOCK_GROUP_PREFIX = "1500000US"

# Census-documented annotation values used in place of estimates / margins of error.
SENTINELS = frozenset({-999999999, -888888888, -666666666, -555555555, -333333333, -222222222})


def acs_filename(year: int, table: str) -> str:
    return f"acsdt5y{year}-{table.lower()}.dat"


def acs_url(year: int, table: str) -> str:
    return f"{ACS_BASE}/{year}/table-based-SF/data/5YRData/{acs_filename(year, table)}"


def block_group_prefixes(state_fips: str, county_fips: list[str]) -> tuple[bytes, ...]:
    return tuple(f"{BLOCK_GROUP_PREFIX}{state_fips}{c}".encode() for c in county_fips)


def make_line_filter(prefixes: tuple[bytes, ...]) -> Callable[[bytes], bool]:
    def keep(line: bytes) -> bool:
        return line.startswith(prefixes)

    return keep


def read_table(path: Path) -> pd.DataFrame:
    """Parse a (filtered) table file into one row per block group.

    Returns columns ``geoid`` (12-digit str) plus lower-cased value columns as nullable
    floats. Raises if a negative value appears that is not a documented sentinel: counts,
    medians and MOEs in these tables are never legitimately negative.
    """
    raw = pd.read_csv(path, sep="|", dtype=str)
    if "GEO_ID" not in raw.columns:
        raise ValueError(f"{path.name}: missing GEO_ID column")
    raw = raw[raw["GEO_ID"].str.startswith(BLOCK_GROUP_PREFIX)]

    out = pd.DataFrame({"geoid": raw["GEO_ID"].str.slice(len(BLOCK_GROUP_PREFIX))})
    for col in raw.columns.drop("GEO_ID"):
        values = pd.to_numeric(raw[col], errors="raise").astype("Float64")
        bad = values[(values < 0) & ~values.isin(SENTINELS)]
        if not bad.empty:
            raise ValueError(f"{path.name}:{col}: unexpected negative values {bad.unique()[:5]}")
        out[col.lower()] = values.mask(values.isin(SENTINELS))
    if not out["geoid"].str.fullmatch(r"\d{12}").all():
        raise ValueError(f"{path.name}: malformed block-group GEOIDs")
    return out.reset_index(drop=True)
