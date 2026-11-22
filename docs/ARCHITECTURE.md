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
| Pipeline (`touchgrass` Python package) | `pipeline/` | Acquire → clean → load → analyze, one CLI | Know about the HTTP API |
| Shared spatial core | `pipeline/touchgrass/core/` | CRS handling, snapping, Dijkstra, aggregation — region-agnostic | Reference any city by name |
| Source adapters | `pipeline/touchgrass/sources/` | One adapter per data source type (TIGER, Census API, OSM parks, ArcGIS FeatureServer) | Contain analysis logic |
| Database | `db/` (migrations, SQL) | Persist normalized data + results, enforce constraints | Hold raw downloads |
| API | `api/` | Read-only queries, serialization, validation | Run heavy analysis |
| Frontend | `web/` | Visualization, interaction, explanation | Compute metrics itself |

## 3. Data flow (one region, end to end)

1. `touchgrass pipeline run --region portland-or` reads the YAML.
2. **Acquire**: download study boundary, neighborhood polygons, TIGER blocks/block groups, P.L. 94-171 block population, ACS block-group tables, OSM parks + walk network for the *buffered* boundary. Each file is written to `data/raw/<region>/<source>/` with a `manifest.json` (URL, timestamp, SHA-256, license).
3. **Clean**: reproject to the region's analysis CRS, fix invalid geometries (`make_valid`), dissolve multi-part parks, classify parks by tag rules, drop excluded types, join population to blocks by GEOID. Output GeoParquet in `data/processed/<region>/`.
4. **Validate**: assertion checks (row counts, CRS, population totals vs. published totals, no null geometries, parks inside buffer).
5. **Load** into PostGIS (stored in EPSG:4326, plus an analysis-CRS geometry column where useful).
6. **Analyze**: build walk graph → derive park access nodes → multi-source Dijkstra → per-block network + straight-line distance → write `block_access` → aggregate to `area_metrics`.
7. **Serve**: API reads tables; frontend renders.

## 4. Database design

Single schema, every region-scoped table carries `region_id` (D-004).

```
regions(region_id PK text, name, analysis_crs int, bbox geometry(Polygon,4326),
        config jsonb, created_at)

datasets(dataset_id PK, region_id FK, source_name, url, license, retrieved_at,
         sha256, vintage)                                -- provenance for every input

analysis_runs(run_id PK, region_id FK, started_at, params jsonb,
              code_version text, status)                 -- params: speed, thresholds, ...

study_boundaries(region_id PK/FK, geom geometry(MultiPolygon,4326),
                 buffer_geom geometry(MultiPolygon,4326))

analysis_areas(area_id PK, region_id FK, area_type text, source_key text,
               name text, geom geometry(MultiPolygon,4326))
               UNIQUE(region_id, area_type, source_key)  -- neighborhoods, tracts, ...

census_blocks(geoid20 PK char(15), region_id FK, pop_total int, housing_units int,
              geom geometry(MultiPolygon,4326), rep_point geometry(Point,4326))

block_groups(geoid PK char(12), region_id FK, acs_vintage text,
             median_hh_income int, median_hh_income_moe int, ... ,
             geom geometry(MultiPolygon,4326))

area_membership(area_id FK, geoid20 FK, weight real DEFAULT 1, PK(area_id, geoid20))

parks(park_id PK, region_id FK, source text, source_id text, name text,
      park_class text, area_m2 double, is_public bool, tags jsonb,
      geom geometry(MultiPolygon,4326))

park_access_points(access_id PK, park_id FK, region_id FK, method text,  -- entrance|crossing|sampled
                   osm_node_id bigint, geom geometry(Point,4326))

block_access(run_id FK, geoid20 FK, nearest_park_id FK,
             network_dist_m real, euclid_dist_m real, snap_dist_m real,
             PK(run_id, geoid20))

area_metrics(run_id FK, area_id FK, threshold_min int, pop_total int,
             pop_within int, pct_within real, median_network_dist_m real,
             park_area_m2 real, park_m2_per_resident real,
             PK(run_id, area_id, threshold_min))
```

**Indexes:** GiST on every `geom` column; B-tree on `(region_id)` and `(region_id, area_type)`. Spatial joins in loading (block ↔ area) use `ST_Intersects` + `ST_PointOnSurface`, verified with `EXPLAIN ANALYZE` in Stage 4.

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
| Graph | `osmnx.graph_from_polygon(buffer, network_type="walk")`, made undirected | Graph model of networks |
| Access points | Entrance nodes ∪ boundary-touching nodes | Spatial intersection, tolerance |
| Snapping | Nearest node via spatial index (KD-tree/R-tree) | Nearest neighbor search |
| Distances | `multi_source_dijkstra` from access nodes, cutoff ~2× max threshold | Shortest paths |
| Straight-line | Nearest park polygon distance (`sjoin_nearest`) | Euclidean proximity |
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
| **City of Portland boundary & Neighborhood Boundaries** (City of Portland open data / PortlandMaps `COP_OpenData_Boundary` service; maintained by BPS for Civic Life) | City limits; neighborhood association polygons | City of Portland | ArcGIS FeatureServer → GeoJSON / Shapefile | City open data terms **[verify]** | As needed (catalog entry updated 2025) | Fetch → reproject → validate → `analysis_areas` | raw: `data/raw`; final: `analysis_areas`, `study_boundaries` | Neighborhood boundaries overlap in places and are civic, not statistical, units |
| **2020 Census P.L. 94-171 (table P1)** | Total population (and housing units) by **block** | All US | Census API JSON / summary-file text | Public domain (US Gov) | Decennial | Attribute join to TIGER blocks by 15-digit GEOID | `census_blocks.pop_total` | 2020 vintage; differential-privacy noise at block level |
| **TIGER/Line 2020 blocks (`tabblock20`), block groups, tracts** | Census geometries | All US (Oregon state FIPS 41) | Shapefile (zip), NAD83 (EPSG:4269) | Public domain | Annual (block geometry fixed per decade) | Reproject; rep. points; join | `census_blocks`, `block_groups` | Boundaries follow roads/water, not housing locations |
| **ACS 5-year 2020–2024** (released 2026-01-29) | Demographics: income, age, race/ethnicity, vehicle availability, etc. | All US; **select detailed tables** at block group | Census API JSON | Public domain | Annual | Fetch chosen tables → join on 12-digit GEOID → weight to areas | `block_groups` | Sampling estimates with MOE; not every table at block group; mismatched vintage vs 2020 blocks |
| **OpenStreetMap** (via Overpass/OSMnx, or a Geofabrik Oregon `.osm.pbf` extract for reproducibility) | Walk network (streets, footways, paths, crossings); parks; entrances | Global; Portland is well mapped | OSM XML/PBF → OSMnx graph / GeoDataFrame | **ODbL 1.0** (attribution; share-alike for derived databases) | Continuous | Filter tags → classify parks; build walk graph | `data/processed` (graph), `parks`, `park_access_points` | Tag inconsistency; "walk" includes roads without sidewalks; entrances unevenly mapped; snapshot date matters |
| **Metro RLIS — ORCA** (Outdoor Recreation & Conservation Areas) | Authoritative parks/natural areas with ownership | Portland metro (3 counties) | ArcGIS FeatureServer / download | RLIS Discovery open data, appears ODbL/DbCL-based **[verify]** | Periodic | Validation comparison against OSM parks (D-003) | `data/processed` (validation only) | Portland-specific; attribute schema to learn |
| **City of Portland park boundaries** (Parks & Recreation; e.g., "Park Site Taxlots") | City-owned park sites | City of Portland | ArcGIS service | City terms **[verify]** | Updated 2026 | Optional second validation source | — | Taxlot-based: may include non-public parcels |
| **Trust for Public Land ParkServe** | National parks + 10-minute walk service areas | ~15,000 US cities incl. Portland | Shapefile / FGDB downloads | ParkServe terms of use **[verify]**, attribution expected | Annual | **Benchmark only**: compare our 10-minute coverage to theirs | not stored long-term | Uses ½-mile walk areas from access points; method differs from ours |
| *Future:* **Annual NLCD 1.1** (land cover, 30 m, 1985–2024) | Land-cover raster | CONUS | GeoTIFF / COG | Public domain | Annual | Zonal statistics | — | 30 m too coarse for small parks |
| *Future:* **USFS Tree Canopy Cover v2023.5** (30 m, 1985–2023) | % canopy raster | CONUS | GeoTIFF | Public domain | Periodic | Zonal stats per block/area | — | 30 m; deferred (non-MVP) |

Reproducibility rule: every pipeline run records source URL, retrieval time, checksum, and vintage in `datasets`; raw files are cached so re-runs don't depend on live services. For OSM, prefer a dated Geofabrik extract over live Overpass so results are reproducible (decision to confirm in Stage 5).
