"""Allocate ACS block-group estimates to areas, with margins of error (DECISIONS.md D-022).

Blocks nest in block groups (the first 12 GEOID digits), so an area's share of a block
group is the share of that block group's 2020 population living in the area's blocks:

    f(A, g) = Σ_{b ∈ A ∩ g} pop20_b  /  Σ_{b ∈ g} pop20_b      (ALL blocks of g)
    X(A)    = Σ_g f(A, g) · x_g

The denominator must cover the *whole* block group, including blocks outside the study
area. Otherwise a block group straddling the boundary hands its whole estimate to its
in-boundary part.

MOE propagation follows the Census *ACS Handbook* (2020), ch. 8 (approximations):

    sum          MOE = √Σ MOEᵢ²; components whose estimate is 0 contribute only the
                 single largest of their MOEs (otherwise the many "0 ± 13" cells inflate it)
    f · x        MOE = f · MOE(x)              (f treated as known exactly)
    p = X / Y    MOE = √(MOE_X² - p²·MOE_Y²) / Y; if the radicand is negative, the ratio
                 formula √(MOE_X² + p²·MOE_Y²) / Y
    1 - p        same MOE as p

Reliability: CV = (MOE / 1.645) / estimate (1.645 = z for the 90% level ACS uses).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from touchgrass.config.models import AcsIndicator

Z90 = 1.645
HIGH, MEDIUM, LOW = "high", "medium", "low"


def block_group_of(geoid20: pd.Series) -> pd.Series:
    return geoid20.str.slice(0, 12)


def allocation_weights(
    members: pd.DataFrame, block_pop: pd.Series, bg_total: pd.Series
) -> pd.DataFrame:
    """f(A, g) for every (area, block group) pair with population in the area.

    ``members``: area_id, geoid20 (many-to-many is fine). ``block_pop``: pop20 by geoid20.
    ``bg_total``: 2020 population of each block group over ALL its blocks.
    Returns area_id, bg, pop_in, pop_bg, f. Block groups with no 2020 population get f = 0.
    """
    m = members[["area_id", "geoid20"]].copy()
    m["pop20"] = block_pop.reindex(m["geoid20"]).to_numpy()
    if m["pop20"].isna().any():
        missing = m.loc[m["pop20"].isna(), "geoid20"].head(3).tolist()
        raise ValueError(f"members not in the block table, e.g. {missing}")
    m["bg"] = block_group_of(m["geoid20"])
    w = m.groupby(["area_id", "bg"], as_index=False, sort=True)["pop20"].sum()
    w = w.rename(columns={"pop20": "pop_in"})
    w["pop_bg"] = bg_total.reindex(w["bg"]).to_numpy()
    if w["pop_bg"].isna().any():
        missing = w.loc[w["pop_bg"].isna(), "bg"].unique()[:3].tolist()
        raise ValueError(f"block groups without a full 2020 total, e.g. {missing}")
    if (w["pop_in"] > w["pop_bg"]).any():
        raise ValueError("an area holds more of a block group than the block group has")
    w["f"] = np.divide(
        w["pop_in"].to_numpy(dtype=float),
        w["pop_bg"].to_numpy(dtype=float),
        out=np.zeros(len(w)),
        where=w["pop_bg"].to_numpy() > 0,
    )
    return w


def sum_moe(estimates: np.ndarray, moes: np.ndarray) -> float:
    """MOE of a sum, counting zero-estimate components once (their largest MOE)."""
    est = np.asarray(estimates, dtype=float)
    moe = np.asarray(moes, dtype=float)
    zero = est == 0
    total = float(np.sum(moe[~zero] ** 2))
    if zero.any():
        total += float(np.max(moe[zero])) ** 2
    return float(np.sqrt(total))


def proportion_moe(p: float, moe_x: float, moe_y: float, y: float) -> float:
    """MOE of p = X / Y where X is a subset of Y (falls back to the ratio formula)."""
    if y <= 0:
        return float("nan")
    radicand = moe_x**2 - p**2 * moe_y**2
    if radicand < 0:
        radicand = moe_x**2 + p**2 * moe_y**2
    return float(np.sqrt(radicand) / y)


def coefficient_of_variation(estimate: float, moe: float) -> float:
    return float((moe / Z90) / estimate) if estimate > 0 else float("nan")


def reliability(cv: float, high_max: float, medium_max: float) -> str:
    """high < high_max <= medium <= medium_max < low; an undefined CV is low."""
    if np.isnan(cv):
        return LOW
    if cv < high_max:
        return HIGH
    return MEDIUM if cv <= medium_max else LOW


def _column(var: str, part: str) -> str:
    table, number = var.lower().split("_")
    return f"{table}_{part}{number}"


def _allocated(weights: pd.DataFrame, bgs: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    """Per area: allocated estimate and MOE of the sum of ``variables`` (cells: bg x var).

    Cells with f = 0 are left out. If any remaining cell is missing (an ACS value the
    Census suppressed), the area's estimate and MOE are NaN: undefined, not zero.
    """
    used = weights[weights["f"] > 0]
    cells = []
    for var in variables:
        cells.append(
            pd.DataFrame(
                {
                    "area_id": used["area_id"].to_numpy(),
                    "f": used["f"].to_numpy(),
                    "e": bgs[_column(var, "e")].reindex(used["bg"]).to_numpy(dtype=float),
                    "m": bgs[_column(var, "m")].reindex(used["bg"]).to_numpy(dtype=float),
                }
            )
        )
    allc = pd.concat(cells, ignore_index=True)
    rows = []
    for area_id in sorted(weights["area_id"].unique()):
        g = allc[allc["area_id"] == area_id]
        if g[["e", "m"]].isna().any().any():
            rows.append({"area_id": area_id, "estimate": np.nan, "moe": np.nan})
            continue
        # The zero rule looks at the block group's own estimate: an f·0 cell is still a "0".
        rows.append(
            {
                "area_id": area_id,
                "estimate": float((g["f"] * g["e"]).sum()),
                "moe": sum_moe(g["e"].to_numpy(), (g["f"] * g["m"]).to_numpy()) if len(g) else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=["area_id", "estimate", "moe"]).set_index("area_id")


def area_indicators(
    weights: pd.DataFrame,
    block_groups: pd.DataFrame,
    indicators: list[AcsIndicator],
    cv_high_max: float,
    cv_medium_max: float,
) -> pd.DataFrame:
    """One row per (area, indicator): estimate, moe, cv, reliability, and the parts.

    ``block_groups`` is indexed by block-group GEOID with ``<table>_e<nnn>`` / ``_m<nnn>``
    columns. For a share, ``estimate`` is a fraction in [0, 1] and ``numerator`` is the
    allocated count it describes (for a complement: denominator - numerator variables).
    """
    rows = []
    for ind in indicators:
        num = _allocated(weights, block_groups, ind.numerator)
        if ind.kind == "count":
            for area_id, r in num.iterrows():
                cv = coefficient_of_variation(r["estimate"], r["moe"])
                rows.append(
                    {
                        "area_id": area_id,
                        "indicator": ind.id,
                        "kind": ind.kind,
                        "estimate": r["estimate"],
                        "moe": r["moe"],
                        "cv": cv,
                        "reliability": reliability(cv, cv_high_max, cv_medium_max),
                        "numerator": r["estimate"],
                        "numerator_moe": r["moe"],
                        "denominator": np.nan,
                        "denominator_moe": np.nan,
                    }
                )
            continue
        assert ind.denominator is not None
        den = _allocated(weights, block_groups, [ind.denominator])
        for area_id in num.index:
            x, mx = num.loc[area_id, "estimate"], num.loc[area_id, "moe"]
            y, my = den.loc[area_id, "estimate"], den.loc[area_id, "moe"]
            p = x / y if y > 0 else float("nan")  # NaN y (suppressed) is not > 0 either
            moe = proportion_moe(p, mx, my, y) if y > 0 and not np.isnan(x) else float("nan")
            if ind.complement:
                share, count = 1 - p, y - x
                count_moe = float(np.sqrt(mx**2 + my**2))  # a difference: √(MOE_Y² + MOE_X²)
            else:
                share, count, count_moe = p, x, mx
            cv = coefficient_of_variation(share, moe) if not np.isnan(share) else float("nan")
            rows.append(
                {
                    "area_id": area_id,
                    "indicator": ind.id,
                    "kind": ind.kind,
                    "estimate": share,
                    "moe": moe,
                    "cv": cv,
                    "reliability": reliability(cv, cv_high_max, cv_medium_max),
                    "numerator": count,
                    "numerator_moe": count_moe,
                    "denominator": y,
                    "denominator_moe": my,
                }
            )
    out = pd.DataFrame(rows)
    return out.sort_values(["area_id", "indicator"], ignore_index=True) if len(out) else out
