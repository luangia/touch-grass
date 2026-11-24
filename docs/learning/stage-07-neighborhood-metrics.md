# Stage 7: Neighborhood Aggregation & Underserved Metrics

> Status: **prepared, not implemented.** Code snippets show the planned approach.
> Numbers marked **(prototype 2026-11-23)** come from a throwaway script run on the latest Stage 6 run (`7c1fc6efaf00effa`, commit `d3fd91d`) in the scratch area. No project code was changed. Where the prototype itself was approximate, that's said.

## 1. Objective and expected outcome

Turn 10,372 block distances into **answers per neighborhood** (and per council district), honestly:
- For each area and threshold (5/10/15 min): residents, residents within the threshold, the share, the **population-weighted median** walk, the number **underserved** (D-011), and park area.
- **Citywide figures computed from blocks**, never by adding up neighborhoods (they overlap).
- A **priority** list (D-011), and **ACS context** per area (zero-vehicle households, people of color) with margins of error and a reliability label.
- A report naming the neighborhoods with the lowest 10-minute access, by **both share and number of people**, plus a demonstration of why population weighting matters, and draft caveat text for the UI.

`touchgrass metrics portland-or` writes this for the latest Stage 6 run; `load` puts it in `area_metrics` / `area_context`.

## 2. Why this stage exists

Residents and city staff think in neighborhoods, not census blocks. But aggregation is where accessibility studies most often go wrong, through a weighting choice, a ranking choice, or a demographic number with a huge margin of error. This stage makes each of those choices explicit and measurable.

## 3. Dependencies

- **Stage 6 (complete, `d3fd91d`):** `block_access` (latest run), with network distance or unreachable for every populated block.
- **Stage 2:** `area_membership` (many-to-many: 233 blocks are in 2+ neighborhoods), `analysis_areas`, `blocks` (2020 population), `block_groups` with ACS 2020–2024 estimates and MOEs (B01003, B19013, B03002, B25044).
- **Raw TIGER blocks** (cached): for the **total** 2020 population of block groups that straddle the city line (§5.5).
- **Decisions:** D-002 (blocks for weighting, ACS for context), D-007 (overlap counted in each area; city totals from blocks), D-011 (underserved = beyond 800 m; priority combines underserved population and density; context, not cause).

## 4. What the prototype found (prototype 2026-11-23)

| | |
|---|---|
| Citywide (from blocks) | 652,503 residents; **85.0%** within 10 min; **97,753 underserved** (15.0%) |
| Sum of neighborhood populations | 666,525: **14,022 more** than the city, from overlaps. Exactly why totals come from blocks |
| Monotone thresholds | 5 ≤ 10 ≤ 15-minute shares hold in all 93 neighborhoods |
| Lowest **share** within 10 min | Sunderland 0% (698 people), Sumner 13.9% (2,090), Sylvan-Highlands 32.8%, Arnold Creek 50.2%, Maplewood 52.5%, Hayden Island 53.2% |
| Most underserved **people** | **Powellhurst-Gilbert 11,867** (63.9%), Hazelwood 9,428, Centennial 8,460, Cully 3,815, Montavilla 3,669, Richmond 3,605 |
| Weighted vs unweighted (share of *blocks*) | median difference 1.4 points; **max 44.9 points** (Bridgeton) |
| Weighted vs land-weighted (share of *area*) | median 1.6 points; **max 67.1 points** (Brooklyn) |
| Park m² per resident *inside the boundary* | median 15. **Alameda, Bridgeton, Hollywood: 0** (parks are just across their edges); Linnton 17,133 (Forest Park inside it). A container metric, not an access metric |
| D-011 priority as a rank sum (underserved + density) | Richmond, Sunnyside, North Tabor top the list. Density outweighs the underserved count; not what "priority" should mean |
| ACS zero-vehicle households | median share 9%; about 28 of 93 neighborhoods have a CV > 40% (rough: the prototype used a simplified MOE formula) |
| ACS allocation trap | I divided by the block group's **in-city** population. 168 of 533 block groups have in-city 2020 population below 90% of their ACS estimate, from straddling the city line plus growth since 2020. Dividing by in-city population hands a straddler's *whole* estimate to its in-city part |
| Correlation (10-min share vs zero-vehicle share), across neighborhoods | +0.27: car-free households are *more* common where park access is better (dense inner neighborhoods). A warning against reading demographics as cause (§5.6) |

## 5. Concepts

### 5.1 Population weighting
The question is *"what share of residents can walk to a park in 10 minutes?"*, so each block counts by its population:

```
pct_within(A, t) = Σ_{b∈A} pop_b · [d_b ≤ t]  /  Σ_{b∈A} pop_b
```

Two tempting alternatives answer different questions:
- **Share of blocks** ("unweighted"): a 2-person block in an industrial area counts as much as a 600-person apartment block. In Bridgeton the answers differ by 45 points.
- **Share of land** (area-weighted): empty land dominates. In Brooklyn, a neighborhood with large rail yards, they differ by 67 points.

The report will show all three for the most-affected neighborhoods, which is the "show why weighting matters" acceptance criterion.

**Population-weighted median distance:** sort blocks by distance, then walk up the cumulative population to half. It's the walk the typical *resident* faces, not the typical block.

### 5.2 Share vs count: what "worst" means
A share hides size and a count hides rate. Sunderland at 0% is 698 people; Powellhurst-Gilbert at 63.9% is 11,867 people. Both matter, for different decisions (equity of rate vs scale of need). The report ranks by **both**, and share rankings are shown only for areas with at least **1,000 residents**, so 10 people in a tiny area don't top the list (Q2).

### 5.3 Overlaps and city totals (D-007)
A block in two neighborhoods counts in **both** neighborhoods' metrics (its residents really are in both associations' areas), but **once** in the city total. So the city's numbers are computed from blocks directly. An invariant checks this: city population = Σ block population = 652,503, and never Σ area population (666,525).

### 5.4 The modifiable areal unit problem (MAUP)
The same blocks grouped differently give different "worst areas". That's the scale effect plus the zoning effect. We have two zonings: **93 neighborhoods** and **4 council districts**. The report shows both, so the reader sees how much the conclusion depends on the boundaries chosen. With 4 districts the extremes average out; with 93, small areas swing.

### 5.5 Allocating ACS block-group estimates to neighborhoods
ACS publishes at block-group level, and neighborhoods don't follow block-group lines. Blocks nest in block groups (GEOID prefix), so each neighborhood's share of a block group is the share of that block group's **2020 population** living in the neighborhood's blocks:

```
f(A, g) = Σ_{b ∈ A ∩ g} pop20_b  /  Σ_{b ∈ g} pop20_b          ← ALL blocks of g, in or out of the city
estimate(A) = Σ_g f(A, g) · estimate_g
```

- **The denominator must cover the whole block group.** Our study `blocks` stop at the city line, so the full count comes from the raw TIGER blocks. The prototype got this wrong (§4).
- **Assumption:** a block group's characteristics are spread evenly over its 2020 population, and the 2020 distribution still holds in 2020–2024. Documented as a limitation.
- **Medians can't be allocated.** A neighborhood's median income isn't any weighted combination of block-group medians. Without the income-distribution table (B19001) it can't be computed, so median income stays at block-group level (for the Stage 9 map) and isn't reported per neighborhood (Q6).

### 5.6 Margins of error and reliability
ACS estimates come from a sample, so each has a 90% margin of error (MOE). Approximate propagation (Census *ACS Handbook*, ch. 8):

| Derived value | MOE |
|---|---|
| Sum X = Σ xᵢ | √(Σ MOEᵢ²) |
| Allocated part f·x | f · MOE (approximation; assumes f is known exactly) |
| Proportion p = X / Y (X a subset of Y) | √(MOE_X² − p² · MOE_Y²) / Y; if the radicand is negative, use the ratio formula √(MOE_X² + p² · MOE_Y²) / Y |

**Coefficient of variation** CV = (MOE / 1.645) / estimate. Reliability labels: **high** < 15%, **medium** 15–30%, **low** > 30% (a common convention among ACS data users). Low-reliability values are shown greyed, not ranked (Q7).

### 5.7 Context, not cause (D-011)
The +0.27 correlation between park access and car-free households across neighborhoods doesn't mean anything causal either way. Dense inner neighborhoods have both more parks within walking distance and more car-free households. And the **ecological fallacy** applies: a neighborhood-level association says nothing about individual households. The UI caveat text (deliverable) says this plainly.

### 5.8 Priority (D-011, needs a precise definition)
D-011 says priority "combines underserved population count with population density". The prototype's rank sum let density dominate. Options:
- **(a) Underserved residents per km² of the area's residential land:** one interpretable number ("1,850 underserved residents per km²") that is high where many people are packed beyond a 10-minute walk.
- **(b) Underserved count, with density only to break ties.**
- **(c) Rank sum:** as prototyped; hard to explain.

Option (a) needs "residential land", which we don't have as data. A practical proxy is the land area of the area's *populated* blocks.

### 5.9 Park area that residents can reach
"Park m² per resident *inside the boundary*" (prototype: Alameda 0, Linnton 17,133) measures where boundaries fall, not access. An access-based alternative: for each block, the total area of **parks reachable within 10 minutes**, then the population-weighted mean per area. It's computed with one bounded Dijkstra per park (448 × limit 800 m; about 10 s by the Stage 6 timing). It answers "how much park can a typical resident walk to?", which pairs naturally with the distance to the *nearest* park.

## 6. How the concepts relate

```
block_access (latest run) ─┬─ area_membership (many-to-many) ──► per area × threshold: pop, within, pct,
                           │                                     weighted median, underserved, reachable park area
                           ├─ city totals from blocks directly (overlaps counted once)
                           └─ blocks → block groups (GEOID prefix) ──► allocation weights f (all blocks of g)
                                                                         │
                                ACS estimates + MOEs ────────────────────┴─► area context: sums, shares, MOE, CV, label
                                                                              │
                    priority (D-011) ◄─ underserved, density ─────────────────┘   report + UI caveats
```

## 7. Fit in the architecture

```
pipeline/src/touchgrass/
  core/aggregate.py      (NEW) weighted shares, weighted median, city totals, priority
  core/acs_alloc.py      (NEW) allocation weights, MOE propagation, CV, reliability
  core/routing.py        + bounded per-park search for reachable park area
  pipeline/metrics.py    (NEW) per-run metrics, checks, markdown report
  db/migrations/0008_area_metrics.sql (NEW) area_metrics, area_context
  cli.py                 + `touchgrass metrics <region>`
data/processed/<region>/runs/<run_id>/
  area_metrics.parquet, area_context.parquet, metrics_report.md, metrics_validation.json
```

**Planned tables:**
- `area_metrics (region_id, run_id, area_id, threshold_min, pop_total, pop_within, pct_within, underserved_pop, median_walk_m_weighted, reachable_park_m2_per_resident, park_m2_inside_per_resident, priority_score)`, with a `city` pseudo-area computed from blocks.
- `area_context (region_id, run_id, area_id, indicator, estimate, moe, cv, reliability, acs_vintage)`.

## 8. Implementation approach

1. **Aggregation** (`core/aggregate.py`): join membership × block_access; per area × threshold, weighted sums and the weighted median; city row from blocks. Unreachable blocks count as beyond every threshold (D-021 Q6).
2. **Reachable park area:** bounded multi-source search per park (its access points as sources, `limit` = 800 m); per block, sum the park areas within reach; then the population-weighted mean per area.
3. **ACS** (`core/acs_alloc.py`): block-group 2020 totals from raw TIGER; weights f; allocated sums and MOEs; shares with the proportion formula (and its fallback); CV and label.
4. **Priority** per the chosen definition (Q3).
5. **Report** (`metrics_report.md`): citywide summary; lowest share (≥ 1,000 residents) and most underserved people; weighting demonstration; council districts (MAUP); context table with reliability; caveat text.
6. **Checks**, outputs, migration, `load`.

### 8.1 Validation checks (planned)
| Check | Kind | Rule |
|---|---|---|
| metrics.city_from_blocks | hard | city pop = Σ block pop (652,503); city underserved = Σ over blocks |
| metrics.pct_bounds | hard | 0 ≤ pct ≤ 1 everywhere |
| metrics.monotone_thresholds | hard | pct(5) ≤ pct(10) ≤ pct(15) for every area |
| metrics.area_pop_matches_membership | hard | each area's pop = Σ pop of its member blocks |
| metrics.district_partition | hard | council districts partition the city: Σ district pop = city pop |
| acs.allocation_weights | hard | for each block group, Σ_A f(A, g) ≤ 1, and = the in-city share of its 2020 population |
| acs.reliability | info | counts by label per indicator |
| metrics.weighting_effect | info | weighted vs block-unweighted vs land-weighted, the largest differences |

### 8.2 Testing strategy
- **Hand-computed fixtures:** two overlapping areas with known populations and distances: weighted vs unweighted answers, weighted median, overlap counted in both areas but once in the city.
- **MOE formulas:** textbook examples (sum, proportion, and the negative-radicand fallback).
- **Allocation:** a block group straddling the boundary, so that only the in-city share is allocated.
- **Invariants** on real data (`-m data`), and determinism (same run in → identical files).

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | Areas | **Neighborhoods + council districts (both configured)** · neighborhoods only | **Both**: districts show the MAUP and are what council members use |
| Q2 | "Worst" lists | **Both share and count; share list only for areas ≥ 1,000 residents** · share only · count only | **Both, with the 1,000 floor** (configurable) |
| Q3 | Priority (D-011) | **(a) underserved residents per km² of populated-block land** · (b) count, density as tiebreak · (c) rank sum | **(a)**, amending D-011 with the precise formula |
| Q4 | Park area metric | **Reachable park area within 10 min (primary) + inside-boundary area (secondary, labelled)** · inside-boundary only | **Reachable primary** |
| Q5 | ACS allocation | **2020 block-population weights, denominators from all TIGER blocks of the block group** · areal (land) weights | **Population weights** |
| Q6 | ACS indicators per area | **Zero-vehicle households (B25044), people of color (1 − non-Hispanic white alone, B03002), total population; median income at block-group level only** · also a weighted mean of block-group medians, labelled approximate | **The three counts-based indicators**; no area-level median income |
| Q7 | Reliability | **Labels high / medium / low at CV 15% / 30%; low values shown but never ranked** · CV only · drop low values | **Labels + no ranking on low** |
| Q8 | Command and storage | **`touchgrass metrics`, outputs in the Stage 6 run folder, tables keyed by run_id** · fold into `analyze` | **Separate command, keyed by run** |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)
- [ ] `touchgrass metrics portland-or` writes metrics, context and a report for the latest run; reruns are identical.
- [ ] Hard invariants pass: city from blocks, bounds, monotone thresholds, membership sums, districts partition the city, allocation weights.
- [ ] The report lists the neighborhoods with the lowest 10-minute access by **share and absolute population**.
- [ ] The weighted vs unweighted (blocks and land) difference is demonstrated with real examples.
- [ ] ACS context with MOE, CV and reliability labels; the allocation uses all blocks of each block group.
- [ ] Draft caveat text for the UI (context not cause; ecological fallacy; ACS reliability; walk-network limitations).
- [ ] Migration 0008 + `load`; ruff, mypy, pytest; hardcoding guard.
- [ ] Docs: D-022 (Q1–Q8), D-011 amendment (priority formula), ARCHITECTURE §4, lesson "what was built".

## 11. Proposed implementation checklist
- [ ] Config: thresholds for share rankings (min residents), reliability CVs, indicator list
- [ ] `core/aggregate.py`: weighted shares, weighted median, city totals, priority
- [ ] Reachable park area (bounded per-park search)
- [ ] `core/acs_alloc.py`: full-block-group denominators from TIGER, allocation, MOE, CV, labels
- [ ] `pipeline/metrics.py` + report + checks; CLI `touchgrass metrics`
- [ ] Migration `0008_area_metrics.sql` + loader
- [ ] Tests: fixtures with known weighted answers, MOE textbook cases, straddling allocation, invariants, determinism
- [ ] Run on Portland; review the report together; record results in this lesson
- [ ] Docs: D-022, D-011 amendment, ARCHITECTURE, log, status

## 12. Questions to think about before we start
1. Sunderland has 0% of residents within 10 minutes and Powellhurst-Gilbert 63.9%. Which is "worse served"? What decision would each ranking support?
2. Why can't you add up 93 neighborhood populations to get the city's population here? Name a Portland situation where a block truly belongs to two neighborhoods.
3. A neighborhood's zero-vehicle share is 9% ± 7%. What's its CV, and would you rank neighborhoods on it?
4. Car-free households are slightly *more* common where park access is better. Give one explanation that isn't causal, and say why the ecological fallacy forbids concluding anything about individual households.

## References
- U.S. Census Bureau (2020). *Understanding and Using American Community Survey Data: What All Data Users Need to Know*, ch. 8 (calculating MOEs for derived estimates).
- Openshaw, S. (1984). *The Modifiable Areal Unit Problem.* CATMOG 38.
- Robinson, W. S. (1950). *Ecological correlations and the behavior of individuals.* American Sociological Review 15(3).
- Talen, E. & Anselin, L. (1998). *Assessing spatial equity: an evaluation of measures of accessibility to public playgrounds.* Environment and Planning A 30(4).
