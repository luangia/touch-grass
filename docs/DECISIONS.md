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
2. **Metro ORCA** (Outdoor Recreation and Conservation Areas) or **City of Portland park boundaries**. Authoritative, includes ownership/access attributes. Portland-only; each future city needs a new adapter. Licence: the RLIS Open Database EULA (verified 2026-11-22). It is attribution-style and states compatibility with CC BY 4.0 / ODC-By, *not* ODbL share-alike as first assumed.

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

**Amendment (2026-11-23, D-020 Q5):**
- **(c) is removed.** In Portland, the 10 parks with no network within 15 m are mostly river islands (Ross Island, Tri-Club Island, Moore and Wright Islands), and snapping their edges to the nearest street would invent a crossing over the Willamette. They are reported as `no_network_contact`.
- **(b) works on edges, not nodes.** After OSMnx simplification most boundary crossings are mid-edge, so an access point is the stretch of an edge within 15 m of the park: a contact interval, located by distance from each end.
- **(a) excludes** `access=private` / `no` entrances, and entrances more than 15 m from the network.
- **Second amendment (2026-11-23, after your field check):** a dry gap replaces the old fallback. A park with no contact and no entrance connects to the nearest network within `max_park_gap_m` (100 m), **if the straight gap crosses no OSM water**; the gap length is added to the walking cost. Field check: Cottonwood Creek (28 m), Stephens Creek (37 m) and Ravine Woods (91 m) are walkable and now connect. Moore and Wright Islands (26 m, all over water) and Ross Island stay unreachable. Distance alone could not separate these cases.

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

**Amendment (2026-11-23, Stage 7 Q3, "Approve as recommended"): the priority formula.** "Combines underserved population with density" was too vague: read as a sum of two rankings, density dominated (the prototype put dense, mostly-served neighborhoods first). Priority is now one number:

```
priority(A) = underserved residents of A  /  land area (km², Census ALAND20) of A's populated blocks
```

It reads as "underserved residents per km² of where people live". It's high where many people are packed beyond a 10-minute walk. It's ranked only for areas with at least `metrics.min_residents_for_ranking` residents (1,000 in Portland), like the share ranking, so a tiny area can't top the list on a handful of people.

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

---

## D-016 — Trust for Public Land ParkServe: do not ingest; cite published figures only

**Status:** Accepted (2026-11-23): Option 1, with Option 2 in progress. The project owner emailed TPL a consent request on 2026-11-23 (Portland only; comparison statistics and maps with attribution; no redistribution). **Awaiting a response.** If consent arrives, record its exact conditions here and in ARCHITECTURE §9 before using any ParkServe data. Until then, Option 1 applies.

**Context.** The planning docs listed ParkServe's 10-minute-walk service areas as an external benchmark to download and compare against (ARCHITECTURE §9). The ParkServe Terms of Use (May 2023; verified 2026-11-22) reserve all rights:
- One copy may be downloaded for **personal, non-commercial use**.
- Downloaded materials may **not be modified, used for public display, or transferred** without TPL's prior written consent.
- ParkServe also contains OSM data (ODbL) and Esri material.

TouchGrass is a public portfolio project, so clipping, reprojecting or overlaying ParkServe polygons, or publishing maps or numbers derived from them, would need written consent. (This is a reading of the terms, not legal advice.)

**Options.**
1. **Don't ingest ParkServe at all; cite TPL's own published Portland figures** (e.g., their headline 10-minute-walk share) as a sanity benchmark, with attribution and a link. A cited fact isn't a redistribution of their data.
2. **Ask TPL for written consent** to use the downloads for a public, non-commercial comparison. Highest-quality benchmark, but it's an external dependency with an unknown response time.
3. Drop the benchmark entirely.

**Recommendation.** Option 1 now. Option 2 is optional and can run in parallel (you'd send the request; I'd only draft it). Our own validation doesn't depend on ParkServe: it rests on OSM-vs-ORCA comparison, invariants, and independent route checks (Stages 3 and 6).

**Consequences.** No ParkServe adapter, no ParkServe files in `data/`, and no ParkServe geometry in the app. The Stage 6 comparison becomes "our share vs TPL's published share", with the method differences explained (TPL uses half-mile walkable service areas from park access points).

---

## D-017 — OpenStreetMap from dated Geofabrik extracts, read with pyosmium; uv-managed Python

**Status:** Accepted (2026-11-23, Stage 3 Q1, Q2, Q7, "Approve as recommended")

**Context.** Stage 3 needs OSM park polygons; Stage 5 will need the walk network. During preparation the public Overpass API returned *"502 … currently at capacity"* and asks automated clients to run their own instance. Geofabrik keeps dated state extracts (yearly 1 January since 2014, monthly recent, daily for about 10 days), each with a published MD5.

**Decision.**
- Source: `https://download.geofabrik.de/<geofabrik_path>/<name>-<YYMMDD>.osm.pbf`, configured per region (`osm.geofabrik_path`, `osm.snapshot`). Portland: `north-america/us/oregon`, snapshot `261101` (1 Nov 2026).
- `fetch` verifies the file against the published `.md5` and records it in the manifest; a mismatch deletes the file and fails.
- Reading: **pyosmium** (libosmium) streams the PBF, keeps a node-location index, and assembles areas from closed ways and multipolygon/boundary relations. One pass over Oregon takes about 1 minute.
- OSMnx stays planned for the Stage 5 network.
- **Q7:** the project venv uses a uv-managed CPython 3.12 (`[tool.uv] python-preference = "only-managed"`), not the Anaconda interpreter it was first created from.

**Consequences.** Results are reproducible from the cached snapshot plus checksum. A snapshot change is a deliberate config edit. Monthly files may eventually be pruned by Geofabrik, so the cached raw file and manifest are the record of what was used.

---

## D-018 — Park classification rules and overlap merging

**Status:** Accepted (2026-11-23, Stage 3 Q3–Q6, "Approve as recommended")

**Decision** (all in `config/regions/<region>.yaml` → `parks`):

| Rule | Value (Portland) |
|---|---|
| Include | `leisure` = park, nature_reserve, dog_park, common; `boundary=protected_area`; `landuse` = recreation_ground, village_green |
| Natural-area class | `leisure=nature_reserve`, `boundary=protected_area` |
| Exclude: access | `access` ∈ {private, no} (even when an include tag is present) |
| Exclude: not open space | golf_course, cemetery |
| Exclude: land cover, not a park | landuse = grass, forest, meadow |
| Exclude: amenity usually in a park or school | pitch, playground, sports_centre, track |
| Exclude: garden (fee, membership or private) | leisure=garden |
| Minimum area | 400 m² |
| Merge | candidates whose interiors overlap by > 1 m² become one unit (union-find over an STRtree); touching parks stay separate |
| Sentinel parks (hard check) | Forest Park, Washington Park, Mount Tabor Park, Laurelhurst Park, Powell Butte Nature Park |
| Validation reference | Metro ORCA, `UNITTYPE IN ('Park','Natural Area') AND STATUS <> 'Planned'` |

**Why.**
- In Portland's OSM data, Forest Park is `leisure=nature_reserve` + `boundary=protected_area` and Washington Park is `boundary=protected_area` only, so a `leisure=park`-only rule would miss the city's two largest parks.
- Reserve and protected-area polygons overlap 46.1 of 47.4 km², hence the merge.
- Exclusions follow the working definition "public green space a resident can walk into and spend time in".

**Consequences.** Every candidate is kept in `green_candidates.parquet` with its decision and reason, so any rule (e.g., cemeteries) can be flipped in config and its effect measured.

**Amendment (2026-11-23, approved "Yes, plus the zoo"):** the discrepancy review found park outlines enclosing excluded facilities: the Heron Lakes golf courses inside Delta Park West (~308 acres) and the Oregon Zoo inside Washington Park (~65 acres).
- `tourism=zoo` added to `not_open_space`.
- New `parks.subtract_reasons: [not_open_space]`: after merging, those areas are cut out of enclosing units; units left below the minimum area are dropped.
- Effect on Portland: 1.51 km² removed from 2 units; 1 unit dropped (a 0.16-acre recreation ground inside Waverly Country Club). OSM-vs-ORCA precision 0.877 → **0.895**, recall unchanged at 0.931.
- Gardens are deliberately **not** subtracted: e.g., the International Rose Test Garden inside Washington Park is free and public.

**Amendment 3 (2026-11-23, approved "C: drop but show me the named ones"):** `landuse=recreation_ground` and `landuse=village_green` are removed from `include_tags` and recorded under the exclude reason `sports_or_private_ground`, so they stay auditable.
- **Why:** your Stage 5 map check found three unnamed "parks" that weren't parks: a private lot, a lawn now built on, and Cascade Athletic Club. All three got in through these tags. Of the 40 features included through them, the named ones were mostly sports facilities: school tennis courts, a motocross track, a paddock.
- **Effect:** units 473 → 449 (24 removed; named: The Paddock, Lower School Field, Peter W. Stott Community Recreation Field, Tri-Club Island, Jackson Middle School Tennis Courts). Motocross, Glenhaven Skate Park and Bless Field were members of larger parks, which are unchanged. Park area 73.50 → 73.34 km²; precision 0.8961 → 0.8974, recall 0.9309 → 0.9303.

**Amendment 4 (2026-11-23, approved "Exclude via config"):** `parks.closed_to_public` lists green spaces known to be closed although OSM doesn't record it. Each entry has the OSM id, a name and a source, and matching candidates are excluded with the reason `closed_to_public`.
- **Portland's list:** Ross Island Natural Area (`w1365628608`; your field check; no OSM `access` tag).
- **Hard check `parks.closed_list_matched`:** every listed id must still exist **with the same name**, so a deleted or reused OSM id forces a review instead of silently doing nothing.
- **Effect:** units 449 → 448; park area 73.34 → 73.21 km²; precision 0.8974 → 0.8972, **recall 0.9303 → 0.9284**. Metro ORCA counts Ross Island as a natural area: a definitional disagreement between the sources ("exists" vs "open to the public"), not an error.
- The better long-term fix is `access=no` in OSM itself; the list can then shrink.

**Amendment 2 (2026-11-23, approved "Yes, amend D-018"):** querying the loaded parks in Stage 4 (the nearest park to Pioneer Courthouse Square was the courthouse itself) showed that include tags alone let in buildings and heritage sites.
- New `parks.veto_tags`: checked after the access rule, it overrules an include tag. Each veto is `tags` (any match) plus an optional `unless` (any match cancels the veto); the value `"*"` matches any value of a key.
- Portland vetoes:
  - `building_not_open_space`: `building=*`
  - `heritage_site_not_park`: `protect_class=22` or `protection_title=protected_site`, unless the feature also has a park-type include tag (`leisure` park, nature_reserve, dog_park, common; `landuse` recreation_ground, village_green)
- Before choosing these rules, I checked them against every included candidate. Exactly 4 features match: Pioneer Courthouse (`building=office`), Aubrey Watzek House and Fido's Indoor Dog Park (`building=yes`), and the US Government Moorings Historic District (`protect_class=22`).
  - Willamette Stone State Heritage Site (`protect_class=22` + `leisure=park`) stays, via `unless`.
  - Washington Park (`protect_class=5`, "City Park") is unaffected.
- **Effect on Portland:** units 477 → 473, with no other unit changed; park area 73.57 → 73.50 km². OSM-vs-ORCA precision 0.8952 → **0.8961** (all 4 were OSM-only), recall unchanged at 0.9309. Sentinels still present.


---

## D-019 — PostGIS storage: loading, migrations, schema shape, test database

**Status:** Accepted (2026-11-23, Stage 4 Q1–Q7, "Approve as recommended")

**Decision:**

| # | Choice |
|---|---|
| Q1 Loading | psycopg 3 **binary `COPY`** of WKB into temp staging tables, then `INSERT … SELECT ST_GeomFromWKB(wkb, 4326)`. A region is replaced in **one transaction**: `DELETE FROM regions` cascades to every table, then everything is re-inserted. `ANALYZE` afterwards. The loader re-runs Stage 2–3 validation first and refuses outputs that fail it. |
| Q2 Migrations | Numbered plain SQL files plus a small runner (`touchgrass.db.migrate`). Each file runs in its own transaction under an advisory lock and is recorded with its SHA-256. An applied file whose checksum changed is refused. |
| Q3 Metric queries | Store **EPSG:4326 only** (D-008). Metres come from `geography` casts or by transforming the *constant* side. Heavy metric analysis stays in Python. Expression indexes are left as an exercise. |
| Q4 ACS | Long table `acs_estimates(region_id, geoid, variable, estimate, moe, vintage)` plus the view `acs_estimates_cv`. |
| Q5 Scope | Load everything processed **except reports** (`park_discrepancies`, `validation.json`, `process_stats.json` stay as files). Provenance from raw manifests goes in `datasets`. |
| Q6 Tests | A separate `<POSTGRES_DB>_test` database, created and dropped by the test session. Tests are marked `db` and skip when the server is unreachable. |
| Q7 Settings | `DATABASE_URL` wins; otherwise `POSTGRES_*` environment variables, then the repo's `.env`, then localhost:5432 defaults. The password is never printed. |

**Implementation detail decided during Stage 4:** the migration files live **inside the package** (`pipeline/src/touchgrass/db/migrations/`) rather than at the repo root as the lesson sketched. Shipping them with the code means an installed `touchgrass` always finds the schema it was written for.

**Consequences:**
- Every table's primary key starts with `region_id`, and every table cascades from `regions`. Reloading one region never touches another's rows, and a test checks this.
- The column types enforce SRID 4326 and the geometry type. `CHECK` constraints restate the validation rules, e.g. `ST_IsValid`, non-negative counts, `included = (reason = 'included')`, `cardinality(member_ids) = n_members`.
- The API (Stage 8) can connect read-only to the same schema. The pipeline stays the only writer.

---

## D-020 — Walk network: offline OSMnx build, walk filter, components, edge snapping

**Status:** Accepted (2026-11-23, Stage 5 Q1–Q7, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 Source | pyosmium filters the **dated extract** (D-017) into `.osm` XML; `osmnx.graph_from_xml(bidirectional=True)` builds the graph. No Overpass, so parks and network come from the same snapshot. |
| Q2 Walk filter | OSMnx 2.1 `walk` rules, restated in `config/models.py` and overridable per region, **plus `readmit`**: Portland re-admits `highway=cycleway` with `foot` ∈ {designated, yes, permissive}. |
| Q3 Components | Keep the largest connected component. Every other piece is reported with the population whose nearest street is on it, and all blocks are snapped to the main component. |
| Q4 Origins | Snap each populated block's representative point to the **nearest edge**, recording the connector length and the distance along the edge to each end (`offset_u_m`, `offset_v_m`). |
| Q5 Access points | Edge contact + entrances; no sampled fallback. See the D-006 amendment. |
| Q6 Storage | GeoParquet nodes, edges, origins and access points; PostGIS tables `block_origins` and `park_access_points` (migration 0005). The graph itself is not in PostGIS. |
| Q7 Command | `touchgrass network <region>`, separate from `process`. |

**Decisions made during implementation:**
- **Edge weight = length in the analysis CRS (D-008), not OSMnx's `length`.** OSMnx measures haversine on a sphere (R = 6,371,009 m). Against WGS 84 ellipsoidal lengths on a sample of 3,000 Portland edges, OSMnx is median −0.11%, ranging from −0.28% (east–west) to +0.05% depending on direction. UTM 10N is −0.039% on every edge (its scale factor). OSMnx's value is kept as `osmnx_length_m`.
- **Freshness guard:** `network_report.json` records the SHA-256 of `parks.parquet` and `blocks.parquet`. `load` refuses network outputs built from different files, because `process` may have changed the parks since.

**Why the walk filter matters (Portland, measured):**
- OSMnx's rules drop 119 km of foot-legal shared-use paths, among them the I-205 Multi-Use Path, Springwater Corridor and Marine Drive Trail.
- They also disconnect Hayden Island, leaving 6,891 residents whose nearest street is off the main network.
- With `readmit`, 712 ways return, the main component goes from 97.4% to 98.4% of nodes, and the stranded population falls to 4,244. That remainder is 38 small fragments, none larger than 25 nodes.

**Known limitations:**
- Residential streets without sidewalks count as walkable.
- A fence along a park edge isn't modelled, so a perimeter street counts as access.
- Streets tagged `sidewalk=separate` rely on the separately mapped sidewalks connecting at crossings.

**D-020 amendment (2026-11-23, after your field check; approved "A: keep as is, B: use the more general approach"):**
- **Connectors may not cross water.** Water = OSM `natural=water`, `waterway=riverbank`, `landuse=reservoir|basin` areas that *intersect* the buffer. Rivers are huge multipolygons whose representative point lies far upstream, so the park rule (representative point inside) would drop the Willamette. Each block snaps to the nearest main-network edge it can reach over dry land, and gets a `snap_status`:
  - `ok`: 10,361 blocks, 652,230 people.
  - `moorage`: the census point is on water (floating homes); a dock is assumed. 10 blocks, 254 people.
  - `isolated`: no dry route to the main network, so it snaps to its own piece. 1 block: Hardtack Island, 19 people. It was previously bridged 275 m across the Willamette.
  - `crosses_water`: no dry edge at all; flagged. None in Portland.
- **All network pieces are kept** in `network_nodes` and `network_edges`, each with its `component` id, so isolated origins have edges to snap to. Stage 6 will find them unreachable unless their piece reaches a park.
- **The network extent stays at the 2 km buffer (A).** Pieces the clip line cuts are now marked `touches_clip`, and a park without access gets a reason: `network_piece_cut_by_buffer_edge` (Rivervilla Park), `gap_crosses_water` or `no_network_within_max_gap`.
- Schema change in migration 0006: `block_origins.snap_status`, `block_origins.component`, and `park_access_points.gap_m` plus method `gap`. 0005 is not edited.
- Ross Island (closed to the public, not recorded in OSM) is resolved by D-018 amendment 4.

**D-020 amendment 2 (2026-11-23, Stage 6 Q1):** two contact-interval bugs, found by the Stage 6 invariant `network ≥ straight line − tolerance`, are fixed in `core/access.py`.
1. **One row per stretch.** An edge that touches a park at two separate places now gives two access rows (`access_id` suffix `#k`), not one span from the first touch to the last. The single span put the street in between "at the park": 4 Portland blocks came out up to 44.6 m closer than the straight line allows.
2. **Interior edges count.** Contact = the edge ∩ the park grown by `access_tolerance_m`, not just its boundary ring. A block on a trail inside Forest Park went from 2,628 m to 203 m.
- Portland access points: 11,771 → 15,676 (interior trails).

---

## D-021 — Walking accessibility analysis: engine, rules, run identity

**Status:** Accepted (2026-11-23, Stage 6 Q1–Q7, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 | Fix the two Stage 5 contact bugs in this stage (D-020 amendment 2). |
| Q2 | Engine: `scipy.sparse.csgraph.dijkstra(min_only=True)`, with one extra node per access point joined to its edge's ends by the access offsets. It returns the nearest access point per node; NetworkX is the test oracle and a hard check on a fixed random sample. |
| Q3 | **No cutoff.** Every reachable block gets an exact distance; the search takes 24 ms. |
| Q4 | **Same-edge rule on:** a block whose own edge holds an access stretch may walk straight along it. 665 Portland blocks use it. |
| Q5 | Straight-line baseline = Euclidean distance (analysis CRS) from the block point to the **nearest park polygon**. |
| Q6 | Unreachable blocks are kept: `reachable = false`, distance NULL. Stage 7 counts them as beyond every threshold. |
| Q7 | Run id = first 16 hex chars of SHA-256 over (parameters, input checksums, code version: git commit + a hash of uncommitted `pipeline/src` changes). Runs accumulate in `data/processed/<region>/runs/<run_id>/`; `analysis_latest.json` points at the newest; the loader reloads every run on disk and refuses a stale latest run. |

**Implementation notes:**
- **SciPy traps handled:** duplicate sparse entries are *summed* (so parallel edges are reduced to their minimum first), and explicitly stored zeros are edges, so `eliminate_zeros()` is never called. Both are pinned by tests.
- **Invariant slack is 30 m:** the 15 m contact tolerance, plus up to 15 m where an entrance node is snapped onto its edge.
- `block_access` has **no foreign keys** to the current blocks or parks: a historical run keeps the ids it was computed with.

**D-020 amendment 3 (2026-11-23, approved "Apply the fix" after your route check):** pedestrian access follows OSM convention, not OSMnx.
- **Closures:** `access` ∈ {private, **no**} and `foot` ∈ {no, **private**} exclude a way. OSMnx's walk rules miss `access=no` and `foot=private`; OSRM's foot profile does not.
- **`foot=*` overrides `access`:** a way with `foot` ∈ {yes, designated, permissive} is walkable despite an `access` closure (`WalkFilter.foot_overrides`, applied to the keys in `foot_overrides_keys` = [access]). It does not override `service=private`, `area=yes` or the others.
- **Found by:** the route check. The 7.7 km "longest walk" started on an `access=no` airport service road (OSM way 484824281) that OSRM refused.
- **Portland effect:** ways 123,690 → 123,245 (−586 `access=no` and −60 `foot=private`, +152 foot-legal `access=private`); main network 8,370 → 8,323 km, 98.40% → 98.47% of nodes, 490 → 476 pieces; access points 15,676 → 15,601. 35 blocks changed by more than 1 m (20 shorter, 15 longer). The longest-walk block went from 7,709 m to **806 m**: it now snaps to the Marine Drive Trail (584 m, the same start OSRM uses) and reaches Broughton Beach; OSRM agrees. City-wide shares are unchanged to 0.1 point (85.0% within 10 min).

---

## D-022 — Neighborhood metrics: areas, rankings, priority, park area, ACS allocation and reliability

**Status:** Accepted (2026-11-23, Stage 7 Q1–Q8, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 | Report by **every configured area type**: Portland's 93 neighborhoods *and* 4 council districts. Comparing the two shows how much the boundaries drive the conclusion (MAUP). |
| Q2 | "Worst served" is ranked **two ways**: by share within 10 minutes and by number of underserved residents. The share (and priority) rankings include only areas with ≥ `min_residents_for_ranking` residents (1,000); smaller areas are listed separately, not hidden. |
| Q3 | Priority = underserved residents per km² of populated-block land (D-011 amendment). |
| Q4 | Park area: **park area reachable within 10 minutes** (population-weighted, per block) is primary. Park area *inside the boundary* per resident is kept as a labelled secondary measure, because it rewards boundaries that happen to contain a big park. |
| Q5 | ACS estimates are allocated to areas by **2020 block population**: f(A, g) = in-A population of g / population of *all* of g's blocks. The denominator comes from the raw TIGER block file, so a block group that straddles the city line contributes only its in-city share. |
| Q6 | Area-level indicators: **total population, zero-vehicle households (B25044), people of color (1 − non-Hispanic white alone, B03002)**. Median household income stays at block-group level: a median can't be allocated or summed. |
| Q7 | Reliability from the coefficient of variation CV = (MOE / 1.645) / estimate: **high** < 15%, **medium** ≤ 30%, **low** otherwise (or undefined). Low values are shown, marked, and never ranked. |
| Q8 | A separate `touchgrass metrics <region>` command for the latest analysis run. Its outputs live in that run's folder (`runs/<run_id>/`), and its tables are keyed by `run_id`. |

**Implementation notes:**
- **City totals come from blocks**, never from summing areas: overlapping neighborhoods count a shared block in each.
- **Unreachable blocks** (D-021 Q6) count as beyond every threshold.
- **MOE propagation** follows the Census *ACS Handbook* (2020), ch. 8:
  - A sum's MOE is √Σ MOE²; zero-estimate components contribute only their single largest MOE.
  - An allocated part f·x has MOE f·MOE(x); f is treated as exact.
  - A proportion uses √(MOE_X² − p²·MOE_Y²)/Y, falling back to the ratio formula when the radicand is negative.
  - p and 1 − p share one MOE.


---

## D-023 — API: separate read-only service over the schema; GeoJSON + MVT built in SQL

**Status:** Accepted (2026-11-23, Stage 8 Q1–Q9, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 | The API is its own uv project in `api/` (package `touchgrass_api`), with **no runtime dependency on the pipeline**. Their contract is the database schema: the API checks the migration version at startup. The pipeline is a dev-only dependency, used to seed the test database with the synthetic region. |
| Q2 | Plain `def` endpoints (FastAPI runs them in a thread pool) with a `psycopg_pool.ConnectionPool`. |
| Q3 | Raw parameterized SQL through psycopg. GeoJSON FeatureCollections and MVT tiles are built in PostGIS. Identifiers that vary (ranking metric) come from a fixed whitelist keyed by an Enum, never from request text. |
| Q4 | v1 answers for the **latest** analysis run only. It's resolved once per request, and its `run_id` is returned in every response. |
| Q5 | GeoJSON geometry is simplified per polygon to 5 m in the region's analysis CRS (`ST_SimplifyPreserveTopology`), with 6 decimals. |
| Q6 | Block tiles at z12–14 only (MapLibre overzooms beyond). Empty tiles return 204. Geometry is transformed per request; no stored 3857 column. |
| Q7 | gzip (`GZipMiddleware`), `ETag` from the run id with `Cache-Control`, and CORS from a configured origin list (GET only). |
| Q8 | Every API session is read-only (`default_transaction_read_only = on`). The startup check fails if the schema version differs. A dedicated SELECT-only role is deferred to deployment (Stage 11). |
| Q9 | The map caveat text has one source: the pipeline writes it into the metrics run record (`summary.caveats`), and `/methodology` serves it. |

---

## D-024 — Map frontend: Vite + React + MapLibre, honest classification, URL state

**Status:** Accepted (2026-11-23, Stage 9 Q1–Q11, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 | `web/`: Vite + React + TypeScript (strict), npm with `.npmrc` `before=2026-11-08` (the D-014 cutoff) and a committed lockfile, Prettier, no ESLint. `.nvmrc` = 24 (LTS); Node 25 also works. |
| Q2 | Plain `maplibre-gl` with a small React bridge component; no wrapper library. |
| Q3 | API types generated from the API's `/openapi.json` (openapi-typescript, committed). GeoJSON property types are hand-written and checked against the live API. |
| Q4 | Neighborhood fill below z12, drawn under the basemap's water, roads and labels; outlines at all zooms; blocks from z12; parks always. |
| Q5 | Blocks: fixed classes at the region's thresholds, two cool (≤ 5, ≤ 10 min), two warm (≤ 15, > 15), gray for no route. The threshold toggle doesn't change them. |
| Q6 | Neighborhoods: share within the toggled threshold, fixed breaks 50 / 70 / 85 / 95%. Areas below the ranking floor get a dashed outline. |
| Q7 | ColorBrewer-derived, colorblind-safe palettes (RdYlBu split at the underserved threshold; YlGnBu for shares). The legend and panel always carry text. |
| Q8 | React state mirrored to the URL (`region`, `threshold`, `area`); no state library. |
| Q9 | Find a neighborhood by name (combobox) or by clicking. Address search is out of scope (external geocoder, privacy). |
| Q10 | Basemap: OpenFreeMap positron, URL from `VITE_BASEMAP_STYLE`. No SLA, noted for Stage 11. |
| Q11 | Vitest + Testing Library (jsdom, map mocked) + a scripted check in the in-app browser; no Playwright yet. |

**Version notes (implementation):**
- **TypeScript 5.9.3, not 7.0:** openapi-typescript 7.13 requires `typescript ^5`.
- **jsdom 29:** jsdom 30+ declares Node `^22.22 || ^24.15 || >=26` and excludes Node 25, which is installed here.

**Amendment (2026-11-23, after your review of Stage 9: "go ahead with all four"):**
1. **Two views, chosen explicitly** (`?view=residents|blocks`), replacing the zoom-dependent switch:
   - **Residents near a park:** areas filled by share at every zoom; the walk-time choice applies; only its legend shows.
   - **Walk from each block:** blocks colored by walk; areas as outlines; the walk-time choice is hidden, because the block colors show every threshold. Switching in zooms to z12 if needed, and a "zoom in" note replaces an empty map below z12.
   - **Clicks:** in the residents view a click selects the area; in the blocks view it selects the block, and the panel leads with it.
2. **Wording:** "% of residents who live within an N-minute walk of a park or natural area" (not "nature", which overclaims what the park rules count). The panel headline says the same in a sentence.
3. **Pan limit:** the region's bbox from the API plus 600 km on every side (`maxBounds`). Derived from data, so no state or place is named in code. For Portland: 130.5–114.8°W, 40.0–51.0°N (measured in the browser), which is all of Oregon and Washington, the coast, and into Idaho, Nevada and California (400 km, tried first, cut off eastern Oregon).
4. **Block selection:** the clicked block is outlined (feature state; `promoteId: geoid20` on the tile source). Its panel section shows: the area(s) it's in, linked; the distance and the **name of the nearest park**; within or beyond the underserved line, with its color; residents; parks within reach.

**API changes this required:**
- Block tiles gain `nearest_park_name`.
- The ETag gains a response-format version (`RESPONSE_VERSION`, now 2). Without it, a browser keeps a pre-change tile for the same run: the 304 says "unchanged" because the run is unchanged. Found when the panel showed no park names right after the change. After a format change a cached copy can still be served until its `max-age` (5 min) expires.

**Amendment 2 (2026-11-23, "build as recommended", credits with name + profile link, plus dark mode):**
1. **About page** at `/about`; it doubles as Stage 10's planned methodology page. It has no router library (history API). Every figure, parameter, caveat and source comes from the API. The city figures come from `/areas/city/metrics`. Fixed text: the framing, and the credits ("Built by Luan Pham", linking to luangia.github.io).
2. **Zoom-in notice:** the map dims and a centered card with a "Zoom in" button appears. The overlay ignores the pointer, so scroll-to-zoom still works.
3. **Residents palette = the block palette** (RdYlBu without its pale middle): red/orange for low shares, blues for high. The worst class is now the most visible, not the palest. Green is excluded from data colors: it means parks and the brand, and red-green is the commonest color-blind confusion. A test pins that the two views share their extreme colors.
4. **Design:**
   - **Type:** Inter (self-hosted via `@fontsource-variable/inter`; no font CDN).
   - **Color:** a white page, near-black text, hairline borders, and one forest-green accent (`#2f7d55` light / `#62b98a` dark) used only for the brand mark, active tab, links, focus rings and the primary button.
   - **Components:** pill toggles, hero numbers in the panel.
   - **Parks:** a soft green fill with a darker outline instead of a dark solid.
   - **Basemap buildings** fade in the residents view (nearly black on the dark basemap) and return in the block view.
5. **Dark mode:**
   - **Default and memory:** the system setting by default; a header toggle remembers the choice in localStorage (a per-browser convenience; it falls back to the system setting if storage is blocked). `index.html` sets the theme before first paint.
   - **Basemap:** OpenFreeMap `dark` (`VITE_BASEMAP_STYLE_DARK`).
   - **Map rebuild:** the map is rebuilt on a theme switch, keeping your camera position.


---

## D-025 — Answering every question in the UI: rankings, comparison, straight line vs network, demographics, licences

**Status:** Accepted (2026-11-24, Stage 10 Q1–Q8, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 | Header navigation **Map · Rankings · Compare · About**: pages with their own URLs, linked both ways (a ranking row opens the map; the panel can add an area to Compare). |
| Q2 | Charts are hand-written SVG (bars, grouped bars): few and simple, themed by the CSS tokens, no chart dependency. |
| Q3 | Straight line vs walking route, all four: straight-line share per area as a faint bar behind the walking bar; per block "N m on foot, M m in a straight line (k× longer)"; a "where barriers cost the most" ranking (`barrier_gap` = straight-line share − walking share); city figures on About. |
| Q4 | Demographics: per-area ACS context with ± and the reliability label (low greyed, never ranked), plus a city-level **resident** comparison: shares among residents beyond vs within the underserved walk. No neighborhood scatter plot (ecological fallacy, mixed reliability). |
| Q5 | The resident comparison treats each side as an "area" in the D-022 allocation, so it carries ACS MOE propagation. The difference's MOE is √(MOE₁² + MOE₂²); when \|difference\| ≤ MOE the UI says "no clear difference". |
| Q6 | Compare 2–4 areas of any type: table + grouped bars across thresholds, context rows with reliability; URL `?areas=`. |
| Q7 | Rankings: four titled lists (lowest share, most underserved residents, priority, largest barrier gap); the floor applies to rates; top 10 then "show all"; equal displayed values are shown as ties. |
| Q8 | Licences are data: fixed per adapter (Census TIGER/ACS: public domain; OpenStreetMap: ODbL 1.0) and a required `license` on every configured source (name, optional URL). They're stored in `datasets` and served by `/methodology`. |

---

## D-026 — Hardening, deployment and portfolio

**Status:** Accepted (2026-11-24, Stage 11 Q1–Q8, "Approve as recommended")

| # | Choice |
|---|---|
| Q1 | $0 deployment: web on **GitHub Pages**, API as a Docker web service on **Render free** (sleeps when idle; the web app shows a wake notice), database on **Neon free** (PostGIS). You confirm the free terms on the providers' pricing pages before signing up. |
| Q2 | Production data is loaded from your laptop (`touchgrass db migrate` + `load`) over Neon's **direct** endpoint with TLS; the API uses the direct endpoint with a small pool. Neon's PostGIS version is checked against our migrations and tile SQL first. |
| Q3 | Pages built by an Actions workflow; Vite `base` set from `VITE_BASE`; the router strips the base path; `404.html` fallback for deep links; `VITE_API_URL` from a repository variable. |
| Q4 | CI: pipeline, api and web jobs running the local commands, with a PostGIS service so db tests run; `-m data` stays local; uv and npm caches; audit job that reports only. |
| Q5 | A Playwright smoke test (chromium) against the synthetic `testville-zz` region in CI; run once against the public URL after deploy. |
| Q6 | A committed `results/<region>/reference.json` (source checksums, run ids, headline figures); `touchgrass reference` writes and compares it; a fresh-clone rehearsal. Raw data is not redistributed. |
| Q7 | A SELECT-only `touchgrass_reader` role with read-only transactions and `statement_timeout` set on the role; production CORS allows only the Pages origin; secrets only in host and GitHub settings; no rate limiter. |
| Q8 | Final README (results, screenshots, architecture diagram, live link, quickstart, licences, CI badge, credits) and `docs/DEPLOYMENT.md`. The longer write-up comes from `/learn:portfolio`. |

**Amendment 1 (2026-11-25, after launch; "Yes, make both"):**
- **Basemap fallback.** A visitor's browser couldn't reach `tiles.openfreemap.org` (`Failed to fetch (0)`: a blocker or filtering network; the host itself was up). The data layers were added only after the basemap loaded, so the whole map stayed blank. Now, if the basemap fails before the first load, the map switches once to a plain built-in background (no fonts, sprites or sources to fetch), draws the data on it, and shows a neutral notice instead of an error.
- **Label contrast.** The dark basemap's mid-grey labels (rgb 101,101,101) nearly vanished over the share colors. Every basemap text label now uses the page's ink with a strong page-colored halo (both themes; a test requires a WCAG contrast of at least 7:1 between text and halo). The map credits' plain text uses the muted token, readable in dark mode.

**Amendment 2 (2026-11-25; "build the touch grass toy"):** while a sleeping API wakes (the wake notice, after 3 s), the loading screen shows a strip of grass to brush, with a counter ("You've touched 37 blades of grass"). It needs no data or requests, so it works while the API is asleep. It never shows on a fast load. Someone still brushing when the data arrives gets a "Your map is ready →" button instead of a sudden switch; someone who never touched it goes straight to the map. Canvas drawing; the physics (damped springs, a light wind) is pure and unit-tested; the loop pauses when the grass is still; no wind and no back-and-forth sway under reduced motion; Space or Enter sends a gust for keyboard users. Chosen over a quiz or a game because the wait is short, rare and shouldn't ask anything of the visitor.
