"""Area metrics on hand-computed fixtures: weighting, overlaps, medians, priority, ranking."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from touchgrass.core.aggregate import (
    CITY_ID,
    ranked,
    summarize_areas,
    weighted_median,
    with_city,
)

#  block  pop   distance   land (m²)   areas
#  b1      100     300 m    10,000     A
#  b2      300     900 m    10,000     A, B   (shared: counts in both, once in the city)
#  b3        2     200 m   980,000     B      (tiny population, huge land)
#  b4       50       inf    10,000     B      (no route: beyond every threshold)
BLOCKS = pd.DataFrame(
    {
        "geoid20": ["b1", "b2", "b3", "b4"],
        "pop20": [100, 300, 2, 50],
        "distance_m": [300.0, 900.0, 200.0, np.inf],
        "land_m2": [10_000.0, 10_000.0, 980_000.0, 10_000.0],
        "reach_ha": [5.0, 0.0, 2.0, 0.0],
    }
)
MEMBERS = pd.DataFrame(
    {
        "area_id": ["A", "A", "B", "B", "B"],
        "area_type": "hood",
        "geoid20": ["b1", "b2", "b2", "b3", "b4"],
    }
)
THRESHOLDS = {5: 400.0, 10: 800.0, 15: 1200.0}


def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics, shares = summarize_areas(with_city(MEMBERS, BLOCKS), BLOCKS, THRESHOLDS, 800.0)
    return metrics.set_index("area_id"), shares.set_index(["area_id", "threshold_min"])


def test_population_weighted_shares_by_hand() -> None:
    m, s = run()
    # A: 100 of 400 residents within 10 min; B: 2 of 352.
    assert s.loc[("A", 10), "pct_within"] == pytest.approx(100 / 400)
    assert s.loc[("B", 10), "pct_within"] == pytest.approx(2 / 352)
    assert s.loc[("B", 15), "pct_within"] == pytest.approx(302 / 352)  # b2 at 900 m
    assert m.loc["A", "underserved_pop"] == 300 and m.loc["B", "underserved_pop"] == 350


def test_shared_block_counts_in_each_area_but_once_in_the_city() -> None:
    m, _ = run()
    assert m.loc["A", "pop_total"] + m.loc["B", "pop_total"] == 752  # b2 twice
    assert m.loc[CITY_ID, "pop_total"] == 452  # each block once
    assert m.loc[CITY_ID, "underserved_pop"] == 350  # b2 + b4, once each


def test_unreachable_is_beyond_every_threshold() -> None:
    m, s = run()
    assert m.loc["B", "unreachable_pop"] == 50
    assert s.loc[("B", 15), "pop_within"] == 302  # b4 never counted within


def test_block_and_land_shares_answer_different_questions() -> None:
    m, _ = run()
    # B, within 10 min: residents 2/352 ≈ 0.6%, blocks 1/3, land 980k/1M = 98%.
    assert m.loc["B", "block_share_within"] == pytest.approx(1 / 3)
    assert m.loc["B", "land_share_within"] == pytest.approx(0.98)


def test_weighted_median_is_the_typical_residents_walk() -> None:
    m, _ = run()
    # A: 100 people at 300 m, 300 at 900 m → half the residents walk 900 m or more.
    assert m.loc["A", "median_walk_m"] == 900.0
    # B: 2 + 300 of 352 by 900 m (>= half) → 900; the unweighted block median would be 900 too,
    # but a plain median of block distances in A would be 600 (the mean of 300 and 900).
    assert m.loc["B", "median_walk_m"] == 900.0
    assert weighted_median(np.array([1.0, 2.0, 3.0]), np.array([1, 1, 10])) == 3.0
    assert weighted_median(np.array([np.inf, 5.0]), np.array([3, 1])) == np.inf
    assert np.isnan(weighted_median(np.array([]), np.array([])))


def test_median_is_nan_when_most_residents_have_no_route() -> None:
    blocks = BLOCKS.assign(distance_m=[np.inf, np.inf, 200.0, np.inf])
    metrics, _ = summarize_areas(with_city(MEMBERS, blocks), blocks, THRESHOLDS, 800.0)
    assert np.isnan(metrics.set_index("area_id").loc["A", "median_walk_m"])


def test_priority_is_underserved_per_km2_of_populated_land() -> None:
    m, _ = run()
    assert m.loc["A", "priority_score"] == pytest.approx(300 / 0.02)  # 2 blocks of 0.01 km²
    assert m.loc["B", "priority_score"] == pytest.approx(350 / 1.0)


def test_reachable_park_area_is_population_weighted() -> None:
    m, _ = run()
    assert m.loc["A", "reachable_park_ha_mean"] == pytest.approx((100 * 5) / 400)
    assert m.loc["A", "reachable_park_ha_median"] == 0.0


def test_ranking_excludes_the_city_and_small_areas() -> None:
    m, _ = run()
    order = ranked(m.reset_index(), "underserved_share", ascending=False)
    assert order["area_id"].tolist() == ["B", "A"]
    assert ranked(m.reset_index(), "underserved_share", ascending=False, min_pop=400)[
        "area_id"
    ].tolist() == ["A"]


def test_straight_line_shares_sit_beside_walking_shares() -> None:
    # b1 300 m walk / 250 m straight; b2 900 / 700; b3 200 / 100; b4 no route / 500.
    blocks = BLOCKS.assign(straight_m=[250.0, 700.0, 100.0, 500.0])
    _, shares = summarize_areas(with_city(MEMBERS, blocks), blocks, THRESHOLDS, 800.0)
    s = shares.set_index(["area_id", "threshold_min"])
    # A within 10 min: by walk 100 of 400; by straight line 400 of 400 (b2 is 700 m away).
    assert s.loc[("A", 10), "pct_within"] == pytest.approx(0.25)
    assert s.loc[("A", 10), "pct_within_straight"] == pytest.approx(1.0)
    # B: b4 has no walking route but is 500 m away in a straight line.
    assert s.loc[("B", 10), "pop_within_straight"] == 2 + 300 + 50
    assert (s["pct_within_straight"] >= s["pct_within"]).all()


def test_no_straight_column_means_no_straight_shares() -> None:
    _, shares = summarize_areas(with_city(MEMBERS, BLOCKS), BLOCKS, THRESHOLDS, 800.0)
    assert "pct_within_straight" not in shares.columns
