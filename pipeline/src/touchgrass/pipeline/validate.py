"""Step 3: check processed outputs and write ``validation.json``.

Hard checks guard correctness: any failure makes the run exit non-zero.
Soft checks are facts to read: counts, distributions, data-quality signals. They never
fail the run but are always reported, so surprises are visible rather than silent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from touchgrass.config import RegionConfig
from touchgrass.core.compare import ACRE_M2
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.core.parks import INCLUDED, missing_sentinels
from touchgrass.sources.http import now_utc

VALIDATION_FILE = "validation.json"
CV_UNRELIABLE = 0.40  # coefficient of variation above which an ACS estimate is unreliable
Z_90 = 1.645  # ACS MOEs are published at 90% confidence


@dataclass
class Check:
    name: str
    severity: Literal["hard", "soft"]
    passed: bool | None  # None for purely informational soft checks
    expected: Any = None
    actual: Any = None
    details: dict[str, Any] = field(default_factory=dict)


def _hard(name: str, ok: bool, expected: Any = None, actual: Any = None, **details: Any) -> Check:
    return Check(name, "hard", bool(ok), expected, actual, details)


def _info(name: str, actual: Any = None, **details: Any) -> Check:
    return Check(name, "soft", None, None, actual, details)


def _geometry_checks(frames: dict[str, Any]) -> list[Check]:
    checks = []
    for name, frame in frames.items():
        if name == "area_membership":
            continue
        epsg = frame.crs.to_epsg() if frame.crs is not None else None
        checks.append(_hard(f"{name}.crs", epsg == STORAGE_EPSG, STORAGE_EPSG, epsg))
        bad = int((~frame.geometry.is_valid | frame.geometry.is_empty).sum())
        checks.append(_hard(f"{name}.geometries_valid", bad == 0, 0, bad))
    return checks


def _census_checks(cfg: RegionConfig, blocks: Any, bgs: Any) -> list[Check]:
    checks = []
    geoids = blocks["geoid20"]
    checks.append(
        _hard(
            "blocks.geoid_format_unique",
            bool(geoids.str.fullmatch(r"\d{15}").all() and geoids.is_unique),
            actual=len(geoids),
        )
    )
    checks.append(
        _hard(
            "block_groups.geoid_format_unique",
            bool(bgs["geoid"].str.fullmatch(r"\d{12}").all() and bgs["geoid"].is_unique),
            actual=len(bgs),
        )
    )
    orphan = sorted(set(geoids.str.slice(0, 12)) - set(bgs["geoid"]))
    checks.append(
        _hard("blocks.nest_in_block_groups", not orphan, 0, len(orphan), sample=orphan[:5])
    )

    total = int(blocks["pop20"].sum())
    expected = cfg.validation.expected_population
    if expected is not None:
        rel = (total - expected) / expected
        checks.append(
            _hard(
                "population.matches_official_total",
                abs(rel) <= cfg.validation.population_tolerance,
                expected,
                total,
                relative_difference=round(rel, 6),
                tolerance=cfg.validation.population_tolerance,
            )
        )
    else:
        checks.append(_info("population.total", total))

    pop, housing = blocks["pop20"], blocks["housing20"]
    checks.append(
        _info(
            "blocks.population_distribution",
            {
                "blocks": len(blocks),
                "populated_blocks": int((pop > 0).sum()),
                "empty_blocks": int((pop == 0).sum()),
                "max_block_population": int(pop.max()),
            },
        )
    )
    checks.append(
        _info(
            "blocks.privacy_noise_signals",
            {
                "pop_gt0_housing_eq0": int(((pop > 0) & (housing == 0)).sum()),
                "pop_in_those_blocks": int(pop[(pop > 0) & (housing == 0)].sum()),
                "housing_gt0_pop_eq0": int(((housing > 0) & (pop == 0)).sum()),
            },
            note="2020 counts include differential-privacy noise; single blocks are unreliable",
        )
    )
    census_area = blocks["aland20"] + blocks["awater20"]
    rel_err = (blocks["area_m2"] - census_area) / census_area.where(census_area > 0)
    checks.append(
        _info(
            "blocks.area_vs_census",
            {
                "our_total_km2": round(float(blocks["area_m2"].sum()) / 1e6, 3),
                "census_total_km2": round(float(census_area.sum()) / 1e6, 3),
                "total_ratio": round(float(blocks["area_m2"].sum() / census_area.sum()), 6),
                "median_block_rel_diff": round(float(rel_err.median()), 6),
                "p99_abs_block_rel_diff": round(float(rel_err.abs().quantile(0.99)), 6),
            },
            note="Independent check of our analysis-CRS areas against Census ALAND+AWATER",
        )
    )
    fallback = blocks["rep_point_source"] != "census_intpt"
    checks.append(
        _info(
            "blocks.rep_point_fallbacks",
            int(fallback.sum()),
            sample=blocks.loc[fallback, "geoid20"].head(5).tolist(),
        )
    )
    partial = blocks["frac_in_boundary"] < 0.5
    checks.append(
        _info(
            "blocks.mostly_outside_boundary_but_counted",
            {"blocks": int(partial.sum()), "population": int(blocks.loc[partial, "pop20"].sum())},
            note="Representative point inside the boundary, but <50% of the block's area is",
        )
    )
    return checks


def _area_checks(cfg: RegionConfig, frames: dict[str, Any]) -> list[Check]:
    areas, blocks, members = frames["analysis_areas"], frames["blocks"], frames["area_membership"]
    pop = blocks.set_index("geoid20")["pop20"]
    checks = []
    for spec in cfg.analysis_areas:
        t = spec.area_type
        these = areas[areas["area_type"] == t]
        checks.append(_hard(f"{t}.keys_unique", these["source_key"].is_unique, actual=len(these)))
        checks.append(_info(f"{t}.count", len(these)))

        m = members[members["area_type"] == t]
        per_block = m.groupby("geoid20").size().reindex(pop.index, fill_value=0)
        dist = {}
        for label, mask in {
            "0": per_block == 0,
            "1": per_block == 1,
            "2+": per_block >= 2,
        }.items():
            dist[label] = {"blocks": int(mask.sum()), "population": int(pop[mask].sum())}
        multi = per_block[per_block >= 2]
        details: dict[str, Any] = {"sample_multi": multi.index[:5].tolist()}
        if spec.allow_overlaps:
            checks.append(_info(f"{t}.membership_distribution", dist, **details))
        else:
            checks.append(
                _hard(f"{t}.no_block_in_two_areas", multi.empty, 0, len(multi), distribution=dist)
            )

        if spec.population_check_field:
            summed = (
                m.merge(pop.rename("pop20"), left_on="geoid20", right_index=True)
                .groupby("area_id")["pop20"]
                .sum()
            )
            official = these.set_index("area_id")["check_population"].astype("int64")
            diff = (summed.reindex(official.index, fill_value=0) - official).astype(int)
            checks.append(
                _hard(
                    f"{t}.population_reconciles",
                    bool((diff == 0).all()),
                    {k: int(v) for k, v in official.items()},
                    {k: int(v) for k, v in summed.reindex(official.index, fill_value=0).items()},
                    differences={k: int(v) for k, v in diff.items() if v != 0},
                )
            )
    return checks


def _acs_checks(cfg: RegionConfig, bgs: Any) -> list[Check]:
    checks = []
    est_cols = [c for c in bgs.columns if "_e" in c and c.split("_e")[-1].isdigit()]
    joined = bgs[est_cols].notna().any(axis=1)
    checks.append(
        _info(
            "acs.block_groups_with_data",
            {"block_groups": len(bgs), "with_any_estimate": int(joined.sum())},
        )
    )
    nulls = {c: int(bgs[c].isna().sum()) for c in est_cols if bgs[c].isna().any()}
    checks.append(_info("acs.null_estimates_by_column", nulls))
    for table in cfg.census.acs.tables:
        e, m = f"{table.lower()}_e001", f"{table.lower()}_m001"
        if e in bgs and m in bgs:
            est, moe = bgs[e].astype("float64"), bgs[m].astype("float64")
            cv = (moe / Z_90) / est.where(est > 0)
            checks.append(
                _info(
                    f"acs.{table.lower()}_reliability",
                    {
                        "median_cv": round(float(cv.median()), 4),
                        "share_cv_over_40pct": round(float((cv > CV_UNRELIABLE).mean()), 4),
                    },
                    note="CV = (MOE/1.645)/estimate; >40% is commonly treated as unreliable",
                )
            )
    return checks


def _park_checks(cfg: RegionConfig, frames: dict[str, Any], stats: dict[str, Any]) -> list[Check]:
    if cfg.parks is None or "parks" not in frames:
        return []
    rules, units, cands = cfg.parks, frames["parks"], frames["green_candidates"]
    checks = [
        _hard("parks.ids_unique", bool(units["park_id"].is_unique), actual=len(units)),
        _hard(
            "parks.min_area",
            bool((units["area_m2"] >= rules.min_area_m2).all()),
            rules.min_area_m2,
            round(float(units["area_m2"].min()), 1) if len(units) else None,
        ),
        _hard(
            "green_candidates.decisions_complete",
            bool(
                cands.loc[cands["included"], "park_class"].notna().all()
                and (cands.loc[~cands["included"], "reason"] != INCLUDED).all()
            ),
            actual=len(cands),
        ),
    ]
    # Every closure listed in config must still match a candidate (OSM ids can change).
    names = dict(
        zip(cands["osm_type"].str[0] + cands["osm_id"].astype(str), cands["name"], strict=True)
    )
    stale = [
        f"{c.osm} ({c.name})"
        for c in rules.closed_to_public
        if c.osm not in names or str(names[c.osm]).casefold() != c.name.casefold()
    ]
    checks.append(
        _hard("parks.closed_list_matched", not stale, "every listed id found", stale or "all found")
    )
    missing = missing_sentinels(units, rules.sentinel_parks)
    checks.append(
        _hard("parks.sentinels_present", not missing, rules.sentinel_parks, missing or "all found")
    )
    park_stats = stats.get("parks", {})
    for key in ("extract", "by_reason", "units", "validation"):
        if key in park_stats:
            checks.append(_info(f"parks.{key}", park_stats[key]))
    disc = frames.get("park_discrepancies")
    if disc is not None and len(disc):
        for direction, label in (("a_only", "osm_only"), ("b_only", "reference_only")):
            top = disc[disc["direction"] == direction].nlargest(10, "area_m2")
            checks.append(
                _info(
                    f"parks.top_discrepancies_{label}",
                    [
                        {"name": n, "acres": round(float(a) / ACRE_M2, 1)}
                        for n, a in zip(top["name"], top["area_m2"], strict=True)
                    ],
                )
            )
    return checks


def run_validation(cfg: RegionConfig, frames: dict[str, Any], stats: dict[str, Any]) -> list[Check]:
    checks = _geometry_checks(frames)
    checks += _census_checks(cfg, frames["blocks"], frames["block_groups"])
    checks += _area_checks(cfg, frames)
    checks += _acs_checks(cfg, frames["block_groups"])
    checks += _park_checks(cfg, frames, stats)
    checks.append(_info("process.stats", stats))
    return checks


def write_report(cfg: RegionConfig, checks: list[Check], out_dir: Path) -> Path:
    hard = [c for c in checks if c.severity == "hard"]
    report = {
        "region_id": cfg.region_id,
        "generated_at": now_utc(),
        "summary": {
            "hard_passed": sum(1 for c in hard if c.passed),
            "hard_failed": sum(1 for c in hard if not c.passed),
            "soft": sum(1 for c in checks if c.severity == "soft"),
        },
        "checks": [asdict(c) for c in checks],
    }
    path = out_dir / VALIDATION_FILE
    path.write_text(json.dumps(report, indent=2, default=_json_default) + "\n", "utf-8")
    return path


def _json_default(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value)}")


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.severity == "hard" and not c.passed]
