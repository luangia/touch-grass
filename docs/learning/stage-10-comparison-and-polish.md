# Stage 10: Comparison, Underserved View, Methodology and Polish

> Status: **implemented 2026-11-24, awaiting your review.** §13 is what was built, §14 the results and browser check, §15 the answers to §12, and §16 where the build departed from the plan.
> Numbers marked **(prototype 2026-11-23)** come from throwaway SQL against the loaded dev database (run `7c1fc6efaf00effa`) in the scratch area. No project code was changed.

## 1. Objective and expected outcome

By the end of this stage, **every question in the README is answerable from the UI**, and the methodology lists every source, **licence**, parameter and limitation.

| README question | Today | After Stage 10 |
|---|---|---|
| How accessible are parks to residents? | About page figures; area panel | + a city summary on the map's start panel |
| Which neighborhoods have the best and worst access? | Only by clicking around | **Rankings page**: lowest share, most underserved people, priority; linked to the map |
| How far must someone walk on the real network? | Block view + panel | (kept) |
| How does walking distance differ from straight-line distance? | **Not in the UI** | Straight-line share beside the walking share per area; per block "1,222 m walk vs 745 m straight (1.6×)"; a "where barriers cost the most" ranking |
| Which neighborhoods have many residents beyond 10 minutes? | Panel number only | Rankings by underserved residents |
| Are there demographic disparities? (observed, not causal) | **Not in the UI** (the API has it) | Census context per area with ± and reliability; a city-level comparison of residents beyond vs within a 10-minute walk, with margins of error |

Plus a **Compare** view for 2–4 areas (table and bar chart across thresholds).

## 2. Why this stage exists

Stage 9 made the map work. This stage makes the project **answer its own questions** without the reader hunting. The two missing answers, straight-line vs network and demographics, are also the two most often misread. The first is often dismissed ("everyone's near a park"). The second gets over-interpreted ("parks cause..."). So this stage is as much about **framing and uncertainty** as about features.

## 3. Dependencies

- **Stages 6–9:**
  - per-block `straight_m` and `detour_ratio` (Stage 6), stored but never shown;
  - area metrics and ACS context with MOE and reliability (Stage 7);
  - `/rankings`, `/compare` and `/methodology` (Stage 8);
  - the map, URL state, About page and design tokens (Stage 9).
- **Decisions:** D-011 (context, not cause), D-022 (rankings: floor of 1,000 residents; ACS never ranked; low reliability shown but not ranked), D-024 (palette, URL state, two views).

## 4. What the prototype found (prototype 2026-11-23)

| Question | Found |
|---|---|
| City: walking route vs straight line | Within 5 / 10 / 15 min: **43.7 / 85.0 / 97.5%** by walking route vs **67.6 / 97.2 / 100%** by straight line |
| Per neighborhood (≥ 1,000 residents, 10 min) | Median gap **7.4 points**. Largest: **Sylvan-Highlands 32.8% vs 98.1% (65 points)**, Maplewood 47.5, North Tabor 42.9, Sumner 41.8, Parkrose 41.6, Arnold Creek 35.8. **20 of 87** neighborhoods show no gap |
| Per block detour (route ÷ straight line) | Median 1.35, 90th percentile 2.05, 99th 4.26 |
| Neighborhood correlation, access vs demographics (10 min) | Zero-vehicle households **+0.27** (+0.28 using only the 48 not-low-reliability areas); people of color **−0.14**. Weak, and an ecological comparison |
| **Residents** beyond vs within 10 min (each block group's ACS shares spread evenly over its blocks, as in D-022 Q5) | People of color: **37.7%** of residents beyond vs **32.9%** within. Zero-vehicle households: **9.7%** beyond vs **12.4%** within (car-free households are *more* often within a walk). **No MOE yet** |
| Licences in the database | **None.** `datasets` stores attribution text and URLs but no licence, so the acceptance criterion "lists every licence" can't be met from data today |

## 5. Concepts

### 5.1 Answering questions, not showing data
Each view is built around one question, phrased as its title. A ranking table titled "Neighborhoods with the most residents beyond a 10-minute walk" beats a sortable grid of every column: the reader knows what the order means.

### 5.2 Rankings that don't mislead (D-022)
- **Share vs count:** two different lists, never one blended score. The rate list applies the 1,000-resident floor; the count list doesn't need it.
- **Ties and near-ties:** 40 neighborhoods sit at 95–100% within 10 minutes. Ranking them 1–40 invents precision. Show the share and let equal values look equal; never print a rank for values that round the same.
- **Linked to the map:** each row opens the map on that area, so a number is never far from its place.

### 5.3 Straight line vs walking route: making the barrier visible
- **Per area:** a ghost bar for the straight-line share behind the solid walking-route bar. The gap *is* the cost of barriers (freeways, rivers, hills, dead-end streets).
- **Per block:** "1,222 m to Frazer Park on foot, 745 m in a straight line, 1.6× longer".
- **"Where barriers cost the most":** areas ranked by the gap. Sylvan-Highlands (65 points) is a story the network analysis tells and a buffer analysis can't.
- The straight-line distance is to the nearest park *polygon* (D-021 Q5), so it's a floor, not a realistic walk.

### 5.4 Demographic context without causal claims (D-011)
- **Per area:** zero-vehicle households and people of color, as "**38% ± 6%**", with the reliability label. Low-reliability values are greyed and never ranked (D-022 Q7).
- **Why not a scatter plot of neighborhoods?** It invites the ecological fallacy: "areas with more X have worse access" reads as "people who are X have worse access". It also mixes reliable and unreliable estimates, and overlapping neighborhoods double-count.
- **The better question is about residents:** of the people beyond a 10-minute walk, what share are people of color, compared with people within it? That's an *exposure* comparison, weighted by where people live.
  - **Assumption:** a block group's composition is spread evenly over its blocks (the same assumption D-022 Q5 already makes).
  - **Margins of error:** without one, a 4.8-point difference can't be called real or noise.
- **Wording template:** "Among residents farther than a 10-minute walk, 38% (± X) are people of color, compared with 33% (± Y) among residents within it. This describes where people live; it doesn't show why."

### 5.5 Margin of error for the resident comparison
Each side is a weighted sum over block groups: X = Σ_g w_g · x_g, with w_g = the share of g's 2020 population on that side. That has the same form as the D-022 allocation, so the same approximations apply:
- MOE(X) = √Σ (w_g · MOE_g)², with the zero-estimate rule;
- the proportion formula for the share;
- MOE of the difference ≈ √(MOE₁² + MOE₂²). This is conservative, since the two sides share some block groups.

When the difference is smaller than its MOE, the UI says "**no clear difference**" rather than printing the gap as if it were meaningful.

### 5.6 Small charts, honestly drawn
- **Bars start at zero;** shares share one 0–100% axis; labels sit on the bars (no legend hunt).
- **Grouped bars for comparison:** areas × thresholds, at most 4 × 3 = 12 bars.
- **Hand-written SVG:** a few dozen lines per chart, fully themeable with the CSS tokens, testable, and no chart dependency (Q2).

### 5.7 Licences are data too
- **Attribution vs licence:** attribution says *who* to credit; a licence says *what you may do*. Both belong in the methodology.
- **Where licences come from:**
  - fixed per adapter: Census TIGER/ACS = U.S. Government work, public domain; OpenStreetMap = ODbL 1.0;
  - per configured source in the region YAML: City of Portland data, and Metro RLIS = its Open Database License.
- They flow config → `datasets.license_*` (migration 0009) → `/methodology` → About.

## 6. How the concepts relate

```
pipeline metrics ──► + straight-line share per (area, threshold)      ┐
                 ──► + resident disparity (beyond vs within) with MOE ├─ migration 0009
config + adapters ─► + licence per source → datasets                 ┘
        ▼
API: /areas, /metrics, /compare carry straight-line shares; tiles carry straight_m & detour;
     /rankings gains "barrier_gap"; /methodology gains licences + disparity
        ▼
web: Rankings page ─ click ─► Map (area selected) ◄─ "Add to compare" ─► Compare page
     Panel: walking vs straight-line bars, census context (±, reliability), block detour
     About: full methodology (parameters table, licences, disparity paragraph with ±)
```

## 7. Fit in the architecture
- **pipeline:**
  - `core/aggregate.py`: straight-line shares;
  - `core/acs_alloc.py`: resident-weighted shares by side, with MOE;
  - `pipeline/metrics.py`: outputs, checks, report section;
  - `sources/*`: licence per adapter;
  - `config/models.py`: `license` on configured sources;
  - `db/migrations/0009_*.sql`: `area_thresholds.pct_within_straight`, `datasets.license_name/url`, `metrics_runs` disparity in its summary.
- **api:**
  - models and queries for the new fields;
  - `RankMetric.barrier_gap`;
  - tile attributes `straight_m` and `detour_ratio`;
  - `EXPECTED_SCHEMA_VERSION = 9`; `RESPONSE_VERSION = 3`.
- **web:**
  - `pages/Rankings`, `pages/Compare`, `components/charts/*` (SVG), `components/Context`;
  - About becomes the full methodology;
  - the header nav: Map · Rankings · Compare · About.

## 8. Implementation approach
1. **Pipeline:** straight-line shares (city and areas) with checks:
   - straight share ≥ walking share everywhere (the D-021 invariant, aggregated);
   - city values equal the Stage 6 summary.

   Then the resident disparity with MOE, tested against hand-computed fixtures. Then licences (config + adapters), migration 0009, the loader, and a rerun of `metrics` and `load`.
2. **API:** the new fields; the `barrier_gap` ranking; tile attributes; licences and disparity in `/methodology`; tests; version bumps.
3. **Web:**
   - the Rankings page (three lists + barrier gap, linked to the map);
   - Compare (picker from panel and rankings; table + grouped bars; URL `?areas=`);
   - panel walking vs straight-line ghost bars, block detour line, census context section;
   - About: a parameters table, licences, the disparity paragraph with ± or "no clear difference";
   - a city summary on the map's start panel.
4. **Copy pass:** every chart and number has a one-line title stating its question. Caveats sit next to the demographic numbers, not only on About.
5. **Tests** (pipeline, API, web) and a browser check in both themes at both widths.

### 8.1 Testing strategy
- **Pipeline:** hand-computed straight-line shares; the invariant straight ≥ walking; the disparity on a two-block-group fixture with a known MOE; licences present for every source (hard check).
- **API:** contracts for the new fields; `barrier_gap` ordering; schema 9; the ETag version.
- **Web:**
  - chart scale functions (zero baseline, 0–100% domain);
  - Rankings rows link to `/?area=`;
  - Compare picker limits (2–4) and URL round trip;
  - context greys low reliability and never sorts by it;
  - the disparity sentence switches to "no clear difference" when |diff| ≤ MOE.
- **Guard:** no region constants anywhere.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | Navigation | **Header: Map · Rankings · Compare · About** (Rankings and Compare as pages with their own URLs) · everything in the map panel | **Pages**: each answers its question at full width; linked both ways |
| Q2 | Charts | **Hand-written SVG** (bars, grouped bars) · Observable Plot / Recharts | **SVG**: a handful of simple charts; themeable; no dependency |
| Q3 | Straight-line explainer | **(a)** ghost bars per area · **(b)** per-block detour line · **(c)** "barrier cost" ranking · **(d)** city figures on About | **All four** (needs the pipeline + migration 0009 + API) |
| Q4 | Demographics in the UI | **Per-area context (± and reliability) + a city-level resident comparison (beyond vs within) with MOE** · also a neighborhood scatter | **No scatter** (ecological fallacy, mixed reliability) |
| Q5 | Disparity uncertainty | **Approximate ACS MOE propagation; "no clear difference" when \|diff\| ≤ MOE** · bootstrap · no MOE | **ACS propagation**, consistent with D-022 |
| Q6 | Compare | **2–4 areas, any type; table + grouped bars across thresholds; context rows with reliability; URL `?areas=`** · 2 only | **2–4** (as planned) |
| Q7 | Rankings content | **Lowest share (floor), most underserved residents, priority, largest barrier gap (floor); top 10, then "show all"; ties shown as ties** · one sortable table | **Four titled lists** |
| Q8 | Licences | **Per-adapter defaults (Census = public domain, OSM = ODbL) + `license` on configured sources; stored and served; hard check that every source has one** · hand-written list on About | **From data**, so a new city can't forget them |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)
- [ ] Each README question is answered by a named view (table in §1), checked in the browser.
- [ ] About lists every source with its **licence**, every analysis and metrics parameter, and every caveat, all from the API.
- [ ] Straight-line shares: pipeline hard checks (≥ walking share; city equals the Stage 6 summary).
- [ ] Disparity with MOE; "no clear difference" logic tested; wording reviewed with you.
- [ ] Compare: 2–4 areas, URL round trip; Rankings linked to the map.
- [ ] All tests (pipeline, API, web); both themes; phone width; no console errors.
- [ ] Docs: D-025 (Q1–Q8), lesson "what was built", README "Questions" linked to views.

## 11. Proposed implementation checklist
- [ ] Pipeline: straight-line shares + checks; resident disparity with MOE + tests; licences in config and adapters + hard check
- [ ] Migration 0009; loader; rerun `metrics` + `load`
- [ ] API: new fields, `barrier_gap`, tile attributes, licences and disparity in `/methodology`; schema 9; response version 3; tests
- [ ] Web: nav; Rankings; Compare; SVG charts; panel ghost bars, block detour, census context; About as full methodology; map start panel city summary
- [ ] Tests (all three projects), guard, browser check (light/dark, desktop/phone)
- [ ] Docs: D-025, README, ARCHITECTURE, log, status, lesson §13+

## 12. Questions to think about before we start
1. Sylvan-Highlands is 98% within 10 minutes as the crow flies and 33% by walking route. What kinds of barriers produce a gap like that, and would a buffer-based study ever see it?
2. Car-free households are *more* common among residents within a 10-minute walk. Name one non-causal reason.
3. A 4.8-point difference with a ± 5-point MOE: what should the UI say, and why is "no clear difference" not the same as "no difference"?
4. Why rank by share and by count separately instead of combining them into one score?

## References
- Census Bureau, *Understanding and Using ACS Data* (2020), ch. 8.
- Robinson (1950), ecological correlations.
- Tufte, *The Visual Display of Quantitative Information*: data-ink and zero baselines.
- Open Data Commons, ODbL 1.0; U.S. Census Bureau data use (public domain).

## 13. What was built (2026-11-24)

| Layer | Pieces |
|---|---|
| **Pipeline** | |
| Straight-line shares | `aggregate.summarize_areas` adds `pop_within_straight` / `pct_within_straight` per area × threshold. Hard checks: never below the walking share; the city equals Stage 6's own summary (±0.00005) |
| Residents beyond vs within | `acs_alloc.compare_sides` treats each side as an area in the D-022 allocation, so it inherits the MOE propagation. Difference MOE = √(MOE² + MOE²); `clear` = \|diff\| > MOE. Hard check: the two sides cover every resident. Stored in `metrics_runs.summary.resident_comparison`, and in the report |
| Licences | `SourceLicense` (name + optional https URL) is **required** on every configured ArcGIS source. Adapters set Census = public domain and OSM = ODbL 1.0. The loader refuses a source without a licence. Portland: RLIS EULA (boundary, ORCA) and the City Data Distribution Policy (neighborhoods, districts) |
| Migration 0009 | `area_thresholds.pop_within_straight` / `pct_within_straight` (CHECK ≥ walking share), `datasets.license_name` / `license_url` |
| **API** | |
| Schema and caching | Schema 9; `RESPONSE_VERSION = 3` |
| New fields | Straight-line shares in `/areas`, metrics and compare; tiles carry `straight_m` and `detour_ratio`; `/methodology` carries licences and the resident comparison |
| Rankings | New `barrier_gap` metric (rate, floored); `underserved_pop` and `priority_score` stay tied to the underserved threshold |
| **Web** | |
| Navigation | Header: Map · Rankings · Compare (with a count of picks) · About |
| `RankingsPage` | Four titled lists, ties shown as "=", rows open the map |
| `ComparePage` | Picker, chips, "+ Whole city", grouped SVG bars, a table with walking · straight-line shares and census context (low greyed); `?compare=` in the URL |
| `charts.tsx` | Walking vs straight-line bars; grouped bars |
| `context.tsx` | Context list with ± and reliability badges, never sorted; comparison sentences with "no clear difference" |
| Panel | Ghost bars, census context, "+ Add to compare", the block detour line, and the city summary on the start panel |
| About | Straight-line table, "Who lives farther from parks", a licence per source, a parameters table plus every raw parameter |
| **Tests** | Pipeline 247 (+5), API 39 (+4), web 47 (+11: ties, chart scale, wording, context, detour, parameters, Rankings, Compare) |

**Found while building:**
- **Generated types caught drift.** Regenerating the API types made the web test fixtures fail to compile (missing licence and straight-line fields): the contract working as intended.
- **The car-free sentence was wrong at first.** "9.8% of residents" misstates a *household* share. The sentence is now neutral ("9.8% where residents live farther than…"), with the difference in percentage points.
- **The prototype's car-free figures were off.** It averaged block-group shares weighted by people (12.4% within); allocating household counts gives 14.3% within, 9.8% beyond.
- **A clipped chart label:** the longest bar's label ran off the chart; the right margin is now wider.

## 14. Results on Portland (2026-11-24, run `7c1fc6efaf00effa`)

| | |
|---|---|
| Straight line vs walking route (city) | 5 / 10 / 15 min: 67.6 / 97.2 / 100.0% vs 43.7 / 85.0 / 97.5% |
| Where barriers cost the most (10 min) | Sylvan-Highlands 65 points (33% vs 98%), Maplewood 48, North Tabor 43, Sumner 42, Parkrose 42 |
| Residents farther than vs within 10 min | People of color **38.5% ± 1.2 vs 33.3% ± 0.6: +5.2 ± 1.3 points, clear**. Households with no vehicle **9.8% ± 1.0 vs 14.3% ± 0.6: −4.5 ± 1.2 points, clear** |
| Licences | All 11 sources: public domain (TIGER, ACS), ODbL 1.0 (OSM), RLIS EULA (city boundary, ORCA), City Data Distribution Policy (neighborhoods, districts) |

**Browser check (light and dark, pane at 700 px, no console errors):**
- Rankings: four lists; clicking Sylvan-Highlands opens the map there, with the panel showing 33% · 98%.
- "+ Add to compare", then the Compare page: the nav badge shows 2; the chart and table appear; chips can be removed.
- About: all sections, licences and parameters.
- Block view, dark mode: "1,222 m to Frazer Park … 921 m in a straight line: the walk is 1.3× as long".

### Review for you (acceptance)
1. Can you answer each README question from the app? The README now links each question to the view that answers it.
2. Read "Who lives farther from parks" on About. Is the wording right: neutral, with margins, and "not why"?
3. Rankings: are the four list titles clear?

## 15. Answers to §12
1. **Sylvan-Highlands (98% vs 33%):** a hillside neighborhood with curving streets, few through-connections, and Highway 26 along its edge. A park can be close as the crow flies and still a long walk: the street pattern, not the distance, decides. A buffer analysis (a circle around each park) can't see this at all.
2. **Car-free households live closer to parks:** a non-causal reason is that they concentrate in dense inner neighborhoods with older street grids, transit and small parks. Density drives both.
3. **4.8 ± 5 points:** the UI says "no clear difference: the gap is within the margin of error". That doesn't mean "no difference": the true gap could be 0 or nearly 10. The data can't tell, and saying so is the honest result.
4. **Share and count separately:** a share (rate) and a count (scale) answer different questions: "where is it worst?" and "where are the most people affected?". Any combined score hides a weighting choice the reader can't see. Two titled lists make the choice explicit.

## 16. Departures from the plan
- **The resident comparison lives in the metrics run summary** (served by `/methodology`), not a new table. It's a handful of numbers per run.
- **Straight-line shares for old runs are NULL** (the column is nullable); `metrics` must be rerun to fill them, which it was for the latest run.
- **Compare picks come from the current area type plus the whole city.** Mixing types works through the URL but has no picker.

