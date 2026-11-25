"""Block distances → area metrics, weighted by population (DECISIONS.md D-007, D-011, D-022).

Input is one row per (group, block): a block appears once per area it belongs to (shared
blocks count in each overlapping area) and exactly once in the ``city`` group, so city
totals come from blocks, never from adding areas up.

    pct_within(A, t) = Σ_{b∈A} pop_b · [d_b ≤ t]  /  Σ_{b∈A} pop_b
    median walk      = population-weighted median of d_b (the typical *resident's* walk)
    priority(A)      = underserved residents / km² of A's populated-block land (D-011 amend.)

Unreachable blocks have d = inf: beyond every threshold (D-021 Q6).
The block-share and land-share columns answer *different* questions on purpose (share of
blocks, share of land); they exist to show why population weighting matters.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

CITY_ID = "city"
CITY_TYPE = "city"


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """Smallest value whose cumulative weight reaches half the total (inf allowed)."""
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if len(v) == 0 or w.sum() <= 0:
        return float("nan")
    order = np.argsort(v, kind="stable")
    cum = np.cumsum(w[order])
    return float(v[order][np.searchsorted(cum, cum[-1] / 2)])


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    w = np.asarray(weights, dtype=float)
    return float(np.dot(values, w) / w.sum()) if w.sum() > 0 else float("nan")


def with_city(membership: pd.DataFrame, blocks: pd.DataFrame) -> pd.DataFrame:
    """Membership rows (area_id, area_type, geoid20) plus one ``city`` row per block."""
    city = pd.DataFrame({"area_id": CITY_ID, "area_type": CITY_TYPE, "geoid20": blocks["geoid20"]})
    return pd.concat([membership[["area_id", "area_type", "geoid20"]], city], ignore_index=True)


def summarize_areas(
    groups: pd.DataFrame,
    blocks: pd.DataFrame,
    thresholds_m: dict[int, float],
    underserved_m: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-area metrics and per-(area, threshold) shares.

    ``groups``: area_id, area_type, geoid20 (from :func:`with_city`).
    ``blocks``: one row per populated block: geoid20, pop20, distance_m (inf = unreachable),
    land_m2, reach_ha (park area reachable within the underserved distance), and optionally
    straight_m (straight-line distance to the nearest park polygon: a floor on the walk), which
    adds pop_within_straight / pct_within_straight to the shares (D-025 Q3).
    """
    has_straight = "straight_m" in blocks.columns
    b = blocks.set_index("geoid20")
    g = groups.merge(b, left_on="geoid20", right_index=True, how="inner")
    metrics: list[dict[str, Any]] = []
    shares: list[dict[str, Any]] = []
    for (area_id, area_type), a in g.groupby(["area_id", "area_type"], sort=True):
        pop = a["pop20"].to_numpy(dtype=float)
        d = a["distance_m"].to_numpy(dtype=float)
        land = a["land_m2"].to_numpy(dtype=float)
        total = pop.sum()
        beyond = d > underserved_m
        under = float(pop[beyond].sum())
        median = weighted_median(d, pop)
        land_km2 = land.sum() / 1e6
        metrics.append(
            {
                "area_id": area_id,
                "area_type": area_type,
                "blocks": len(a),
                "pop_total": int(total),
                "populated_land_m2": float(land.sum()),
                "unreachable_pop": int(pop[np.isinf(d)].sum()),
                "underserved_pop": int(under),
                "underserved_share": under / total if total else np.nan,
                "median_walk_m": median if np.isfinite(median) else np.nan,
                "priority_score": under / land_km2 if land_km2 > 0 else np.nan,
                "block_share_within": float((~beyond).mean()) if len(a) else np.nan,
                "land_share_within": float(land[~beyond].sum() / land.sum())
                if land.sum() > 0
                else np.nan,
                "reachable_park_ha_mean": weighted_mean(a["reach_ha"].to_numpy(), pop),
                "reachable_park_ha_median": weighted_median(a["reach_ha"].to_numpy(), pop),
            }
        )
        straight = a["straight_m"].to_numpy(dtype=float) if has_straight else None
        for minutes, dist in sorted(thresholds_m.items()):
            within = float(pop[d <= dist].sum())
            row: dict[str, Any] = {
                "area_id": area_id,
                "threshold_min": minutes,
                "threshold_m": dist,
                "pop_within": int(within),
                "pct_within": within / total if total else np.nan,
            }
            if straight is not None:
                crow = float(pop[straight <= dist].sum())
                row["pop_within_straight"] = int(crow)
                row["pct_within_straight"] = crow / total if total else np.nan
            shares.append(row)
    return pd.DataFrame(metrics), pd.DataFrame(shares)


def ranked(
    metrics: pd.DataFrame, by: str, *, ascending: bool, min_pop: int = 0, n: int | None = None
) -> pd.DataFrame:
    """Areas (never the city row) ordered by ``by``; ties broken by area_id for stability."""
    m = metrics[(metrics["area_type"] != CITY_TYPE) & (metrics["pop_total"] >= min_pop)]
    m = m.dropna(subset=[by]).sort_values([by, "area_id"], ascending=[ascending, True])
    return m.head(n) if n is not None else m
