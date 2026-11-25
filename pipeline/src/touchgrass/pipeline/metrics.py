"""Stage 7: area metrics, rankings and ACS context for the latest analysis run.

Inputs: the latest Stage 6 run (must be fresh), Stage 2 blocks / areas / membership /
block groups, Stage 3 parks, Stage 5 network, and the raw TIGER block file (for the full
2020 population of block groups that straddle the boundary). Outputs go into the run's
own folder, ``runs/<run_id>/`` (DECISIONS.md D-022 Q8):

    area_metrics.parquet       one row per area (+ the ``city`` row computed from blocks)
    area_thresholds.parquet    one row per (area, threshold): residents within, share
    area_context.parquet       one row per (area, ACS indicator): estimate, MOE, CV, label
    block_park_reach.parquet   per block: parks and park area reachable within the
                               underserved walk
    metrics_run.json           parameters, input checksums, code version, summary
    metrics_validation.json    hard / info checks
    metrics_report.md          rankings, the weighting demonstration, caveats (draft UI text)

Rerunning with unchanged inputs writes identical parquet and markdown files.
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from touchgrass.config import RegionConfig
from touchgrass.config.models import MetricsConfig
from touchgrass.core import acs_alloc
from touchgrass.core.aggregate import CITY_ID, CITY_TYPE, ranked, summarize_areas, with_city
from touchgrass.core.routing import parks_within
from touchgrass.paths import RegionPaths
from touchgrass.pipeline import sources as src
from touchgrass.pipeline.analyze import (
    RUN_FILES,
    RUNS_DIR,
    _dump,
    code_version,
    latest_run_id,
    run_freshness_problems,
)
from touchgrass.pipeline.network import NETWORK_FILES, load_network_outputs
from touchgrass.pipeline.process import MissingDataError
from touchgrass.pipeline.validate import Check, _hard, _info, failed
from touchgrass.sources import tiger
from touchgrass.sources.http import Manifest, manifest_path, now_utc, sha256_file

METRICS_FILES = {
    "area_metrics": "area_metrics.parquet",
    "area_thresholds": "area_thresholds.parquet",
    "area_context": "area_context.parquet",
    "block_park_reach": "block_park_reach.parquet",
    "run": "metrics_run.json",
    "validation": "metrics_validation.json",
    "report": "metrics_report.md",
}
# Processed files the metrics read directly (the run's own inputs are checked separately).
INPUT_FILES = {
    "blocks": "blocks.parquet",
    "area_membership": "area_membership.parquet",
    "analysis_areas": "analysis_areas.parquet",
    "block_groups": "block_groups.parquet",
    "study_boundary": "study_boundary.parquet",
    "parks": "parks.parquet",
    **{k: v for k, v in NETWORK_FILES.items() if k != "network_nodes"},
}
TOL = 1e-6


@dataclass
class MetricsResult:
    run_id: str
    metrics: pd.DataFrame
    thresholds: pd.DataFrame
    context: pd.DataFrame
    reach: pd.DataFrame
    record: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    run_dir: Path | None = None


# ---------------------------------------------------------------------------- inputs


def _tiger_blocks(cfg: RegionConfig, paths: RegionPaths) -> tuple[pd.DataFrame, str]:
    raw = src.tiger_source(cfg, paths, "tabblock20")
    if not raw.path.is_file():
        raise MissingDataError(f"{raw.path} not found; run `touchgrass fetch`")
    manifest = Manifest.read(manifest_path(raw.path))
    return tiger.read_block_population(raw.path, cfg.county_fips), manifest.stored_sha256


def _park_area_inside(
    areas: gpd.GeoDataFrame, boundary: Any, parks: gpd.GeoDataFrame, epsg: int
) -> pd.Series:
    """m² of park inside each area polygon (and inside the study boundary, for ``city``)."""
    union = shapely.union_all(np.asarray(parks.to_crs(epsg).geometry.values))
    polys = areas.set_index("area_id").to_crs(epsg).geometry
    out = {a: float(shapely.area(shapely.intersection(g, union))) for a, g in polys.items()}
    city = gpd.GeoSeries([boundary], crs=areas.crs).to_crs(epsg).iloc[0]
    out[CITY_ID] = float(shapely.area(shapely.intersection(city, union)))
    return pd.Series(out, name="park_m2_inside")


def _area_names(cfg: RegionConfig, areas: pd.DataFrame) -> pd.Series:
    names = areas.set_index("area_id")["name"].astype(str)
    return pd.concat([names, pd.Series({CITY_ID: cfg.name})])


# --------------------------------------------------------------------------- driver


def run_metrics(
    cfg: RegionConfig,
    paths: RegionPaths,
    repo: Path,
    *,
    progress: Callable[[str], None] = lambda _m: None,
) -> MetricsResult:
    if cfg.metrics is None or cfg.network is None:
        raise MissingDataError(f"{cfg.region_id}: no 'metrics' section in the region config")
    mcfg = cfg.metrics
    out_dir = paths.processed
    run_id = latest_run_id(out_dir)
    if run_id is None:
        raise MissingDataError("no analysis run; run `touchgrass analyze` first")
    stale = run_freshness_problems(out_dir, run_id)
    if stale:
        raise MissingDataError(f"latest run not usable: {'; '.join(stale)}; rerun `analyze`")
    network = load_network_outputs(out_dir)
    if network is None:
        raise MissingDataError("network outputs missing; run `touchgrass network`")
    t0 = time.perf_counter()
    run_dir = out_dir / RUNS_DIR / run_id
    ba = pd.read_parquet(run_dir / RUN_FILES["block_access"]).drop(columns="geometry")
    blocks = pd.read_parquet(out_dir / "blocks.parquet").drop(columns="geometry")
    membership = pd.read_parquet(out_dir / "area_membership.parquet")
    areas = gpd.read_parquet(out_dir / "analysis_areas.parquet")
    bgs = pd.read_parquet(out_dir / "block_groups.parquet").drop(columns="geometry")
    parks = gpd.read_parquet(out_dir / "parks.parquet")
    sb = gpd.read_parquet(out_dir / "study_boundary.parquet")
    boundary = sb.loc[sb["kind"] == "boundary", "geometry"].iloc[0]
    thresholds = cfg.analysis.threshold_distances_m
    under_m = thresholds[mcfg.underserved_minutes]

    progress(f"parks reachable within {mcfg.underserved_minutes} min ({len(parks)} searches)")
    t_reach = time.perf_counter()
    pairs = parks_within(
        pd.DataFrame(network["network_edges"].drop(columns="geometry")),
        pd.DataFrame(network["park_access_points"].drop(columns="geometry")),
        pd.DataFrame(network["block_origins"].drop(columns="geometry")),
        under_m,
    )
    reach_s = time.perf_counter() - t_reach
    park_ha = parks.set_index("park_id")["area_m2"] / 1e4
    pairs["park_ha"] = pairs["park_id"].map(park_ha).to_numpy()
    per_block = pairs.groupby("geoid20").agg(
        parks_reachable=("park_id", "size"),
        reachable_park_ha=("park_ha", "sum"),
        nearest_reachable_m=("network_m", "min"),
    )
    reach = (
        pd.DataFrame({"run_id": run_id, "geoid20": ba["geoid20"]})
        .join(per_block, on="geoid20")
        .fillna({"parks_reachable": 0, "reachable_park_ha": 0.0})
        .astype({"parks_reachable": "int64"})
        .sort_values("geoid20", ignore_index=True)
    )

    progress("population-weighted area metrics")
    land = blocks.set_index("geoid20")["aland20"]
    block_rows = pd.DataFrame(
        {
            "geoid20": ba["geoid20"],
            "pop20": ba["pop20"],
            "distance_m": ba["network_m"].fillna(np.inf).to_numpy(),
            "straight_m": ba["straight_m"].to_numpy(dtype=float),
            "land_m2": land.reindex(ba["geoid20"]).to_numpy(dtype=float),
            "reach_ha": reach.set_index("geoid20")["reachable_park_ha"]
            .reindex(ba["geoid20"])
            .to_numpy(),
        }
    )
    groups = with_city(membership, blocks)
    metrics, shares = summarize_areas(groups, block_rows, thresholds, under_m)
    inside = _park_area_inside(areas, boundary, parks, cfg.analysis_crs)
    metrics["name"] = metrics["area_id"].map(_area_names(cfg, areas))
    metrics["park_m2_inside"] = metrics["area_id"].map(inside)
    metrics["park_m2_inside_per_resident"] = np.divide(
        metrics["park_m2_inside"],
        metrics["pop_total"],
        out=np.full(len(metrics), np.nan),
        where=metrics["pop_total"].to_numpy() > 0,
    )
    metrics["ranked"] = (metrics["area_type"] != CITY_TYPE) & (
        metrics["pop_total"] >= mcfg.min_residents_for_ranking
    )
    metrics.insert(0, "run_id", run_id)
    shares.insert(0, "run_id", run_id)

    progress("ACS context: allocation by 2020 block population, MOE, reliability")
    tiger_blocks, tiger_sha = _tiger_blocks(cfg, paths)
    bg_total = tiger_blocks.groupby(acs_alloc.block_group_of(tiger_blocks["geoid20"]))[
        "pop20"
    ].sum()
    weights = acs_alloc.allocation_weights(
        groups[["area_id", "geoid20"]], blocks.set_index("geoid20")["pop20"], bg_total
    )
    context = acs_alloc.area_indicators(
        weights, bgs.set_index("geoid"), mcfg.indicators, mcfg.cv_high_max, mcfg.cv_medium_max
    )
    vintage = f"{cfg.census.acs.year - 4}-{cfg.census.acs.year}"
    if len(context):
        context.insert(0, "run_id", run_id)
        context["acs_vintage"] = vintage

    # Residents beyond vs within the underserved walk: who lives where (D-025 Q4-Q5).
    sides = pd.DataFrame(
        {
            "side": np.where(
                ba["network_m"].fillna(np.inf).to_numpy() > under_m, "beyond", "within"
            ),
            "geoid20": ba["geoid20"].to_numpy(),
        }
    )
    comparison = acs_alloc.compare_sides(
        sides,
        blocks.set_index("geoid20")["pop20"],
        bg_total,
        bgs.set_index("geoid"),
        mcfg.indicators,
        mcfg.cv_high_max,
        mcfg.cv_medium_max,
    )
    analysis = json.loads((run_dir / RUN_FILES["run"]).read_text("utf-8"))

    checks = metrics_checks(
        cfg, metrics, shares, ba, blocks, membership, reach, weights, bg_total, context
    )
    checks += straight_and_side_checks(cfg, shares, analysis, comparison, int(ba["pop20"].sum()))
    inputs = {name: sha256_file(out_dir / f) for name, f in sorted(INPUT_FILES.items())}
    inputs["block_access"] = sha256_file(run_dir / RUN_FILES["block_access"])
    inputs["tiger_tabblock20"] = tiger_sha
    city = metrics.set_index("area_id").loc[CITY_ID]
    city_shares = shares[shares["area_id"] == CITY_ID].set_index("threshold_min")["pct_within"]
    record = {
        "run_id": run_id,
        "region_id": cfg.region_id,
        "generated_at": now_utc(),
        "params": {
            "metrics": mcfg.model_dump(),
            "thresholds_m": {str(k): v for k, v in thresholds.items()},
            "underserved_m": under_m,
            "acs_vintage": vintage,
            "allocation": "2020 block population; denominators from all TIGER blocks",
        },
        "inputs": inputs,
        "code": code_version(repo),
        "seconds": {
            "total": round(time.perf_counter() - t0, 2),
            "reach": round(reach_s, 2),
        },
        "summary": {
            "population": int(city["pop_total"]),
            "share_within_minutes": {str(t): round(float(v), 4) for t, v in city_shares.items()},
            "underserved_pop": int(city["underserved_pop"]),
            "areas": metrics[metrics["area_type"] != CITY_TYPE]["area_type"]
            .value_counts()
            .sort_index()
            .to_dict(),
            "ranked_areas": int(metrics["ranked"].sum()),
            "caveats": caveats(cfg),
            "resident_comparison": {
                "threshold_min": mcfg.underserved_minutes,
                "assumption": "Each block group's census estimates are spread evenly over its "
                "2020 population; differences no larger than their margin of error are not clear.",
                "indicators": comparison,
            },
        },
    }
    report = build_report(cfg, metrics, shares, context, weights, run_id, record)
    _write(run_dir, metrics, shares, context, reach, record, checks, report)
    return MetricsResult(run_id, metrics, shares, context, reach, record, checks, run_dir)


# --------------------------------------------------------------------------- checks


def metrics_checks(
    cfg: RegionConfig,
    metrics: pd.DataFrame,
    shares: pd.DataFrame,
    ba: pd.DataFrame,
    blocks: pd.DataFrame,
    membership: pd.DataFrame,
    reach: pd.DataFrame,
    weights: pd.DataFrame,
    bg_total: pd.Series,
    context: pd.DataFrame,
) -> list[Check]:
    assert cfg.metrics is not None
    mcfg = cfg.metrics
    under_m = cfg.analysis.threshold_distances_m[mcfg.underserved_minutes]
    m = metrics.set_index("area_id")
    city = m.loc[CITY_ID]
    d = ba["network_m"].fillna(np.inf)
    checks: list[Check] = []

    # City figures straight from the block table, by a separate path.
    expected = {
        "pop_total": int(ba["pop20"].sum()),
        "underserved_pop": int(ba.loc[d > under_m, "pop20"].sum()),
    }
    actual = {k: int(city[k]) for k in expected}
    checks.append(_hard("metrics.city_from_blocks", actual == expected, expected, actual))

    pct = shares["pct_within"]
    checks.append(_hard("metrics.pct_bounds", bool(((pct >= 0) & (pct <= 1)).all())))
    wide = shares.pivot(index="area_id", columns="threshold_min", values="pct_within")
    cols = sorted(wide.columns)
    bad = [
        a
        for a, row in wide.iterrows()
        if any(row[x] > row[y] + TOL for x, y in itertools.pairwise(cols))
    ]
    checks.append(_hard("metrics.monotone_thresholds", not bad, "pct(t) non-decreasing", bad))

    # Each area's residents = its member blocks' 2020 population (from blocks, not runs).
    pop = blocks.set_index("geoid20")["pop20"]
    by_area = (
        membership.assign(pop20=pop.reindex(membership["geoid20"]).to_numpy())
        .groupby("area_id")["pop20"]
        .sum()
    )
    by_area = by_area[by_area > 0]
    mism = (m["pop_total"].drop(CITY_ID).reindex(by_area.index).fillna(-1) != by_area).sum()
    checks.append(_hard("metrics.area_pop_matches_membership", int(mism) == 0, 0, int(mism)))

    for t in mcfg.partition_area_types:
        mem = membership[membership["area_type"] == t]
        dup = int(mem["geoid20"].duplicated().sum())
        total = int(m.loc[m["area_type"] == t, "pop_total"].sum())
        checks.append(
            _hard(
                f"metrics.partition.{t}",
                dup == 0 and total == int(city["pop_total"]),
                {"duplicate_blocks": 0, "population": int(city["pop_total"])},
                {"duplicate_blocks": dup, "population": total},
            )
        )

    # The per-park searches must agree with Stage 6's nearest-park distance.
    r = reach.set_index("geoid20")
    near = d.set_axis(ba["geoid20"])
    within = near <= under_m
    diff = (r["nearest_reachable_m"].reindex(near.index)[within] - near[within]).abs()
    missing = int(r["parks_reachable"].reindex(near.index)[within].eq(0).sum())
    extra = int(r["parks_reachable"].reindex(near.index)[~within].gt(0).sum())
    max_diff = float(diff.max()) if len(diff) else 0.0
    checks.append(
        _hard(
            "metrics.reach_matches_nearest_park",
            missing == 0 and extra == 0 and max_diff < TOL,
            "min per-park distance == Stage 6 distance; none reachable beyond it",
            {"max_diff_m": max_diff, "missing": missing, "extra": extra},
        )
    )

    # Allocation: the city's share of a block group is its in-city 2020 share; a partition
    # type can't allocate more than that; the full totals cover the in-city blocks.
    w = weights.set_index(["area_id", "bg"])
    city_f = w.xs(CITY_ID, level="area_id")["f"]
    in_city = blocks.groupby(acs_alloc.block_group_of(blocks["geoid20"]))["pop20"].sum()
    expected_f = np.divide(
        in_city.reindex(city_f.index).to_numpy(dtype=float),
        bg_total.reindex(city_f.index).to_numpy(dtype=float),
        out=np.zeros(len(city_f)),
        where=bg_total.reindex(city_f.index).to_numpy() > 0,
    )
    f_ok = bool(np.allclose(city_f.to_numpy(), expected_f) and (weights["f"] <= 1 + TOL).all())
    over = []
    for t in sorted(set(metrics["area_type"]) - {CITY_TYPE}):
        if next(a for a in cfg.analysis_areas if a.area_type == t).allow_overlaps:
            continue
        ids = set(m.index[m["area_type"] == t])
        s = weights[weights["area_id"].isin(ids)].groupby("bg")["f"].sum()
        over += [f"{t}:{g}" for g in s.index[s > city_f.reindex(s.index) + TOL]]
    checks.append(
        _hard(
            "acs.allocation_weights",
            f_ok and not over,
            "city f = in-city share of 2020 population; non-overlapping types sum to <= it",
            {"over": over[:5]},
            straddling_block_groups=int((city_f < 1 - TOL).sum()),
            block_groups=len(city_f),
        )
    )
    short = in_city[in_city > bg_total.reindex(in_city.index).fillna(-1)]
    checks.append(
        _hard(
            "acs.denominators_cover_block_groups",
            short.empty,
            "full TIGER total >= in-study population for every block group",
            short.index.tolist()[:5],
        )
    )

    # Informational.
    if len(context):
        areas_only = context[context["area_id"] != CITY_ID]
        counts = areas_only.groupby(["indicator", "reliability"]).size().unstack(fill_value=0)
        checks.append(_info("acs.reliability", counts.to_dict("index")))
        total = context[(context["area_id"] == CITY_ID) & (context["kind"] == "count")]
        checks.append(
            _info(
                "acs.city_counts",
                {
                    r.indicator: None if pd.isna(r.estimate) else round(r.estimate)
                    for r in total.itertuples()
                },
                census_2020_population=int(city["pop_total"]),
            )
        )
    lost = weights[(weights["pop_bg"] == 0)]["bg"].nunique()
    checks.append(_info("acs.block_groups_without_2020_population", int(lost)))
    areas = m[m["area_type"] != CITY_TYPE].copy()
    pw = 1 - areas["underserved_share"]
    areas["vs_blocks"] = (areas["block_share_within"] - pw).abs()
    areas["vs_land"] = (areas["land_share_within"] - pw).abs()
    checks.append(
        _info(
            "metrics.weighting_effect",
            {
                "median_abs_vs_blocks": round(float(areas["vs_blocks"].median()), 4),
                "max_abs_vs_blocks": round(float(areas["vs_blocks"].max()), 4),
                "median_abs_vs_land": round(float(areas["vs_land"].median()), 4),
                "max_abs_vs_land": round(float(areas["vs_land"].max()), 4),
            },
            largest_vs_blocks=str(areas["vs_blocks"].idxmax()),
            largest_vs_land=str(areas["vs_land"].idxmax()),
        )
    )
    overlap = {
        t: int(areas.loc[areas["area_type"] == t, "pop_total"].sum() - city["pop_total"])
        for t in sorted(set(areas["area_type"]))
    }
    checks.append(_info("metrics.sum_of_areas_minus_city", overlap))
    return checks


def straight_and_side_checks(
    cfg: RegionConfig,
    shares: pd.DataFrame,
    analysis: dict[str, Any],
    comparison: list[dict[str, Any]],
    population: int,
) -> list[Check]:
    """Straight-line shares (D-025 Q3) and the resident comparison (Q4-Q5)."""
    checks: list[Check] = []
    s = shares.dropna(subset=["pct_within_straight"])
    below = s[s["pct_within_straight"] < s["pct_within"] - TOL]
    checks.append(
        _hard(
            "metrics.straight_not_below_walk",
            below.empty,
            "straight-line share >= walking share (a straight line is a floor on the walk)",
            below[["area_id", "threshold_min"]].to_dict("records")[:5],
        )
    )
    # The city's straight-line shares must equal Stage 6's own summary (rounded to 4 places).
    stage6 = analysis["summary"]["share_within_minutes"]
    city = s[s["area_id"] == CITY_ID].set_index("threshold_min")["pct_within_straight"]
    diffs = {
        str(t): round(abs(float(v) - stage6[str(t)]["straight_line"]), 6) for t, v in city.items()
    }
    checks.append(
        _hard(
            "metrics.city_straight_matches_analysis",
            all(d <= 5e-5 for d in diffs.values()),
            "city straight-line share = the analysis run's summary (±0.00005, its rounding)",
            diffs,
        )
    )
    covered = {
        c["indicator"]: c["within"]["residents"] + c["beyond"]["residents"] for c in comparison
    }
    checks.append(
        _hard(
            "acs.sides_cover_every_resident",
            all(v == population for v in covered.values()),
            population,
            covered,
        )
    )
    checks.append(
        _info(
            "acs.resident_comparison",
            {
                c["indicator"]: {
                    "within": round(c["within"]["estimate"], 4),
                    "beyond": round(c["beyond"]["estimate"], 4),
                    "difference": round(c["difference"], 4),
                    "moe": round(c["difference_moe"], 4),
                    "clear": c["clear"],
                }
                for c in comparison
            },
        )
    )
    return checks


# --------------------------------------------------------------------------- report


def _pct(x: float) -> str:
    return "n/a" if pd.isna(x) else f"{x:.1%}"


def _int(x: float) -> str:
    return "n/a" if pd.isna(x) else f"{round(x):,}"


def _m(x: float) -> str:
    return "unreachable" if pd.isna(x) else f"{x:,.0f} m"


def _type_label(area_type: str, plural: bool = False) -> str:
    label = area_type.replace("_", " ")
    return label + "s" if plural else label


def _display(row: Any) -> str:
    name = str(row["name"])
    return f"{_type_label(row['area_type']).capitalize()} {name}" if name.isdigit() else name


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _context_cell(ctx: pd.DataFrame, area_id: str, indicator: str) -> str:
    row = ctx[(ctx["area_id"] == area_id) & (ctx["indicator"] == indicator)]
    if row.empty:
        return "n/a"
    r = row.iloc[0]
    if pd.isna(r["estimate"]):
        return "*undefined (low)*"
    if r["kind"] == "share":
        text = f"{r['estimate']:.0%} ± {r['moe']:.0%}" if not pd.isna(r["moe"]) else "n/a"
    else:
        text = f"{r['estimate']:,.0f} ± {r['moe']:,.0f}"
    return f"*{text} (low)*" if r["reliability"] == acs_alloc.LOW else text


def caveats(cfg: RegionConfig) -> list[dict[str, str]]:
    """Plain-language limitations for the map (D-023 Q9): one source for report and API."""
    not_parks = ""
    if cfg.parks is not None and cfg.parks.subtract_reasons:
        values = [
            v.replace("_", " ")
            for r in cfg.parks.subtract_reasons
            for vs in cfg.parks.exclude_tags.get(r, {}).values()
            for v in vs
        ]
        if values:
            listed = ", ".join(values[:-1]) + (" or " if len(values) > 1 else "") + values[-1]
            not_parks = f" and areas tagged {listed}"
    return [
        {
            "id": "walk_network",
            "title": "Distances are walking routes on OpenStreetMap paths.",
            "text": f"They assume {cfg.analysis.walk_speed_kmh} km/h, from each census block's "
            "interior point to the nearest point where a path meets a park. Missing or wrong "
            "paths in OpenStreetMap change the result; steep hills, busy crossings and "
            "sidewalk quality are not modelled.",
        },
        {
            "id": "block_points",
            "title": "Each block is one point.",
            "text": "Everyone in a block is treated as living at the same spot, so large "
            "blocks are less precise.",
        },
        {
            "id": "park_definition",
            "title": "What counts as a park",
            "text": "comes from OpenStreetMap tags and the project's rules (decision log "
            "D-018): public parks and natural areas count; areas closed to the public"
            f"{not_parks} do not.",
        },
        {
            "id": "context_not_cause",
            "title": "Demographics are context, not cause.",
            "text": "Showing car-free households or people of color next to park access does "
            "not mean one causes the other. Patterns across areas say nothing about any "
            "individual household (the ecological fallacy).",
        },
        {
            "id": "survey_error",
            "title": "Survey estimates have margins of error.",
            "text": "ACS figures come from a sample; values marked low-reliability can be far "
            "off and are never used to rank areas.",
        },
        {
            "id": "boundaries",
            "title": "Boundaries matter.",
            "text": 'Different area boundaries give different "worst" areas; shared blocks '
            "count in each overlapping area, and city totals count each resident once.",
        },
    ]


def build_report(
    cfg: RegionConfig,
    metrics: pd.DataFrame,
    shares: pd.DataFrame,
    context: pd.DataFrame,
    weights: pd.DataFrame,
    run_id: str,
    record: dict[str, Any],
) -> str:
    assert cfg.metrics is not None
    mcfg: MetricsConfig = cfg.metrics
    u = mcfg.underserved_minutes
    um = cfg.analysis.threshold_distances_m[u]
    n = mcfg.ranking_size
    floor = mcfg.min_residents_for_ranking
    m = metrics.set_index("area_id", drop=False).rename_axis(None)
    city = m.loc[CITY_ID]
    wide = shares.pivot(index="area_id", columns="threshold_min", values="pct_within")
    crow = (
        shares.pivot(index="area_id", columns="threshold_min", values="pct_within_straight")
        if "pct_within_straight" in shares.columns
        else None
    )
    m = m.join(wide.rename(columns=lambda t: f"pct_{t}"))
    types = sorted(set(m["area_type"]) - {CITY_TYPE})
    code = record["code"]
    lines = [
        f"# Area metrics: {cfg.name}",
        "",
        f"Analysis run `{run_id}`; metrics code `{(code['commit'] or 'unknown')[:7]}`"
        f"{' (uncommitted changes)' if code['dirty'] else ''}. "
        f"Walk speed {cfg.analysis.walk_speed_kmh} km/h; thresholds "
        + ", ".join(f"{t} min = {d:,.0f} m" for t, d in cfg.analysis.threshold_distances_m.items())
        + f". **Underserved** = beyond a {u}-minute walk ({um:,.0f} m) to the nearest park "
        "(unreachable blocks count as beyond every threshold).",
        "",
        "## 1. Citywide (computed from blocks)",
        "",
        _table(
            ["Measure", "Value"],
            [
                ["Residents (2020 Census)", _int(city["pop_total"])],
                *[
                    [
                        f"Within {t} min (walking route · straight line)",
                        f"{_pct(wide.loc[CITY_ID, t])} · {_pct(crow.loc[CITY_ID, t])}"
                        if crow is not None
                        else _pct(wide.loc[CITY_ID, t]),
                    ]
                    for t in cfg.analysis.thresholds_min
                ],
                [
                    f"Underserved (beyond {u} min)",
                    f"{_int(city['underserved_pop'])} ({_pct(city['underserved_share'])})",
                ],
                ["Residents with no walking route (islands)", _int(city["unreachable_pop"])],
                ["Typical resident's walk (population-weighted median)", _m(city["median_walk_m"])],
                [
                    f"Park area a typical resident can reach in {u} min (weighted median)",
                    f"{city['reachable_park_ha_median']:,.1f} ha",
                ],
            ],
        ),
        "",
    ]
    for t in types:
        s = int(m.loc[m["area_type"] == t, "pop_total"].sum())
        if s != int(city["pop_total"]):
            lines.append(
                f"- Adding up the {_type_label(t, True)} gives {s:,} residents, "
                f"{s - int(city['pop_total']):+,} against the city: shared blocks count in "
                "every area they belong to, so citywide figures never come from adding areas."
            )
    lines.append("")

    def rows_for(df: pd.DataFrame) -> list[list[str]]:
        return [
            [
                str(i + 1),
                _display(r),
                _int(r["pop_total"]),
                _pct(r[f"pct_{u}"]),
                _int(r["underserved_pop"]),
                _m(r["median_walk_m"]),
                _int(r["priority_score"]),
            ]
            for i, (_, r) in enumerate(df.iterrows())
        ]

    head = [
        "#",
        "Area",
        "Residents",
        f"Within {u} min",
        "Underserved",
        "Median walk",
        "Priority (per km²)",
    ]
    # Types with few areas (e.g. 4 districts) appear once, in full, in section 6.
    big_types = [t for t in types if int((m["area_type"] == t).sum()) > n]
    sections = [
        (
            f"## 2. Lowest share of residents within {u} minutes",
            f"Areas with at least {floor:,} residents (smaller areas swing on a few people; "
            "they are listed after each table, not hidden).",
            "underserved_share",
            False,
            floor,
        ),
        (
            "## 3. Most underserved residents",
            "The scale of need: a large area at a middling share can hold more underserved "
            "people than a small area at 0%.",
            "underserved_pop",
            False,
            0,
        ),
        (
            "## 4. Priority: underserved residents per km² of populated land",
            "High where many residents are packed beyond a "
            f"{u}-minute walk (D-011). Land = Census land area of the area's populated "
            f"blocks. Areas with at least {floor:,} residents.",
            "priority_score",
            False,
            floor,
        ),
    ]
    for title, note, by, asc, min_pop in sections:
        lines += [title, "", note, ""]
        for t in big_types:
            top = ranked(metrics, by, ascending=asc, min_pop=min_pop).merge(
                m[[f"pct_{u}"]], left_on="area_id", right_index=True
            )
            top = top[top["area_type"] == t].head(n)
            lines += [f"**{_type_label(t, True).capitalize()}**", "", _table(head, rows_for(top))]
            if min_pop:
                small = m[(m["area_type"] == t) & (m["pop_total"] < min_pop)].sort_values(
                    [f"pct_{u}", "area_id"]
                )
                if len(small):
                    lines += [
                        "",
                        f"Not ranked (< {min_pop:,} residents): "
                        + "; ".join(
                            f"{_display(r)} ({_int(r['pop_total'])} residents, "
                            f"{_pct(r[f'pct_{u}'])} within {u} min)"
                            for _, r in small.iterrows()
                        )
                        + ".",
                    ]
            lines.append("")

    # Weighting demonstration.
    a = m[m["area_type"] != CITY_TYPE].copy()
    a["vs_blocks"] = (a["block_share_within"] - a[f"pct_{u}"]).abs()
    a["vs_land"] = (a["land_share_within"] - a[f"pct_{u}"]).abs()
    demo = pd.concat([a.nlargest(3, "vs_blocks"), a.nlargest(3, "vs_land")]).drop_duplicates(
        "area_id"
    )
    lines += [
        "## 5. Why population weighting matters",
        "",
        f"The same blocks, three questions. *Residents* within {u} min (what this project "
        "reports) vs share of *blocks* (a 3-person block counts as much as a 600-person one) "
        "vs share of *land* (empty land dominates). Largest differences:",
        "",
        _table(
            ["Area", "Residents", "Residents within", "Blocks within", "Land within"],
            [
                [
                    _display(r),
                    _int(r["pop_total"]),
                    _pct(r[f"pct_{u}"]),
                    _pct(r["block_share_within"]),
                    _pct(r["land_share_within"]),
                ]
                for _, r in demo.iterrows()
            ],
        ),
        "",
        f"Across all areas the median absolute difference is {a['vs_blocks'].median():.1%} "
        f"(blocks) and {a['vs_land'].median():.1%} (land): usually small, occasionally large.",
        "",
        "## 6. Same city, different boundaries",
        "",
        "Grouping the same blocks differently changes which places look worst (the "
        "modifiable areal unit problem). Spread of the share within "
        f"{u} min across areas of each type (ranked areas only):",
        "",
        _table(
            ["Area type", "Ranked areas", "Lowest", "Median", "Highest"],
            [
                [
                    _type_label(t, True),
                    str(int(((a["area_type"] == t) & a["ranked"]).sum())),
                    _pct(a.loc[(a["area_type"] == t) & a["ranked"], f"pct_{u}"].min()),
                    _pct(a.loc[(a["area_type"] == t) & a["ranked"], f"pct_{u}"].median()),
                    _pct(a.loc[(a["area_type"] == t) & a["ranked"], f"pct_{u}"].max()),
                ]
                for t in types
            ],
        ),
        "",
    ]
    for t in types:
        sub = a[a["area_type"] == t]
        if len(sub) <= n:
            lines += [
                f"**All {_type_label(t, True)}**",
                "",
                _table(
                    head,
                    rows_for(sub.sort_values(["underserved_share", "area_id"], ascending=False)),
                ),
                "",
            ]

    # Park area.
    zero_inside = a[(a["park_m2_inside"] < 1) & a["ranked"]].sort_values(
        "reachable_park_ha_median", ascending=False
    )
    big_inside = a[a["ranked"]].nlargest(3, "park_m2_inside_per_resident")
    park_rows = pd.concat([zero_inside.head(3), big_inside]).drop_duplicates("area_id")
    lines += [
        "## 7. Park area: reachable vs inside the boundary",
        "",
        f"**Reachable** (primary): total area of the parks a resident can walk to within {u} "
        "minutes, population-weighted median over the area's blocks. **Inside** "
        "(secondary): park area within the area's boundary per resident; it measures where "
        "boundaries fall, not access.",
        "",
        _table(
            ["Area", "Residents", f"Reachable in {u} min (median)", "Inside, per resident"],
            [
                [
                    _display(r),
                    _int(r["pop_total"]),
                    f"{r['reachable_park_ha_median']:,.1f} ha",
                    f"{r['park_m2_inside_per_resident']:,.0f} m²",
                ]
                for _, r in park_rows.iterrows()
            ],
        ),
        "",
    ]

    # ACS context.
    if len(context):
        ind_ids = [i.id for i in mcfg.indicators]
        labels = {i.id: i.label for i in mcfg.indicators}
        focus = pd.concat(
            [
                ranked(metrics, "underserved_share", ascending=False, min_pop=floor),
                ranked(metrics, "underserved_pop", ascending=False),
            ]
        )
        # The finest area type (the most areas) is where context differs most.
        finest = max(types, key=lambda t: (int((a["area_type"] == t).sum()), t))
        focus = focus[focus["area_type"] == finest].drop_duplicates("area_id").head(n)
        rel = (
            context[context["area_id"] != CITY_ID]
            .groupby(["indicator", "reliability"])
            .size()
            .unstack(fill_value=0)
            .reindex(columns=[acs_alloc.HIGH, acs_alloc.MEDIUM, acs_alloc.LOW], fill_value=0)
        )
        straddle = int((weights[weights["area_id"] == CITY_ID]["f"] < 1 - TOL).sum())
        lines += [
            f"## 8. Demographic context (ACS {record['params']['acs_vintage']}, 5-year)",
            "",
            "Context, **not cause**. Block-group estimates are allocated to areas by 2020 "
            "block population, dividing by the population of *all* of a block group's blocks "
            f"({straddle} block groups straddle the study boundary and contribute only their "
            "in-boundary share). ± is the 90% margin of error. *Italic (low)* = coefficient "
            f"of variation above {mcfg.cv_medium_max:.0%}: shown, never ranked.",
            "",
            "**Areas from the rankings above**",
            "",
            _table(
                ["Area", f"Within {u} min", *[labels[i] for i in ind_ids]],
                [
                    [
                        _display(m.loc[r["area_id"]]),
                        _pct(m.loc[r["area_id"], f"pct_{u}"]),
                        *[_context_cell(context, r["area_id"], i) for i in ind_ids],
                    ]
                    for _, r in focus.iterrows()
                ]
                + [
                    [
                        cfg.name + " (city)",
                        _pct(wide.loc[CITY_ID, u]),
                        *[_context_cell(context, CITY_ID, i) for i in ind_ids],
                    ]
                ],
            ),
            "",
            "**Reliability of the area estimates** (all area types)",
            "",
            _table(
                ["Indicator", "High", "Medium", "Low"],
                [
                    [labels.get(str(i), str(i)), *[str(int(v)) for v in row]]
                    for i, row in rel.reindex(ind_ids).fillna(0).iterrows()
                ],
            ),
            "",
        ]

    comp = record["summary"].get("resident_comparison")
    if comp and comp["indicators"]:
        t = comp["threshold_min"]
        lines += [
            f"**Residents farther than vs within a {t}-minute walk** (who lives where, not why)",
            "",
            _table(
                ["Indicator", f"Farther than {t} min", f"Within {t} min", "Difference"],
                [
                    [
                        c["label"],
                        f"{c['beyond']['estimate']:.1%} ± {c['beyond']['moe']:.1%}",
                        f"{c['within']['estimate']:.1%} ± {c['within']['moe']:.1%}",
                        f"{c['difference']:+.1%} ± {c['difference_moe']:.1%}"
                        + ("" if c["clear"] else " (no clear difference)"),
                    ]
                    for c in comp["indicators"]
                ],
            ),
            "",
            comp["assumption"],
            "",
        ]

    lines += [
        "## 9. Caveats (draft text for the map)",
        "",
        *[f"- **{c['title']}** {c['text']}" for c in caveats(cfg)],
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- writing


def _write(
    run_dir: Path,
    metrics: pd.DataFrame,
    shares: pd.DataFrame,
    context: pd.DataFrame,
    reach: pd.DataFrame,
    record: dict[str, Any],
    checks: list[Check],
    report: str,
) -> None:
    metrics.to_parquet(run_dir / METRICS_FILES["area_metrics"], index=False)
    shares.to_parquet(run_dir / METRICS_FILES["area_thresholds"], index=False)
    context.to_parquet(run_dir / METRICS_FILES["area_context"], index=False)
    reach.drop(columns="nearest_reachable_m").to_parquet(
        run_dir / METRICS_FILES["block_park_reach"], index=False
    )
    (run_dir / METRICS_FILES["run"]).write_text(_dump(record), "utf-8")
    hard = [c for c in checks if c.severity == "hard"]
    validation = {
        "run_id": record["run_id"],
        "summary": {
            "hard_passed": sum(1 for c in hard if c.passed),
            "hard_failed": len(failed(checks)),
        },
        "checks": [asdict(c) for c in checks],
    }
    (run_dir / METRICS_FILES["validation"]).write_text(_dump(validation), "utf-8")
    (run_dir / METRICS_FILES["report"]).write_text(report, "utf-8")


def has_metrics(run_dir: Path) -> bool:
    return (run_dir / METRICS_FILES["run"]).is_file()


def metrics_freshness_problems(out_dir: Path, run_id: str) -> list[str]:
    """Reasons a run's metrics no longer match the files they were computed from."""
    run_dir = out_dir / RUNS_DIR / run_id
    record = json.loads((run_dir / METRICS_FILES["run"]).read_text("utf-8"))
    problems = []
    for name, digest in record["inputs"].items():
        if name == "tiger_tabblock20":
            continue  # raw downloads are immutable (checked by their manifest at fetch)
        path = run_dir / RUN_FILES[name] if name == "block_access" else out_dir / INPUT_FILES[name]
        if sha256_file(path) != digest:
            problems.append(f"{path.name} changed since the metrics for run {run_id}")
    validation = json.loads((run_dir / METRICS_FILES["validation"]).read_text("utf-8"))
    if validation["summary"]["hard_failed"]:
        problems.append(
            f"metrics for run {run_id} have {validation['summary']['hard_failed']} failed check(s)"
        )
    return problems
