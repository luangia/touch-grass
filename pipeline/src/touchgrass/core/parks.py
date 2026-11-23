"""Decide which OSM areas are public green space, then merge overlapping ones into units.

Classification is configuration (``ParksConfig``), not code: tag meanings vary between
mappers and cities, so rules must be explicit, auditable and testable. Every candidate
keeps its decision and reason.

Merging: one place is often several OSM objects (a nature reserve and a protected area
drawn on the same land, smaller parks inside a large one). Candidates whose *interiors*
overlap are merged into one unit; parks that merely touch stay separate destinations.
This is a graph problem: candidates are nodes, overlaps are edges, units are connected
components (found with union-find).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from touchgrass.config.models import ParksConfig, TagRule
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.core.geometry import area_m2, to_multipolygon

INCLUDED = "included"
TOO_SMALL = "too_small"
ACCESS = "access_restricted"
NO_RULE = "no_matching_rule"


@dataclass(frozen=True, slots=True)
class Decision:
    included: bool
    park_class: str | None  # 'park' | 'natural_area' when included
    reason: str
    rule_tag: str  # the key=value that decided


def _first_match(tags: dict[str, str], rule: TagRule) -> str | None:
    for key, values in rule.items():
        if tags.get(key) in values:
            return f"{key}={tags[key]}"
    return None


def classify(tags: dict[str, str], rules: ParksConfig) -> Decision:
    """Apply the rules in ParksConfig's documented order (size is checked separately)."""
    included_by = _first_match(tags, rules.include_tags)
    if included_by is not None:
        if tags.get("access") in rules.exclude_access:
            return Decision(False, None, ACCESS, f"access={tags['access']}")
        natural = _first_match(tags, rules.natural_area_tags) is not None
        return Decision(True, "natural_area" if natural else "park", INCLUDED, included_by)
    for reason, rule in rules.exclude_tags.items():
        matched = _first_match(tags, rule)
        if matched is not None:
            return Decision(False, None, reason, matched)
    return Decision(False, None, NO_RULE, "")


def classify_candidates(
    candidates: gpd.GeoDataFrame, rules: ParksConfig, analysis_epsg: int
) -> gpd.GeoDataFrame:
    """Add decision columns (and area) to every candidate; nothing is dropped."""
    decisions = [classify(json.loads(t), rules) for t in candidates["tags"]]
    out = candidates.copy()
    out["area_m2"] = area_m2(out, analysis_epsg).values if len(out) else []
    out["included"] = [d.included for d in decisions]
    out["park_class"] = [d.park_class for d in decisions]
    out["reason"] = [d.reason for d in decisions]
    out["rule_tag"] = [d.rule_tag for d in decisions]
    small = out["included"] & (out["area_m2"] < rules.min_area_m2)
    out.loc[small, ["included", "reason"]] = [False, TOO_SMALL]
    out.loc[small, "park_class"] = None
    return out


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]  # path halving
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def overlap_components(geoms_projected: np.ndarray, min_overlap_m2: float = 1.0) -> list[int]:
    """Component label per geometry: geometries whose interiors overlap by more than
    ``min_overlap_m2`` share a label (transitively). Geometries must be in a metric CRS."""
    n = len(geoms_projected)
    uf = _UnionFind(n)
    if n:
        tree = shapely.STRtree(geoms_projected)
        left, right = tree.query(geoms_projected, predicate="intersects")  # filter + refine
        keep = left < right
        left, right = left[keep], right[keep]
        overlap = shapely.area(shapely.intersection(geoms_projected[left], geoms_projected[right]))
        for a, b in zip(
            left[overlap > min_overlap_m2], right[overlap > min_overlap_m2], strict=True
        ):
            uf.union(int(a), int(b))
    return [uf.find(i) for i in range(n)]


def merge_into_units(included: gpd.GeoDataFrame, analysis_epsg: int) -> gpd.GeoDataFrame:
    """One row per green-space unit (see module docstring)."""
    columns = [
        "park_id", "name", "park_class", "area_m2", "n_members",
        "member_ids", "member_names", "geometry",
    ]  # fmt: skip
    if included.empty:
        return gpd.GeoDataFrame({c: [] for c in columns}, geometry="geometry", crs=STORAGE_EPSG)

    df = included.reset_index(drop=True)
    labels = overlap_components(df.to_crs(analysis_epsg).geometry.values)
    df["unit"] = labels
    df["member_id"] = df["osm_type"].str[0] + df["osm_id"].astype(str)

    rows = []
    for _, group in df.groupby("unit", sort=False):
        group = group.sort_values(["area_m2", "member_id"], ascending=[False, True])
        named = group[group["name"].notna()]
        geom = to_multipolygon(shapely.union_all(group.geometry.values))
        rows.append(
            {
                "park_id": f"osm:{group['member_id'].iloc[0]}",
                "name": named["name"].iloc[0] if len(named) else None,
                "park_class": (
                    "natural_area" if (group["park_class"] == "natural_area").any() else "park"
                ),
                "n_members": len(group),
                "member_ids": ";".join(sorted(group["member_id"])),
                "member_names": "; ".join(sorted(set(named["name"]))),
                "geometry": geom,
            }
        )
    units = gpd.GeoDataFrame(rows, geometry="geometry", crs=STORAGE_EPSG)
    units["area_m2"] = area_m2(units, analysis_epsg).values
    return units[columns].sort_values("park_id", ignore_index=True)


def subtract_excluded(
    units: gpd.GeoDataFrame,
    holes: gpd.GeoDataFrame,
    analysis_epsg: int,
    min_area_m2: float,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    """Cut excluded areas (e.g. golf courses, a zoo) out of the units that enclose them.

    A park outline drawn around a fee-charging facility would otherwise count that land as
    park, and its fence as a park edge. Units left empty or smaller than ``min_area_m2``
    are dropped. Returns (units, stats); units gain a ``subtracted_m2`` column.
    """
    out = units.copy()
    before = out["area_m2"].copy()
    if not out.empty and not holes.empty:
        cut = shapely.union_all(holes.geometry.values)
        shapely.prepare(cut)
        touched = out.geometry.intersects(cut)
        out.loc[touched, "geometry"] = [
            to_multipolygon(g.difference(cut)) for g in out.loc[touched, "geometry"]
        ]
    emptied = out.geometry.isna()
    out = gpd.GeoDataFrame(out[~emptied], geometry="geometry", crs=STORAGE_EPSG)
    if not out.empty:
        out["area_m2"] = area_m2(out, analysis_epsg).values
    removed = (before.loc[out.index] - out["area_m2"]).clip(lower=0)
    out["subtracted_m2"] = removed.where(removed > 1.0, 0.0)  # ignore edge slivers
    small = out["area_m2"] < min_area_m2

    stats: dict[str, object] = {
        "units_cut": int((out["subtracted_m2"] > 0).sum()),
        "subtracted_km2": round(float(out["subtracted_m2"].sum()) / 1e6, 4),
        "units_dropped": sorted(units.loc[emptied, "park_id"]) + sorted(out.loc[small, "park_id"]),
    }
    return out[~small].reset_index(drop=True), stats


def missing_sentinels(units: gpd.GeoDataFrame, sentinels: list[str]) -> list[str]:
    """Sentinel names not found among any unit's member names (case-insensitive)."""
    names = {
        n.strip().lower()
        for joined in units.get("member_names", pd.Series(dtype=str)).dropna()
        for n in joined.split(";")
        if n.strip()
    }
    return [s for s in sentinels if s.strip().lower() not in names]
