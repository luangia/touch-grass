# Project Plan — TouchGrass

> Status: **Stages 1–5 complete. Stage 6 implemented (2026-11-23), awaiting review.** Stages 7–11 not started.
> See [ARCHITECTURE.md](ARCHITECTURE.md) for design and [DECISIONS.md](DECISIONS.md) for the trade-offs behind it.

## 1. Problem

City parks are good for health, but "how many parks a city has" says little about whether **residents can actually walk to one**. A park 300 m away across a freeway with no crossing is, for a pedestrian, far away. Existing tools (e.g., Trust for Public Land's ParkServe "10-minute walk") answer this nationally with a fixed method; TouchGrass makes the method **transparent, adjustable, and neighborhood-focused**, and is built as a learning vehicle for GIS engineering.

## 2. Intended users

| User | Need |
|---|---|
| Residents / curious public | "How close is nature to where I live? How does my neighborhood compare?" |
| Planners, advocates, journalists | "Where do many people lack a 10-minute walk to a park?" with a defensible method |
| Hiring managers / reviewers (portfolio) | Evidence of correct spatial analysis, clean architecture, honest methodology |

## 3. MVP scope

**In scope (Portland city limits, D-001):**
1. Interactive map: parks, neighborhood boundaries, block-level walking-distance layer, population context.
2. Walking accessibility: network distance from each census block to the nearest park access point; 5/10/15-minute thresholds; straight-line distance as comparison.
3. Neighborhood metrics: population-weighted % within each threshold, weighted median distance, underserved population, park area per resident.
4. Neighborhood comparison: pick 2–4 neighborhoods, side-by-side metrics and charts; ranked table.
5. Underserved areas: ranked list + map highlight; ACS demographic context with clear "not causal" framing.
6. Methodology page: sources, parameters, assumptions, limitations.

**Non-goals (MVP):** tree canopy, heat, transit/bike access, mobility-impaired routing, slope-aware walking, park quality/amenities, historical comparison, user accounts, editing data, real-time routing from arbitrary clicked points, a second region (architecture supports it; not built).

**If time runs short, defer in this order:** (1) Stage 11 deployment → local demo only; (2) MVT tiles → simplified GeoJSON; (3) ACS demographics in Stage 7 → population-only underserved view; (4) OSM-vs-ORCA validation depth in Stage 3 → summary counts only.

## 4. Timeline overview

| # | Stage | Complexity | Learning difficulty | Est. effort |
|---|---|---|---|---|
| 1 | Foundations, environment & region config ✅ complete (2026-11-22) | Low | Low–Med | 0.5 day |
| 2 | Boundaries & population data (Census/TIGER) ✅ complete (2026-11-23) | Medium | Medium | 1 day |
| 3 | Parks & green space dataset ✅ complete (2026-11-23) | Medium | Medium | 1 day |
| 4 | PostGIS database & spatial SQL | Medium | **High** | 1 day |
| 5 | Pedestrian network & park access points | **High** | **High** | 1–1.5 days |
| 6 | Walking accessibility analysis | **High** | **High** | 1 day |
| 7 | Neighborhood aggregation & underserved metrics | Medium | Medium | 1 day |
| 8 | FastAPI backend | Medium | Medium | 1 day |
| 9 | Interactive map frontend | Medium–High | Medium | 1.5 days |
| 10 | Comparison, underserved view, methodology & polish | Medium | Low–Med | 1 day |
| 11 | Testing hardening, deployment & portfolio (optional) | Medium | Medium | 0.5–1 day |

Total ≈ 10–12 working days — at the top of the 1–2 week target. Stages 1–7 (the analysis) are the priority; a CLI + notebook demo after Stage 7 is already a meaningful result.

## 5. Stages

Each stage: objective, functional requirements, concepts, dependencies, tasks, deliverables, testing, acceptance criteria, definition of done. **Only one stage is implemented at a time, after explicit approval.**

---

### Stage 1 — Foundations, environment & region config

**Objective.** A runnable, empty skeleton with a validated region configuration and a local PostGIS instance.

**Functional requirements**
- `git init`, `.gitignore` (excludes `data/raw`, `data/processed`, env files).
- Python project (`pipeline/`) managed with `uv`; `ruff`, `mypy`, `pytest` configured.
- `docker-compose.yml` with a single PostGIS service.
- `config/regions/portland-or.yaml` + a Pydantic model that loads and validates it.
- `touchgrass` CLI with a `regions validate` command.

**Concepts:** vector vs raster data; GeoJSON / Shapefile / GeoPackage / GeoParquet; CRS fundamentals (EPSG codes, geographic vs projected, UTM); why configuration-driven design.

**Dependencies:** approval of D-001, D-004, D-008, D-009, D-012.

**Tasks:** repo layout; tooling; compose file; config schema (region_id, CRS, sources, analysis params); CLI entry point; first unit tests (config validation, CRS is projected & in meters via PyProj).

**Deliverables:** skeleton repo, config file, CLI, passing tests, `docs/learning/stage-01-*.md`.

**Testing:** pytest for config validation (valid file passes; missing CRS / geographic CRS rejected); `docker compose up` and `SELECT postgis_full_version();`.

**Acceptance criteria**
- `touchgrass regions validate portland-or` succeeds; an invalid config fails with a clear message.
- PostGIS reachable; version printed.
- `ruff`, `mypy`, `pytest` all pass.
- No Portland-specific value appears outside `config/`.

**Definition of done:** criteria met, docs + implementation log updated, learning doc written, results presented, stop.

---

### Stage 2 — Boundaries & population data (Census/TIGER)

**Objective.** Reproducibly acquire and clean study boundary, neighborhoods, census blocks with 2020 population, and block groups.

**Functional requirements**
- Source adapters: `arcgis_feature_service` (city layers), `tiger` (TIGER/Line shapefiles), `census_pl94171` (block population, via Census API or summary file), `census_acs5` (block-group tables, 2020–2024 vintage).
- Raw files + `manifest.json` (URL, time, SHA-256, license) per source.
- Clean: reproject, `make_valid`, block → study area by representative point, block ↔ neighborhood membership (many-to-many).
- Output GeoParquet.

**Concepts:** census geography hierarchy (state → county → tract → block group → block) and GEOIDs; TIGER/Line; ACS estimates vs decennial counts; margins of error; differential privacy noise; centroid vs `point_on_surface`; spatial joins (point-in-polygon); attribute joins on GEOID; overlapping polygons.

**Dependencies:** Stage 1; D-002, D-007.

**Tasks:** adapters; download command `touchgrass fetch --region`; cleaning functions in shared core; validation report.

**Deliverables:** `data/processed/portland-or/{boundary,neighborhoods,blocks,block_groups}.parquet`, validation report, learning doc.

**Testing:** unit tests on tiny synthetic polygons (membership rules, overlaps, boundary-straddling blocks); data checks: total population of in-city blocks within ±1% of the published 2020 city total (652,503); all geometries valid; CRS correct; every block in exactly one block group by GEOID prefix; neighborhood count matches source.

**Acceptance criteria:** pipeline re-run from scratch yields identical outputs (checksums of processed files, or row-level equality); population check passes; overlap behavior documented with real counts.

---

### Stage 3 — Parks & green space dataset

**Objective.** A clean, classified, defensible set of public parks/natural areas, with its quality measured.

**Functional requirements**
- OSM park adapter (via OSMnx `features_from_polygon` on the buffered boundary) with configurable inclusion/exclusion tags (D-003).
- Classification: `park`, `natural_area`, `garden`, `playground`, `recreation_ground`; exclude golf courses, cemeteries, private/no-access, school grounds unless explicitly public (rules documented).
- Dissolve fragments; compute area in analysis CRS; minimum-area filter.
- Validation against Metro ORCA or City park layer: % area overlap, parks only in one source, top discrepancies listed.

**Concepts:** OSM data model (nodes, ways, relations, tags); multipolygon relations; tag-based classification; geometry validity; unary union / dissolve; area in projected vs geographic CRS (and why Web Mercator lies); spatial intersection and overlay for validation; data licensing (ODbL share-alike).

**Dependencies:** Stage 2 (buffered boundary); D-003.

**Deliverables:** `parks.parquet`, validation report (markdown + map image), learning doc.

**Testing:** unit tests for tag rules on fixture features; checks: no invalid geometries, no park < min area, known major parks present by name (taken from source data, not hardcoded in code — test fixtures may name them); OSM vs authoritative overlap reported.

**Acceptance criteria:** inclusion rules are written in config + methodology; validation report quantifies agreement; you can explain why each excluded category was excluded.

---

### Stage 4 — PostGIS database & spatial SQL

> **Complete (2026-11-23, commit `5a55c70`).** Decisions in D-019; details and measured plans in [the lesson](learning/stage-04-postgis-spatial-sql.md) §13–14.

**Objective.** Persist processed data in a well-designed spatial schema and learn to query it efficiently.

**Functional requirements**
- Migrations creating tables from ARCHITECTURE §4 (regions, datasets, boundaries, analysis_areas, census_blocks, block_groups, area_membership, parks).
- Loader `touchgrass load --region` (idempotent upsert per region).
- GiST indexes; constraints (SRID, geometry type, FKs, unique keys).
- A `sql/explore/` set of documented example queries.

**Concepts:** PostGIS geometry vs geography types; SRIDs; GiST / R-tree internals (bounding boxes, two-phase filter & refine); `ST_Intersects`, `ST_Contains`, `ST_DWithin`, `ST_Distance`, `ST_Transform`, `ST_PointOnSurface`; `EXPLAIN ANALYZE`; index-only vs sequential scans; `&&` operator; KNN `<->` operator.

**Dependencies:** Stages 2–3.

**Deliverables:** migrations, loader, example queries with measured plans (with/without index), learning doc.

**Testing:** integration tests against the compose DB (load → counts match GeoParquet; spatial query results equal GeoPandas results for the same operation); re-load is idempotent.

**Acceptance criteria:** all data loaded with correct SRIDs; one spatial join demonstrated with `EXPLAIN ANALYZE` showing the GiST index used and the speedup measured; PostGIS and GeoPandas agree on block-in-neighborhood membership.

---

### Stage 5 — Pedestrian network & park access points

> **Complete (2026-11-23, commit `f03f451`).** Decisions in D-020 (and the D-006 amendment); results in [the lesson](learning/stage-05-walk-network-access-points.md) §13–14. The probes changed parts of the plan below: the network is built offline from the dated extract (not Overpass), foot-legal cycleways are re-admitted, origins snap to edges, and the sampled fallback is proposed for removal.

**Objective.** A clean, analysis-ready walking graph and a set of park access nodes.

**Functional requirements**
- Build graph with OSMnx (`network_type="walk"`) for the buffered boundary; project to analysis CRS; convert to undirected; keep largest strongly connected component (and report what was dropped).
- Save graph (GraphML or GeoPackage) under `data/processed/`.
- Park access points per D-006 (entrances, boundary-crossing nodes, sampled fallback), with `method` recorded.
- Block origins: snap representative points to nearest node; record snap distance; flag > `max_snap_m`.

**Concepts:** graphs (nodes, edges, weights, adjacency lists); how OSMnx simplifies topology; directed vs undirected for walking; connected components; nearest-neighbor search with KD-trees/R-trees; tolerance and snapping errors; what OSM "walk" includes (roads without sidewalks!) — an equity-relevant bias for East Portland.

**Dependencies:** Stages 2–4.

**Deliverables:** graph file, `park_access_points` table, snapping diagnostics (distribution of snap distances, flagged blocks map), learning doc.

**Testing:** synthetic grid graph fixtures; every park has ≥ 1 access point or is reported; snap distance median and p95 reported; spot-check 5 parks visually against OSM.

**Acceptance criteria:** graph connectivity report; ≥ 95% of populated blocks snap within 100 m (threshold to confirm once real data is seen); every excluded park/block is explained.

---

### Stage 6 — Walking accessibility analysis

> **Implemented 2026-11-23, awaiting review** (route check pending). Decisions in D-021; results in [the lesson](learning/stage-06-walking-accessibility.md) §13–14. The prototype changed parts of the plan below: SciPy instead of NetworkX, no cutoff, a same-edge rule, and two Stage 5 contact fixes found by the invariant.

**Objective.** For every populated block: network distance and time to the nearest park, plus straight-line distance.

**Functional requirements**
- Multi-source Dijkstra from all access nodes (edge weight = length in m), cutoff = max threshold × 2.
- `d_b = snap_dist + network_dist`; time = `d_b / speed`.
- Straight-line distance to nearest park polygon (`sjoin_nearest` in projected CRS).
- `analysis_runs` row with parameters; `block_access` rows.
- Detour ratio (network ÷ straight-line) as a diagnostic.

**Concepts:** Dijkstra's algorithm and priority queues (complexity O((V+E) log V)); why multi-source = one virtual super-source; cutoffs; network vs Euclidean distance; detour index; buffers vs service areas; reproducibility of analysis runs.

**Dependencies:** Stage 5.

**Deliverables:** `block_access` table, run record, diagnostic maps/plots (network vs straight-line scatter), learning doc.

**Testing:** hand-computable fixture graphs (known answers); property tests: network distance ≥ straight-line distance (minus snap tolerance) for every block; distance = 0 handling for blocks inside parks; results deterministic across runs.

**Acceptance criteria:** invariants hold for 100% of blocks (or violations explained); runtime recorded; 3 sample blocks verified manually with an independent route (e.g., OSMnx `shortest_path` or a web router).

---

### Stage 7 — Neighborhood aggregation & underserved metrics

**Objective.** Population-weighted metrics per analysis area, underserved identification, and demographic context.

**Functional requirements**
- `area_metrics` per area × threshold: pop_total, pop_within, pct_within, weighted median distance, underserved pop, park area & m² per resident.
- City-wide metrics computed from blocks (not from summing areas, because of overlaps).
- Priority ranking (D-011).
- ACS context per area: aggregate block-group estimates to areas (population-weighted via blocks) with approximate MOE propagation; documented caveats.
- Compare: population-weighted vs unweighted (area-average) result, to show why weighting matters.

**Concepts:** spatial aggregation; population weighting vs area weighting; modifiable areal unit problem (MAUP); ecological fallacy; ACS MOE and its approximate propagation formula; distinguishing disparity from causation.

**Dependencies:** Stage 6.

**Deliverables:** `area_metrics` table, CLI/notebook summary report (best/worst neighborhoods), learning doc.

**Testing:** fixtures with known weighted answers; invariants: 0 ≤ pct ≤ 100; pct(5) ≤ pct(10) ≤ pct(15); sum of pop_total over blocks equals city total.

**Acceptance criteria:** report lists neighborhoods with lowest 10-minute access with both % and absolute population; weighted vs unweighted difference demonstrated; caveat text drafted for the UI.

---

### Stage 8 — FastAPI backend

**Objective.** Read-only, typed API exposing regions, areas, metrics, parks, and block tiles.

**Functional requirements:** endpoints in ARCHITECTURE §6; Pydantic models; DB connection pooling; GeoJSON output; MVT via `ST_AsMVT` for blocks; methodology endpoint; CORS for the dev frontend.

**Concepts:** REST resource design; FastAPI dependency injection; Pydantic validation; GeoJSON spec (RFC 7946: lon/lat, WGS 84); vector tiles (MVT), tile coordinates (z/x/y), `ST_TileEnvelope`, `ST_AsMVTGeom`; geometry simplification for transport; SQL injection safety with parameters.

**Dependencies:** Stage 7.

**Deliverables:** `api/` service, OpenAPI docs, API tests, learning doc.

**Testing:** pytest + HTTPX against a test DB seeded with fixture data; contract tests (schemas); unknown region → 404; tile endpoint returns valid MVT for a tile over the city and empty for a tile outside.

**Acceptance criteria:** all endpoints documented and tested; no region-specific constants in API code; p95 response for area list < 500 ms locally.

---

### Stage 9 — Interactive map frontend

**Objective.** A MapLibre map that explores parks, neighborhoods, and walking access.

**Functional requirements:** React + TS + Vite app; region loaded from API (bbox → initial view); layers: basemap, parks, neighborhoods, block access choropleth (5/10/15 bins); threshold toggle; hover/click neighborhood → side panel metrics; legend; OSM attribution.

**Concepts:** web map rendering (WebGL, tiles, styles); sources vs layers in MapLibre; data-driven styling expressions; Web Mercator display projection; GeoJSON vs vector tile sources; feature state for hover; accessible color scales (sequential, colorblind-safe).

**Dependencies:** Stage 8.

**Deliverables:** `web/` app, typed API client, component tests, learning doc.

**Testing:** TypeScript strict mode; unit tests for data → style mapping; manual browser verification checklist (pan/zoom/click; layer toggles; no console errors).

**Acceptance criteria:** a user can find their neighborhood, see its 10-minute share, and see which blocks are beyond 10 minutes; no hardcoded coordinates or neighborhood names in `web/`.

---

### Stage 10 — Comparison, underserved view, methodology & polish

**Objective.** Answer the project's questions clearly in the UI.

**Functional requirements:** compare 2–4 neighborhoods (table + bar chart across thresholds); ranked underserved table linked to map; network vs straight-line explainer (e.g., chart or highlighted examples); methodology page from API; demographic context with "observed, not causal" language.

**Concepts:** information design for maps & charts; communicating uncertainty; responsible framing of equity data.

**Dependencies:** Stage 9.

**Acceptance criteria:** each question in README "Questions" section is answerable from the UI; methodology lists every source, license, parameter, and limitation.

---

### Stage 11 — Testing hardening, deployment & portfolio (optional within the 2-week window)

**Objective.** Ship a reviewable, reproducible portfolio project.

**Tasks:** end-to-end test (pipeline on a tiny fixture region → API → UI smoke test); CI (GitHub Actions: lint, type-check, tests); deployment choice (managed PostGIS + API host, or static export); final README, screenshots, architecture diagram; `/portfolio` materials.

**Concepts:** CI pipelines; environment configuration; deploying stateful vs static apps; cost trade-offs.

**Acceptance criteria:** a fresh clone can reproduce results by following README; CI green; (if deployed) public URL works.

## 6. Cross-cutting testing strategy

| Level | What | Tooling |
|---|---|---|
| Unit | Pure spatial functions on tiny synthetic geometries/graphs with hand-computed answers | pytest, Shapely |
| Data validation | Counts, totals, CRS, validity, ranges, monotonicity invariants on real data | pytest-style checks in pipeline (`touchgrass validate`) |
| Integration | Load into PostGIS, compare SQL vs GeoPandas results | pytest + compose DB |
| API | Endpoint contracts, error cases | pytest + HTTPX |
| Frontend | Style/data mappings, components | Vitest |
| Manual | Visual spot checks against OSM / independent router | Checklists in learning docs |

Correctness guards that run on every pipeline execution: projected CRS for all measurements; network ≥ straight-line distance; pct monotonic across thresholds; population totals reconcile.

## 7. Key risks & assumptions

| Risk / assumption | Impact | Mitigation |
|---|---|---|
| OSM `walk` network includes streets without sidewalks | Overstates access where walking is unpleasant/unsafe (notably parts of East Portland) | Document; future: sidewalk-aware weighting using City sidewalk data |
| Park access points approximated | Fenced parks look more accessible than they are | Prefer tagged entrances; document; spot-check large parks |
| 2020 block populations are 6 years old | New housing missed | Document; future: ACS or housing-unit updates |
| Differential privacy noise at block level | Single-block values unreliable | Report at neighborhood level only |
| Hills ignored (West Hills) | Time underestimated on steep routes | Document; future: slope-aware speed with a DEM |
| Uniform walking speed (4.8 km/h) | Overstates access for older adults/children | Config parameter; future: sensitivity analysis |
| Park "quality" ignored | A tiny pocket park counts the same as a large natural area | Report park area per resident; future: size-weighted access |
| Neighborhood overlaps | Double-counting if summed | Many-to-many membership; city totals from blocks |
| Data source URLs/licences change | Pipeline breaks | Manifests, cached raw data, adapters isolate changes |
| Scope vs 1–2 week target | Late finish | Deferral order in §3 |
