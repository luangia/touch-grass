# Architecture — TouchGrass

> Status: **design only**. Nothing below is implemented yet. Decisions referenced as `D-0xx` are in [DECISIONS.md](DECISIONS.md).

## 1. High-level system

TouchGrass is a classic **batch-analysis + read-only serving** GIS system. The expensive spatial work (network analysis) happens offline in a reproducible pipeline; the web app only reads precomputed results.

```
             ┌────────────────────────── offline (per region) ──────────────────────────┐
             │                                                                           │
 config/regions/portland-or.yaml                                                         │
             │                                                                           │
             ▼                                                                           │
  ┌──────────────────┐   ┌───────────────────┐   ┌──────────────────┐   ┌─────────────┐ │
  │ 1. Acquire        │──▶│ 2. Clean/normalize │──▶│ 3. Load PostGIS  │──▶│ 4. Analyze  │ │
  │ Census, TIGER,    │   │ reproject, validate│   │ tables + GiST    │   │ walk network│ │
  │ OSM, city layers  │   │ classify parks     │   │ indexes          │   │ Dijkstra,   │ │
  │ → data/raw/       │   │ → data/processed/  │   │                  │   │ aggregation │ │
  └──────────────────┘   │   (GeoParquet)     │   └──────────────────┘   └──────┬──────┘ │
                         └───────────────────┘                                  │        │
             └──────────────────────────────────────────────────────────────────┼────────┘
                                                                                 ▼
                                                           ┌──────────────────────────────┐
                                                           │ PostgreSQL + PostGIS          │
                                                           │ regions, areas, blocks, parks,│
                                                           │ access results, area metrics  │
                                                           └──────────────┬───────────────┘
                                                                          │ SQL (read-only)
                                                           ┌──────────────▼───────────────┐
                                                           │ FastAPI  /api/v1/...          │
                                                           │ JSON, GeoJSON, MVT tiles      │
                                                           └──────────────┬───────────────┘
                                                                          │ HTTP
                                                           ┌──────────────▼───────────────┐
                                                           │ React + TS + MapLibre GL      │
                                                           │ map, comparison, charts       │
                                                           └──────────────────────────────┘
```

**Why batch, not on-request?** A city-wide multi-source Dijkstra takes seconds to minutes and the inputs change rarely (annual census, periodic OSM). Precomputing keeps the API simple, fast, and deterministic, and makes every result traceable to a pipeline run.

## 2. Components and responsibilities

| Component | Location (planned) | Responsibility | Must NOT |
|---|---|---|---|
| Region config | `config/regions/*.yaml` | Declare everything region-specific | Contain code |
| Pipeline (`touchgrass` Python package) | `pipeline/` (src layout: `pipeline/src/touchgrass/`) | Acquire → clean → load → analyze, one CLI | Know about the HTTP API |
| Shared spatial core | `pipeline/src/touchgrass/core/` | CRS handling, snapping, Dijkstra, aggregation — region-agnostic | Reference any city by name |
| Source adapters | `pipeline/src/touchgrass/sources/` | One adapter per source type: `http` (streaming, SHA-256 manifests, cache), `arcgis` (paged query), `tiger`, `acs` (bulk summary file), `osm` (Geofabrik extract + MD5, pyosmium area extraction) | Contain analysis logic |
| Pipeline steps | `pipeline/src/touchgrass/pipeline/` | `sources` (raw input list), `fetch` (network), `process` (offline → GeoParquet), `validate` (hard/soft checks → `validation.json`) | Download inside `process` |
| Database | `db/` (migrations, SQL) | Persist normalized data + results, enforce constraints | Hold raw downloads |
| API | `api/` (own uv project, package `touchgrass_api`; D-023) | Read-only queries, serialization, validation; GeoJSON and MVT built in PostGIS | Run heavy analysis; import the pipeline (the schema is the contract) |
| Frontend | `web/` (Vite + React + TS + MapLibre; D-024) | Visualization, interaction, explanation; colors from pure style functions; state in the URL | Compute metrics itself; name any region or coordinate |

## 3. Data flow (one region, end to end)

1. `touchgrass pipeline run --region portland-or` reads the YAML.
2. **Acquire**: download study boundary, neighborhood polygons, TIGER blocks/block groups, P.L. 94-171 block population, ACS block-group tables, OSM parks + walk network for the *buffered* boundary. Each file is written to `data/raw/<region>/<source>/` with a `manifest.json` (URL, timestamp, SHA-256, license).
3. **Clean**: reproject to the region's analysis CRS, fix invalid geometries (`make_valid`), dissolve multi-part parks, classify parks by tag rules, drop excluded types, join population to blocks by GEOID. Output GeoParquet in `data/processed/<region>/`.
4. **Validate**: assertion checks (row counts, CRS, population totals vs. published totals, no null geometries, parks inside buffer).
5. **Load** into PostGIS (stored in EPSG:4326, plus an analysis-CRS geometry column where useful).
6. **Analyze**: build walk graph → derive park access nodes → multi-source Dijkstra → per-block network + straight-line distance → write `block_access`.
7. **Metrics** (Stage 7): per-park bounded searches (reachable park area) → population-weighted aggregation by area (+ city from blocks) → ACS allocation by 2020 block population with MOE and reliability → `area_metrics`, `area_thresholds`, `area_context`, report.
7. **Serve**: API reads tables; frontend renders.

## 4. Database design

**Built in Stage 4** (D-019). Schema `touchgrass`; migrations in `pipeline/src/touchgrass/db/migrations/0001–0004`. Every table carries `region_id`, its primary key starts with it, and it cascades from `regions` (D-004). Geometry is stored in EPSG:4326 only (D-008), enforced by typed columns.

```
regions          (region_id PK, name, analysis_crs, config jsonb, loaded_at)
datasets         (region_id, source_id, url, stored_file, stored_bytes, sha256, retrieved_at,
                  vintage, attribution, params jsonb)                PK (region_id, source_id)
study_boundaries (region_id PK, buffer_m, boundary MultiPolygon, analysis_buffer MultiPolygon)
analysis_areas   (region_id, area_id, area_type, source_key, name, check_population,
                  geom MultiPolygon)                                 PK (region_id, area_id)
census_blocks    (region_id, geoid20, county_fips, pop20, housing20, aland20, awater20, area_m2,
                  frac_in_boundary, rep_point Point, rep_point_source, geom MultiPolygon)
                                                                     PK (region_id, geoid20)
area_membership  (region_id, area_id, geoid20)  PK all three; FKs → analysis_areas, census_blocks
block_groups     (region_id, geoid, geom MultiPolygon)               PK (region_id, geoid)
acs_estimates    (region_id, geoid, variable, estimate, moe, vintage)
                                       PK (region_id, geoid, variable); FK → block_groups
  view acs_estimates_cv                adds cv = (moe / 1.645) / estimate
green_candidates (region_id, osm_type, osm_id, name, tags jsonb, included, park_class, reason,
                  rule_tag, area_m2, geom MultiPolygon)              PK (region_id, osm_type, osm_id)
parks            (region_id, park_id, name, park_class, area_m2, subtracted_m2, n_members,
                  member_ids text[], member_names, geom MultiPolygon) PK (region_id, park_id)
schema_migrations(version PK, filename, sha256, applied_at)          written by the runner
```

**Added in Stage 5** (migration 0005, D-020):

```
block_origins      (region_id, geoid20, edge_id, u, v, connector_m, offset_u_m, offset_v_m,
                    nearest_component, flagged, snap_status [ok|moorage|isolated|crosses_water],
                    component, geom Point)                   PK (region_id, geoid20); FK → census_blocks
park_access_points (region_id, access_id, park_id, method [edge_contact|entrance|gap], edge_id, u, v,
                    offset_u_m, offset_v_m, contact_start_m, contact_end_m, osm_node_id, gap_m,
                    geom Point)
                                                             PK (region_id, access_id); FK → parks
```

Both locate a point *on* a network edge by its distance from each end; the graph itself (nodes, edges) is GeoParquet only.

**Added in Stage 6** (migration 0007, D-021):

```
analysis_runs  (region_id, run_id, generated_at, params jsonb, inputs jsonb, code jsonb,
                summary jsonb, seconds, is_latest)  PK (region_id, run_id); one is_latest per region
block_access   (region_id, run_id, geoid20, pop20, snap_status, reachable, network_m, walk_min,
                straight_m, straight_park_id, detour_ratio, nearest_park_id, nearest_access_id,
                route_kind [via_node|same_edge], geom Point)
                                                    PK (region_id, run_id, geoid20); FK → analysis_runs
```

**Indexes:** GiST on every geometry column (plus `census_blocks.rep_point`); B-tree on `analysis_areas (region_id, area_type)`, `area_membership (region_id, geoid20)` and `green_candidates (region_id, reason)`.

**Loading:** `touchgrass load <region>` re-validates the processed outputs, then in one transaction deletes the region (cascade), binary-`COPY`s each table into a temp staging table, and inserts with SQL conversions (WKB → geometry, lon/lat → Point, text → jsonb / text[]). `ANALYZE` runs after commit.

**Added in Stage 7** (migration 0008, D-022). Keyed by analysis run; the `city` row (area_type `city`) is computed from blocks, never by summing areas:

```
metrics_runs      (region_id, run_id, generated_at, params, inputs, code, summary jsonb)
                                                    PK (region_id, run_id); FK → analysis_runs
area_metrics      (region_id, run_id, area_id, area_type, name, blocks, pop_total,
                   populated_land_m2, unreachable_pop, underserved_pop, underserved_share,
                   median_walk_m, priority_score, block_share_within, land_share_within,
                   reachable_park_ha_mean, reachable_park_ha_median, park_m2_inside,
                   park_m2_inside_per_resident, ranked)
                                                    PK (region_id, run_id, area_id); FK → metrics_runs
area_thresholds   (region_id, run_id, area_id, threshold_min, threshold_m, pop_within, pct_within)
                                                    PK (…, area_id, threshold_min); FK → area_metrics
area_context      (region_id, run_id, area_id, indicator, kind [count|share], estimate, moe, cv,
                   reliability [high|medium|low], numerator(_moe), denominator(_moe), acs_vintage)
                                                    PK (…, area_id, indicator); FK → area_metrics
block_park_reach  (region_id, run_id, geoid20, parks_reachable, reachable_park_ha)
                                                    PK (…, geoid20); FK → block_access
```

No foreign keys to the *current* `analysis_areas`, for the same reason as `block_access`: a historical run keeps the areas it was computed with.

**Why not store the walk graph in PostGIS?** We traverse it in Python (NetworkX). Persisting it as GraphML/GeoPackage in `data/processed/` is simpler. Edges may optionally be loaded for display later (pgRouting is a future option).

## 5. Geospatial processing pipeline (details)

| Step | Operation | Key concept |
|---|---|---|
| Reproject | `gdf.to_crs(region.analysis_crs)` | Geographic vs projected CRS |
| Repair | `shapely.make_valid`, drop empties | Geometry validity (OGC simple features) |
| Park filter | Tag rules + min area + `access!=private` | Attribute filtering, classification |
| Dissolve | Merge touching park parts with same name/ID | Topology / unary union |
| Buffer | Study boundary + 2 km | Buffer operation, edge effects |
| Block assignment | Rep. point within boundary/area | Spatial join (point-in-polygon) |
| Graph | pyosmium walk filter (config) → `.osm` XML → `osmnx.graph_from_xml(bidirectional=True)`; clip to buffer; project; undirected; largest component (D-020) | Graph model of networks |
| Access points | Edge-contact intervals within 15 m of the park ∪ public entrance nodes; else a dry gap ≤ 100 m (D-006 amended) | Spatial intersection, tolerance |
| Snapping | Nearest **edge** reachable without crossing water (STRtree); connector + offsets to both ends; `snap_status` | Nearest neighbor search, barriers |
| Distances | SciPy `csgraph.dijkstra(min_only=True)` from one node per access point; no cutoff; via u / via v / same-edge (D-021) | Shortest paths |
| Straight-line | Nearest park polygon (STRtree) from the block point | Euclidean proximity |
| Aggregate | Population-weighted shares per area | Spatial aggregation, weighting |

Population-weighted share for area *A* and threshold *t*:

```
pct_within(A, t) = Σ_{b ∈ A} pop_b · [d_b ≤ t]  /  Σ_{b ∈ A} pop_b
d_b = snap_dist_m(b) + network_dist(node(b), nearest access node)
```

Weighted median distance uses each block's population as its weight (so an empty industrial block counts zero, unlike an area average).

## 6. API design (v1, read-only)

| Method & path | Returns |
|---|---|
| `GET /api/v1/regions` | List of regions with name, bbox, default view |
| `GET /api/v1/regions/{region_id}` | Region metadata, thresholds, data vintages, latest run |
| `GET /api/v1/regions/{region_id}/areas?type=neighborhood` | GeoJSON FeatureCollection of analysis areas (+ headline metrics) |
| `GET /api/v1/regions/{region_id}/areas/{area_id}/metrics` | Metrics for all thresholds + demographics |
| `GET /api/v1/regions/{region_id}/compare?area_ids=a,b,c&threshold=10` | Side-by-side metrics |
| `GET /api/v1/regions/{region_id}/rankings?metric=pct_within&threshold=10&order=asc&limit=10` | Ranked list (underserved view) |
| `GET /api/v1/regions/{region_id}/parks` | GeoJSON parks (simplified) |
| `GET /api/v1/regions/{region_id}/tiles/blocks/{z}/{x}/{y}.pbf` | MVT of blocks with access distance (D-010) |
| `GET /api/v1/regions/{region_id}/methodology` | Parameters, sources, licenses, limitations |
| `GET /health` | Liveness + DB check |

Conventions: snake_case JSON, Pydantic response models, `404` for unknown region/area, `422` for invalid params, OpenAPI docs at `/docs`. No auth (public, read-only data).

**As built (Stage 8, D-023):**
- **Latest run:** every endpoint answers for the latest analysis run and returns its `run_id`, which is also the `ETag` (`304` on revalidation).
- **`areas`:** requires `type` and takes `threshold` (default: the underserved threshold).
- **`rankings`:**
  - `metric` ∈ `pct_within` / `underserved_pop` / `priority_score`, mapped to fixed SQL expressions.
  - The share and priority rankings include only areas with at least `min_residents_for_ranking` residents.
- **Block tiles:** z12–14 only (`422` outside that range), `204` when a tile is empty, layer `blocks` with `geoid20`, `pop20`, `reachable`, `network_m`, `walk_min`, `nearest_park_id`, `parks_reachable`, `reachable_park_ha`.
- **GeoJSON:** simplified to 5 m in the analysis CRS, 6 decimals.
- **Middleware:** gzip above 1 KB; CORS from `TOUCHGRASS_CORS_ORIGINS`, GET only.
- **Sessions:** read-only. The app refuses to start against a schema version other than the one it expects.

## 7. Multi-region design

**Principle:** *Code is shared; data and parameters are configured.* A new city = a new YAML file + reachable data sources + one pipeline run.

Example config (illustrative — not yet created):

```yaml
region_id: portland-or
name: Portland, Oregon
country: US
analysis_crs: 26910                # NAD83 / UTM 10N (meters)
state_fips: "41"
county_fips: ["051", "005", "067"] # Multnomah, Clackamas, Washington (city spans all three)
study_boundary:
  adapter: arcgis_feature_service
  url: <City of Portland boundary layer — verified in Stage 2>
buffer_m: 2000
analysis_areas:
  - area_type: neighborhood
    adapter: arcgis_feature_service
    url: <City of Portland neighborhood boundaries>
    name_field: <field name verified in Stage 2>
    allow_overlaps: true
  - area_type: census_tract
    adapter: tiger
parks:
  adapter: osm
  include_tags: {leisure: [park, nature_reserve, garden, playground, recreation_ground]}
  exclude: {access: [private, no]}
  min_area_m2: 400
  validation_source: {adapter: arcgis_feature_service, url: <Metro ORCA>}
population: {adapter: census_pl94171, vintage: 2020}
demographics: {adapter: census_acs5, vintage: "2024", geography: block_group}
analysis:
  walk_speed_kmh: 4.8
  thresholds_min: [5, 10, 15]
  max_snap_m: 500
```

What varies per region and how it's absorbed:

| Variation | Absorbed by |
|---|---|
| Boundary source & format | Adapter type + URL in config |
| Neighborhood system (or none) | `analysis_areas` list with generic `area_type`; tracts always available as fallback |
| Overlapping/gapped areas | `area_membership` many-to-many |
| Park data quality | Selectable park adapter (OSM default, authoritative override) |
| Local CRS | `analysis_crs` |
| Policy thresholds (e.g., 10-min goal) | `analysis.thresholds_min` |
| Frontend map view | `regions.bbox` from DB — no coordinates in frontend code |

Non-US regions would need different population adapters; the core algorithms do not change.

## 8. Deployment considerations

- **Local (MVP):** `docker compose up db` (PostGIS only), API via `uvicorn`, frontend via `vite`. Pipeline run from CLI.
- **Portfolio deployment (Stage 11, optional):** results are read-only, so a small managed Postgres with PostGIS (or a single VM running compose) + static frontend hosting is enough. Alternative "serverless" option: export results to static GeoJSON/PMTiles and host the frontend statically with no backend at all — worth considering if hosting cost matters.
- **Reproducibility:** pinned dependencies (`uv.lock`, `package-lock.json`), data manifests with checksums, `analysis_runs.params` + git commit hash.
- **Attribution:** OSM (ODbL) attribution on the map; Census is public domain; city/Metro layers per their terms.

## 9. Data sources (evaluation)

Verified at planning time (2026-11-22) unless marked **[verify]** — items marked so are confirmed (URL, field names, license text) in the stage that ingests them. All cover the City of Portland study area; national datasets cover all of Oregon.

| Dataset | Provides | Coverage | Format | License | Updates | Processing | Stored in | Limitations |
|---|---|---|---|---|---|---|---|---|
| **City of Portland boundary, Neighborhoods (regions), Council Districts** (PortlandMaps `COP_OpenData_Boundary` MapServer layers 10, 125, 1413) | City limits (Metro-maintained); neighborhood association polygons (overlapping); 4 council districts with 2020 `pop_total` | City of Portland | ArcGIS MapServer query → GeoJSON (server reprojects from EPSG:3857 with `outSR=4326`) | City layers: "Available for Public Use… City of Portland Data Distribution Policy… provided 'as is'" (layer metadata, verified 2026-11-22). City Boundaries layer is Metro RLIS data under the **RLIS Open Database EULA** (2023-02-01; verified 2026-11-22): royalty-free, revocable licence to copy, publish, adapt and combine; the required attribution statement + licence link must be shown; no sublicensing; no implied Metro endorsement; "as is" with an indemnity clause; Oregon law; stated compatible with CC BY 4.0 / ODC-By (attribution, **not** share-alike) | As needed (catalog entry updated 2025) | Fetch → reproject → validate → `analysis_areas` | raw: `data/raw`; final: `analysis_areas`, `study_boundaries` | Neighborhood boundaries overlap in places and are civic, not statistical, units |
| **2020 Census P.L. 94-171 (table P1)** | Total population (and housing units) by **block** | All US | Embedded in the TIGER 2020 block shapefile as `POP20`/`HOUSING20` (verified 2026-11-22). The Census API now requires a key | Public domain (US Gov) | Decennial | Attribute join to TIGER blocks by 15-digit GEOID | `census_blocks.pop_total` | 2020 vintage; differential-privacy noise at block level |
| **TIGER/Line 2020 blocks (`tabblock20`), block groups, tracts** | Census geometries | All US (Oregon state FIPS 41) | Shapefile (zip), NAD83 (EPSG:4269) | Public domain | Annual (block geometry fixed per decade) | Reproject; rep. points; join | `census_blocks`, `block_groups` | Boundaries follow roads/water, not housing locations |
| **ACS 5-year 2020–2024** (released 2026-01-29) | Demographics: income, age, race/ethnicity, vehicle availability, etc. | All US; **select detailed tables** at block group | Table-based summary file (`acsdt5y2024-<table>.dat`, pipe-delimited, nationwide, sentinels −666666666/−222222222). The API needs a key | Public domain | Annual | Fetch chosen tables → join on 12-digit GEOID → weight to areas | `block_groups` | Sampling estimates with MOE; not every table at block group; mismatched vintage vs 2020 blocks |
| **OpenStreetMap** (dated Geofabrik extract, e.g. `oregon-261101.osm.pbf`; D-017) | Walk network (streets, footways, paths, crossings); parks; entrances | Global; Portland is well mapped | .osm.pbf (Protocol Buffers), MD5 published per file; read with pyosmium (areas assembled from ways + multipolygon relations) | **ODbL 1.0** (attribution; share-alike for derived databases) | Continuous upstream; we pin a dated snapshot (yearly 1 Jan files kept long-term, monthly/daily recent) | Stage 3: candidate areas → config rules (D-018) → overlap-merged park units. Stage 5: walk network + entrances | raw: `data/raw/<region>/osm/`; `green_candidates`, `parks` (GeoParquet) | Tag inconsistency; "walk" includes roads without sidewalks; entrances unevenly mapped; snapshot date matters |
| **Metro RLIS — ORCA** (Outdoor Recreation & Conservation Areas) | Authoritative parks/natural areas with ownership | Portland metro (3 counties) | ArcGIS FeatureServer (`Outdoor_Recreation_and_Conservation_Areas_ORCA/FeatureServer/5`), filtered `UNITTYPE IN ('Park','Natural Area') AND STATUS <> 'Planned'` | RLIS Open Database EULA (verified 2026-11-22, same terms as the City Boundaries layer): attribution statement + licence link required; CC BY 4.0 / ODC-By compatible | Periodic | Validation comparison against OSM parks (D-003) | `data/processed` (validation only) | Portland-specific; attribute schema to learn |
| **City of Portland park boundaries** (Parks & Recreation; e.g., "Park Site Taxlots") | City-owned park sites | City of Portland | ArcGIS service | City terms **[verify]** | Updated 2026 | Optional second validation source | — | Taxlot-based: may include non-public parcels |
| **Trust for Public Land ParkServe** | National parks + 10-minute walk service areas | ~15,000 US cities incl. Portland | Shapefile / FGDB downloads | **Restrictive** (ParkServe Terms of Use, May 2023; verified 2026-11-22): all rights reserved; one copy may be downloaded for **personal, non-commercial** use only, and not modified, publicly displayed or transferred without TPL's prior written consent. Contains OSM (ODbL) and Esri material | Annual | **Not ingested** (D-016). Benchmark only by citing TPL's own published Portland figures, with attribution | nowhere | Uses ½-mile walk areas from access points; method differs from ours |
| *Future:* **Annual NLCD 1.1** (land cover, 30 m, 1985–2024) | Land-cover raster | CONUS | GeoTIFF / COG | Public domain | Annual | Zonal statistics | — | 30 m too coarse for small parks |
| *Future:* **USFS Tree Canopy Cover v2023.5** (30 m, 1985–2023) | % canopy raster | CONUS | GeoTIFF | Public domain | Periodic | Zonal stats per block/area | — | 30 m; deferred (non-MVP) |

Reproducibility rule: every pipeline run records source URL, retrieval time, checksum, and vintage in `datasets`; raw files are cached so re-runs don't depend on live services. For OSM, prefer a dated Geofabrik extract over live Overpass so results are reproducible (decision to confirm in Stage 5).
