# Engineering Decisions — TouchGrass

Each decision records context, options, the choice, and consequences.
Status values: **Proposed** (awaiting approval) · **Accepted** · **Superseded**.

All decisions D-001…D-012 were **Accepted** by the project owner on 2026-11-22. Stage 11 (deployment) stays optional, done only if time allows.

---

## D-001 — MVP study area: City of Portland limits, with an analysis buffer

**Status:** Accepted (2026-11-22)

**Context.** "Portland" can mean three different things:

| Option | Population (approx.) | Jurisdictions | Neighborhood boundaries | Processing size |
|---|---|---|---|---|
| City of Portland limits | ~630–650k (2020 Census: 652,503) | 1 city | Official neighborhood association boundaries (City of Portland) | Small: walk network fits easily in memory |
| Metro Urban Growth Boundary (3 OR counties) | ~3× the city | ~24 cities + unincorporated areas | No single neighborhood system; each city differs | Medium: 3–5× the network size |
| Portland–Vancouver–Hillsboro MSA (7 counties, OR + WA) | ~2.5M | 2 states, many cities, rural land | None common | Large; rural areas make "walking access" less meaningful |

**Decision (recommended).** Use **City of Portland limits** as the *reporting* area for the MVP, but pull walking network and parks from a **buffered** area (default 2 km beyond the boundary).

**Why.**
- Portland has an official, maintained neighborhood boundary dataset — the core unit of Feature 3/4. The metro area does not have one consistent equivalent.
- One jurisdiction keeps data cleaning and validation tractable within 1–2 weeks.
- The buffer prevents an **edge effect**: a resident near the Beaverton or Gresham line may walk to a park outside the city. Ignoring it would wrongly mark them as underserved.
- Census geographies (blocks, block groups) and OSM cover the whole area, so moving to the metro region later is a configuration change, not a rewrite (see D-004).

**Consequences.**
- Population is counted only inside the city boundary; parks/network outside it are destinations/paths only.
- Census blocks that straddle the city boundary need a rule (centroid-in-boundary; see D-007).
- Natural expansion path: "Portland Metro UGB" as a second region using census tracts or city boundaries as its analysis areas.

---

## D-002 — Population unit: 2020 Census blocks for weighting, ACS block groups for demographics

**Status:** Accepted (2026-11-22)

**Context.** Population-weighted accessibility needs population at a fine spatial resolution. Options:

| Source | Smallest geography | Measures | Notes |
|---|---|---|---|
| 2020 Decennial Census (P.L. 94-171 redistricting file, table P1) | **Block** (~city block) | Actual count (with differential-privacy noise) | 2020 counts; ageing by 2026 |
| ACS 5-year 2020–2024 (released 2026-01-29) | Block group (select detailed tables) | Estimates with margins of error | More recent and richer, but coarser and noisy |
| Gridded/dasymetric population (e.g., residential parcels) | Parcel | Modeled | More work; region-specific |

**Decision (recommended).** Use **2020 census blocks** as accessibility *origins* (population points), and **ACS 2020–2024 block groups** only for demographic context (Feature 5).

**Why.** A block group can be ~0.5 km across — the same order as a 5-minute walk (400 m). Using block-group centroids would blur the analysis at exactly the scale we measure. Blocks are small enough that "the block's representative point" is a reasonable approximation of where its residents live.

**Consequences.**
- Block populations are 2020 counts. New housing since 2020 (e.g., denser corridors) is missed. Documented as a limitation.
- Differential privacy adds noise at block level; aggregates (neighborhoods) are much more reliable than single blocks. We will only *display* block results as a spatial pattern, and *report numbers* at neighborhood level.
- ACS estimates carry margins of error; we must show/consider them, and must not over-interpret small differences.

---

## D-003 — Primary park source: OpenStreetMap, validated against an authoritative local layer

**Status:** Accepted (2026-11-22) — OSM primary, ORCA for validation

**Options.**
1. **OSM** (`leisure=park`, `leisure=nature_reserve`, etc.). Same source and code path for every future city; also provides entrances and the walk network. Quality varies by city (Portland's is high — Metro's RLIS was historically imported into OSM). Classification is tag-based and needs careful rules.
2. **Metro ORCA** (Outdoor Recreation and Conservation Areas) or **City of Portland park boundaries**. Authoritative, includes ownership/access attributes. Portland-only; each future city needs a new adapter. License for ORCA must be confirmed (RLIS Discovery open-data terms appear to be ODbL/DbCL-based — to be verified in Stage 3).

**Decision (recommended).** OSM as the **primary** source (portable), with a pluggable "park source adapter" so a region *can* use an authoritative layer. In Stage 3, **quantitatively compare** OSM parks against ORCA/City parks (area overlap, missing/extra parks) and document the result.

**Why.** It satisfies the multi-region requirement with a single ingestion path, while the comparison step gives us evidence about correctness rather than assuming it.

**Alternative if you prefer maximum local accuracy:** Use ORCA as primary for Portland and OSM only for entrances. Costs ~1 extra day of adapter and attribute mapping work.

---

## D-004 — Multi-region model: configuration-driven regions, shared tables keyed by `region_id`

**Status:** Accepted (2026-11-22)

**Options.**
- **Schema per region** (`portland.parks`, `seattle.parks`): strong isolation; cross-region queries and migrations are painful; schema drift.
- **Database per region:** heavy for an MVP.
- **Shared tables with a `region_id` column** (+ composite indexes): one schema, one migration path, easy cross-region comparison.

**Decision.** Shared tables with `region_id`; each region described by a YAML config file (`config/regions/<region_id>.yaml`) declaring boundary source, analysis-area sources, CRS, parks source adapter, and analysis parameters. Neighborhoods are generalized as **analysis areas** with an `area_type` (`neighborhood`, `census_tract`, `council_district`, …) so cities without neighborhoods still work.

**Consequences.** Every query and API route takes `region_id`. Indexes lead with `region_id` where helpful. A future city = new YAML + data sources + run pipeline.

---

## D-005 — Accessibility method: multi-source Dijkstra from park access points over the OSM walk network

**Status:** Accepted (2026-11-22)

**Options.**
- Straight-line buffers (fast, wrong in cities with rivers, freeways, cul-de-sacs, hills).
- One shortest-path query per origin (correct, slow: ~10k+ origins).
- **Multi-source Dijkstra** seeded at all park access nodes simultaneously (correct and fast: one graph traversal gives every node its distance to the *nearest* park).
- Dedicated engines (r5py, pandana contraction hierarchies, pgRouting, OSRM/Valhalla isochrones).

**Decision.** Multi-source Dijkstra (NetworkX `multi_source_dijkstra`) on an undirected OSMnx `walk` graph. Straight-line distance computed alongside as a **comparison metric only**. Pandana/r5py noted as future optimization.

**Parameters (configurable per region):** walking speed 4.8 km/h (80 m/min) → 5/10/15 min ≈ 400/800/1200 m.
Origin snapping uses the nearest network node plus the straight-line connector distance; origins > 500 m from any network node are flagged.

---

## D-006 — Park access points: OSM entrances where tagged, otherwise boundary–network intersections

**Status:** Accepted (2026-11-22)

Treating a park's centroid as its destination is wrong for large parks (Forest Park is >5,000 acres). Treating every boundary vertex as an entrance is wrong for fenced parks or parks behind freeways.

**Decision.** Access points = union of (a) OSM `entrance=*` / gate nodes on the park boundary, (b) walk-network nodes where a path or street crosses into or touches the park boundary (within a small tolerance, default 15 m), and (c) a fallback of boundary points sampled every 50 m snapped to the network only if (a) and (b) produce nothing. The approach is documented as an approximation; fenced-off edges are a known limitation.

---

## D-007 — Spatial assignment rules

**Status:** Accepted (2026-11-22)

- **Block → study area / analysis area:** block's *representative point* is within the polygon. *Amended 2026-11-22 (D-015 Q6):* the representative point is the Census internal point (`INTPTLON20`/`INTPTLAT20`). It's checked to lie inside its block, with GEOS `point_on_surface` as a fallback. Either way the point is guaranteed inside the polygon, unlike a centroid.
- **Overlapping neighborhoods:** Portland neighborhood boundaries include shared/overlapping areas. A many-to-many `area_membership` table lets a block belong to more than one neighborhood; overlaps are counted in each, and city-wide totals are computed from blocks directly (never by summing neighborhoods).
- **ACS block group → block:** blocks nest inside block groups by GEOID prefix (first 12 digits), so this is an attribute join, not a spatial one.

---

## D-008 — Coordinate reference systems

**Status:** Accepted (2026-11-22)

- **Storage / API / web map:** EPSG:4326 (WGS 84 lon/lat). MapLibre reprojects to Web Mercator (EPSG:3857) for display.
- **Analysis (distances, areas, buffers):** a projected CRS declared per region. Portland: **EPSG:26910 (NAD83 / UTM zone 10N, meters)**. Oregon State Plane North (EPSG:2913/2269) is in feet — avoided to keep units consistent across regions.
- Never compute distances/areas in EPSG:4326 or EPSG:3857 (Web Mercator inflates areas ≈2.0× at Portland’s latitude: 1/cos²(45.5°)).

---

## D-009 — Technology stack

**Status:** Accepted (2026-11-22)

| Layer | Choice | Main alternative | Why this choice |
|---|---|---|---|
| Frontend | React + TypeScript + Vite | Plain JS, Svelte | Your preference; typed API contracts; Vite is minimal |
| Map | MapLibre GL JS | Leaflet, deck.gl, OpenLayers | Open-source, WebGL, native vector tiles + GeoJSON |
| Charts | A small library (Recharts or Observable Plot) | D3 directly | D3 is more to learn than this MVP needs |
| Basemap | Free OSM-based vector style (e.g., OpenFreeMap) — to confirm in Stage 9 | Self-hosted PMTiles | No API key, no infra |
| API | FastAPI + Pydantic | Django + GeoDjango | Light, typed, auto OpenAPI docs |
| DB | PostgreSQL + PostGIS (Docker) | SpatiaLite/DuckDB-spatial | Industry standard; GiST indexes; `ST_AsMVT` |
| DB access | SQLAlchemy Core + GeoAlchemy2 (or raw SQL via psycopg) | Full ORM | Spatial SQL stays visible for learning |
| Processing | GeoPandas, Shapely 2, PyProj, OSMnx 2.x, NetworkX | pandana, r5py | Standard Python GIS stack; readable |
| Storage of intermediates | GeoParquet | Shapefile, GeoJSON | Typed, compressed, fast; no 10-char column limit |
| Python tooling | uv, pytest, ruff, mypy | pip/poetry | Fast, single tool |
| Infra | `docker compose` with only PostGIS | Kubernetes, tile servers | Avoid unnecessary services |

Deferred: dedicated tile server (Martin/pg_tileserv), task queues, caching layers, auth.

---

## D-010 — Data delivery to the map: GeoJSON first, MVT via PostGIS for dense layers

**Status:** Accepted (2026-11-22)

Neighborhoods (~95 polygons) and parks (~few hundred) are small → GeoJSON from FastAPI.
Census blocks (~10k+ polygons in the city) are heavier → serve **Mapbox Vector Tiles generated by PostGIS `ST_AsMVT`** through a FastAPI route. No extra tile server; good learning value. If time is short, fall back to simplified GeoJSON.

---

## D-011 — Underserved definition (descriptive, not causal)

**Status:** Accepted (2026-11-22)

A block is **underserved** if its network walking distance to the nearest park access point exceeds the 10-minute threshold (800 m). Neighborhood metrics report the **population** in underserved blocks and the **share** of residents underserved. A **priority** ranking combines underserved population count with population density. Demographic indicators (ACS) are shown *alongside* these metrics as context; the UI text explicitly states that co-occurrence does not establish cause.

---

## D-012 — Version control

**Status:** Accepted (2026-11-22)

The project owner initialised the repository (remote `origin`, branch `main`) before Stage 1. Commit at most once per completed stage, with one commit per completed stage, only after your approval (per AGENTS.md §12). Raw downloaded data is git-ignored and reproduced by the pipeline.

---

## D-013 — PostGIS image on Apple Silicon: official amd64 image under emulation

**Status:** Accepted (2026-11-22, Stage 1, within the approved D-009 stack)

**Context.** The development Mac is arm64. On Docker Hub (checked 2026-11-22) the official `postgis/postgis:17-3.6` image is published for **amd64 only**. A community multi-arch build (`imresamu/postgis`) has arm64.

**Decision.** Use the official image, pinned to `postgis/postgis:17-3.6`, with `platform: linux/amd64`. Docker Desktop runs it through Rosetta emulation.

**Why.** It's the official, documented image and matches typical amd64 deployment targets. Our data volumes are small, so the emulation overhead doesn't matter. Verified: PostgreSQL 17.11, PostGIS 3.6.4, GEOS 3.14.1, PROJ 9.8.1.

**Revisit if** Stage 4–6 queries are noticeably slow: switch to the multi-arch image or native Postgres.app.

---

## D-014 — Dependency resolution cutoff (`exclude-newer`)

**Status:** Accepted (2026-11-22, Stage 1)

**Context.** The first resolution pulled `types-pyyaml` published the same day. Very new releases carry the most supply-chain risk (hijacked or broken uploads), and "latest at lock time" isn't reproducible.

**Decision.** `[tool.uv] exclude-newer` in `pipeline/pyproject.toml` (currently `2026-11-08`) means uv ignores any package published after that date. Exact versions and hashes are pinned in `uv.lock`. When upgrading, bump the date deliberately and keep it at least two weeks in the past.

**Consequence.** We run slightly older patch releases (e.g., pydantic 2.14.3 rather than 2.14.4). The lower bounds in `pyproject.toml` only state the oldest API we rely on.

---

## D-015 — Stage 2 data-source choices

**Status:** Accepted (2026-11-22, "Approve as recommended")

Context and evidence: `docs/learning/stage-02-boundaries-population.md` §4 and §9.

| # | Decision |
|---|---|
| Q1 | Neighborhoods come from City of Portland layer 125 "Neighborhoods (regions)": one polygon per neighborhood, overlapping in shared areas, so blocks can belong to two neighborhoods (`area_membership`) |
| Q2 | The 4 `MC UNCLAIMED` areas (`ID = 0`) are excluded via the config `where` clause. Their residents still count city-wide and are reported as "no neighborhood association" |
| Q3 | City Council districts (layer 1413) are a second `area_type`. Their `pop_total` is an exact reconciliation check |
| Q4 | ACS 2020–2024 tables: B01003, B19013, B03002, B25044. Age (B01001, 191 MB) deferred |
| Q5 | Census data from key-free bulk files (TIGER/Line, ACS table-based summary file), not the Census API (which now requires a key) |
| Q6 | Block representative point = Census internal point, checked inside its polygon (amends D-007) |
