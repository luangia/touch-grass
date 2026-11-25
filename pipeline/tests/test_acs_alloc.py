"""ACS allocation and margin-of-error arithmetic (Census ACS Handbook 2020, ch. 8)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from touchgrass.config.models import AcsIndicator
from touchgrass.core.acs_alloc import (
    HIGH,
    LOW,
    MEDIUM,
    allocation_weights,
    area_indicators,
    coefficient_of_variation,
    proportion_moe,
    reliability,
    sum_moe,
)


def test_sum_moe_root_sum_of_squares() -> None:
    # Two components 100 ± 30 and 200 ± 40: √(30² + 40²) = 50.
    assert sum_moe(np.array([100, 200]), np.array([30, 40])) == pytest.approx(50)


def test_sum_moe_counts_zero_estimates_once() -> None:
    # Three "0 ± 13" cells and one 50 ± 20: only the largest zero MOE is kept.
    # √(20² + 13²) ≈ 23.85, not √(20² + 3·13²) ≈ 29.9.
    got = sum_moe(np.array([0, 0, 0, 50]), np.array([13, 13, 11, 20]))
    assert got == pytest.approx(math.sqrt(20**2 + 13**2))


def test_proportion_moe_textbook_and_fallback() -> None:
    # X = 50 ± 10 of Y = 200 ± 20, p = 0.25: √(10² - 0.25²·20²)/200 = √75/200.
    assert proportion_moe(0.25, 10, 20, 200) == pytest.approx(math.sqrt(75) / 200)
    # X = 90 ± 5 of Y = 100 ± 30, p = 0.9: 25 - 0.81·900 < 0 → ratio formula.
    assert proportion_moe(0.9, 5, 30, 100) == pytest.approx(math.sqrt(25 + 0.81 * 900) / 100)
    assert math.isnan(proportion_moe(0.0, 1, 1, 0))


def test_cv_and_reliability_labels() -> None:
    # 9% ± 7%: CV = (0.07 / 1.645) / 0.09 ≈ 47% → low (lesson §12 question 3).
    cv = coefficient_of_variation(0.09, 0.07)
    assert cv == pytest.approx(0.4728, abs=1e-4) and reliability(cv, 0.15, 0.30) == LOW
    assert reliability(0.149, 0.15, 0.30) == HIGH
    assert reliability(0.15, 0.15, 0.30) == MEDIUM  # boundary belongs to medium
    assert reliability(0.30, 0.15, 0.30) == MEDIUM
    assert reliability(float("nan"), 0.15, 0.30) == LOW
    assert math.isnan(coefficient_of_variation(0, 5))


# Block group G1 lies wholly in the city (blocks a, b). G2 straddles the line: block c is in
# the city (40 people), block d is outside (60 people) and is only in the raw TIGER total.
BLOCK_POP = pd.Series({"990010001001001": 10, "990010001001002": 30, "990010001002001": 40})
BG_TOTAL = pd.Series({"990010001001": 40, "990010001002": 100})
MEMBERS = pd.DataFrame(
    {
        "area_id": ["N", "N", "S", "city", "city", "city"],
        "geoid20": [
            "990010001001001",
            "990010001001002",
            "990010001002001",
            "990010001001001",
            "990010001001002",
            "990010001002001",
        ],
    }
)


def test_straddling_block_group_allocates_only_its_in_city_share() -> None:
    w = allocation_weights(MEMBERS, BLOCK_POP, BG_TOTAL).set_index(["area_id", "bg"])["f"]
    assert w[("N", "990010001001")] == 1.0
    assert w[("S", "990010001002")] == pytest.approx(0.4)  # 40 of 100, not 40 of 40
    assert w[("city", "990010001002")] == pytest.approx(0.4)


def test_allocation_rejects_missing_totals() -> None:
    with pytest.raises(ValueError, match="full 2020 total"):
        allocation_weights(MEMBERS, BLOCK_POP, BG_TOTAL.drop("990010001002"))
    with pytest.raises(ValueError, match="more of a block group"):
        allocation_weights(MEMBERS, BLOCK_POP, BG_TOTAL.replace(100, 10))


BGS = pd.DataFrame(
    {
        # households, of which no vehicle (owner + renter), white non-Hispanic, total pop
        "b25044_e001": [100.0, 500.0],
        "b25044_m001": [10.0, 50.0],
        "b25044_e003": [0.0, 20.0],
        "b25044_m003": [13.0, 10.0],
        "b25044_e010": [10.0, 30.0],
        "b25044_m010": [5.0, 15.0],
        "b01003_e001": [400.0, 1000.0],
        "b01003_m001": [40.0, 100.0],
    },
    index=["990010001001", "990010001002"],
)
ZERO_VEHICLE = AcsIndicator(
    id="zero_vehicle",
    label="No vehicle",
    kind="share",
    numerator=["B25044_003", "B25044_010"],
    denominator="B25044_001",
)
POP = AcsIndicator(id="pop", label="Population", kind="count", numerator=["B01003_001"])


def indicators(*inds: AcsIndicator, bgs: pd.DataFrame = BGS) -> pd.DataFrame:
    w = allocation_weights(MEMBERS, BLOCK_POP, BG_TOTAL)
    return area_indicators(w, bgs, list(inds), 0.15, 0.30).set_index(["area_id", "indicator"])


def test_count_allocation_and_moe() -> None:
    out = indicators(POP)
    assert out.loc[("S", "pop"), "estimate"] == pytest.approx(0.4 * 1000)
    assert out.loc[("S", "pop"), "moe"] == pytest.approx(0.4 * 100)
    city = out.loc[("city", "pop")]
    assert city["estimate"] == pytest.approx(400 + 400)
    assert city["moe"] == pytest.approx(math.sqrt(40**2 + 40**2))


def test_share_with_zero_rule_and_proportion_moe() -> None:
    r = indicators(ZERO_VEHICLE).loc[("N", "zero_vehicle")]
    # N = all of G1: X = 0 + 10, MOE_X = √(5² + 13²) (one zero cell), Y = 100 ± 10.
    mx, p = math.sqrt(5**2 + 13**2), 0.1
    assert r["estimate"] == pytest.approx(p)
    assert r["moe"] == pytest.approx(math.sqrt(mx**2 - p**2 * 10**2) / 100)
    assert r["numerator"] == pytest.approx(10) and r["denominator"] == pytest.approx(100)


def test_complement_share_keeps_the_moe() -> None:
    white = AcsIndicator(
        id="white", label="W", kind="share", numerator=["B25044_010"], denominator="B25044_001"
    )
    other = white.model_copy(update={"id": "not_white", "complement": True})
    out = indicators(white, other)
    a, b = out.loc[("S", "white")], out.loc[("S", "not_white")]
    assert a["estimate"] + b["estimate"] == pytest.approx(1)
    assert a["moe"] == pytest.approx(b["moe"])
    assert b["numerator"] == pytest.approx(b["denominator"] - a["numerator"])


def test_suppressed_values_make_the_estimate_undefined_not_zero() -> None:
    bgs = BGS.copy()
    bgs.loc["990010001002", "b01003_e001"] = np.nan
    out = indicators(POP, bgs=bgs)
    assert out.loc[("N", "pop"), "estimate"] == pytest.approx(400)  # G1 only: unaffected
    assert math.isnan(out.loc[("S", "pop"), "estimate"])
    assert out.loc[("S", "pop"), "reliability"] == LOW


def test_compare_sides_allocates_each_side_like_an_area() -> None:
    from touchgrass.core.acs_alloc import compare_sides

    # Block group ...001 (both blocks) is "within"; block ...002001 of the straddler is "beyond".
    sides = pd.DataFrame(
        {
            "side": ["within", "within", "beyond"],
            "geoid20": ["990010001001001", "990010001001002", "990010001002001"],
        }
    )
    out = compare_sides(sides, BLOCK_POP, BG_TOTAL, BGS, [ZERO_VEHICLE, POP], 0.15, 0.30)
    assert [c["indicator"] for c in out] == ["zero_vehicle"]  # counts aren't compared
    c = out[0]
    assert c["within"]["residents"] == 40 and c["beyond"]["residents"] == 40
    assert c["within"]["estimate"] == pytest.approx(10 / 100)  # all of G1
    assert c["beyond"]["estimate"] == pytest.approx((0.4 * 50) / (0.4 * 500))  # 40% of G2
    assert c["difference"] == pytest.approx(0.0)
    assert c["clear"] is False  # a zero difference is never "clear"
    assert c["difference_moe"] == pytest.approx(
        math.sqrt(c["within"]["moe"] ** 2 + c["beyond"]["moe"] ** 2)
    )
