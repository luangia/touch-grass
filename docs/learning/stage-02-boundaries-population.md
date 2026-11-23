# Stage 2 — Boundaries & Population Data (Census / TIGER / City)

> Status: **prepared, not implemented.** Code snippets show the planned approach; none exist yet.
> Facts marked **(verified 2026-11-22)** were checked against the live sources while preparing this lesson.

## 1. Objective and expected outcome

Turn public data into **clean, validated, reproducible GeoParquet files** describing *where people live* and *which areas they belong to*:

| Output (`data/processed/portland-or/`) | Contents |
|---|---|
| `study_boundary.parquet` | City of Portland polygon + 2 km analysis buffer |
| `analysis_areas.parquet` | Neighborhoods **and** council districts, as generic `area_type` rows |
| `blocks.parquet` | 2020 census blocks inside the city: GEOID, population, housing units, representative point |
| `area_membership.parquet` | Many-to-many block ↔ area table (handles overlapping neighborhoods) |
| `block_groups.parquet` | 2020 block groups + ACS 2020–2024 indicators **with margins of error** |
| `validation.json` | Every check run, its expected and actual values, pass/fail |

Two commands do it:

```
touchgrass fetch   portland-or    # download raw files once, with checksums and provenance
touchgrass process portland-or    # raw → clean GeoParquet + validation report (no network)
```

Nothing goes into PostGIS yet (Stage 4). There are no parks yet (Stage 3) and no walk network (Stage 5).

## 2. Why this stage exists

Every TouchGrass metric is ultimately **"what share of *people*…"**. Stage 6 computes a walking distance per block, and Stage 7 turns those into population-weighted percentages per neighborhood. Both are only as good as:

1. **Where people are**: block population and a sensible point to represent each block.
2. **Which area they belong to**: a block-to-neighborhood assignment that handles overlaps and gaps honestly.
3. **Whether the totals reconcile**: if our blocks don't add up to Portland's official population, every percentage downstream is suspect.

It's also the first stage that touches **external data**, so it sets the ETL patterns every later stage reuses: immutable raw files, manifests, caching, and validation.

## 3. Dependencies

- **Stage 1 (complete):** `RegionConfig`, `assert_analysis_crs`, `make_transformer`, the CLI, and the hardcoding guard.
- **Decisions:** D-001 (city limits + buffer), D-002 (2020 blocks; ACS block groups), D-004 (config-driven), D-007 (assignment rules, amended below), D-008 (CRS roles).
- **New Python dependencies:** `geopandas`, `pyogrio` (GDAL-based fast I/O), `pyarrow` (Parquet), `httpx` (HTTP client). Shapely 2 and pandas come with GeoPandas. Versions resolve under the D-014 date cutoff.
- **No database needed.** PostGIS can stay stopped.
- **Disk:** about **480 MB** of raw downloads (see §8.2), git-ignored.

## 4. What I found in the real data (verified 2026-11-22)

This shaped the plan, so it comes before the theory.

### 4.1 City of Portland ArcGIS service
`https://www.portlandmaps.com/od/rest/services/COP_OpenData_Boundary/MapServer`

| Layer | Id | Features | Key fields | Notes |
|---|---|---|---|---|
| City Boundaries | 10 | 57 cities | `CITYNAME`, `AREA` | Maintained by **Metro** (RLIS). Filter `CITYNAME = 'Portland'` → 1 MultiPolygon, 4 parts, 16,420 vertices |
| Neighborhood Boundaries | 3 | 125 | `NAME`, `SHARED` (Y/N) | A planar *partition*: shared areas are separate polygons named like `BOISE/ELIOT` |
| **Neighborhoods (regions)** | 125 | **97** | `NAME`, `ID`, `nbh_districts` | One polygon per neighborhood; **overlaps where areas are shared**. 4 rows are `MC UNCLAIMED #n` with `ID = 0` |
| **City Council Districts** | 1413 | **4** | `DISTRICT`, `pop_total` | Built from 2020 P.L. 94-171 blocks; `pop_total` sums to **652,503** = Portland's official 2020 population |

- The service stores geometry in **EPSG:3857 (Web Mercator)**, but the query API reprojects on request (`outSR=4326`) and returns GeoJSON (`f=geojson`).
- `maxRecordCount` is 500 and pagination is supported. All of our layers fit in one page, but the adapter must paginate anyway, because a future city's layer may not.
- **The real data shows the CRS lesson:** the city's `Shape_Area` (measured in Web Mercator "m²") is 766 km², while `AREA` (true area in ft²) is 376 km². The ratio is **2.04**, exactly 1/cos²(45.5°) from the [CRS deep dive](deep-dive-crs.md).

### 4.2 Census Bureau
- **The Census Data API now requires a key.** Anonymous requests to `api.census.gov` return HTTP 302 → `missing_key.html`.
- **The 2020 TIGER/Line block shapefile already contains population:** `tl_2020_41_tabblock20.zip` (287 MB, statewide) has `POP20`, `HOUSING20`, `ALAND20`, `AWATER20`, and an internal point `INTPTLAT20`/`INTPTLON20`, per the [TIGER/Line 2020 technical documentation](https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.2020.html). So **one file gives block geometry and P.L. 94-171 population, with no API key**.
- **ACS 2020–2024 bulk "table-based summary file":** one pipe-delimited file per table, nationwide, all geographies. Example: `acsdt5y2024-b19013.dat` (17 MB). Rows look like `1500000US410510001011|1729|605`.
  - 1,240 block groups for our three counties.
  - Missing values are **sentinels**: `-666666666` (estimate not computable) and `-222222222` (margin of error not applicable).
- Example of why margins of error matter: block group `410050201012` has median household income **$110,927 ± $77,173**.

## 5. Concepts

### 5.1 Census geography: a strict hierarchy with structured IDs

**Intuition.** The Census Bureau tiles the whole country into nested units. Each unit's ID **contains its parents' IDs**, like a postal address written backwards.

```
State           41                          (2 digits)   Oregon
└ County        41 051                      (+3)         Multnomah
  └ Tract       41 051 000100               (+6)         ~1,200–8,000 people
    └ Block grp 41 051 000100 1             (+1)  = 12   ~600–3,000 people   ← ACS detailed tables
      └ Block   41 051 000100 1 011         (+3)  = 15   a city block       ← 2020 counts
```

Note that the block code `1011` starts with the block-group digit `1`.

**Mechanics that matter for us:**
- **Nesting is exact.** Every block lies inside exactly one block group, tract, and county. So block → block group is an **attribute join on `GEOID20[:12]`**, not a spatial join. That's faster and can't suffer from geometry slivers.
- **Blocks do NOT nest inside cities or neighborhoods.** Blocks respect *legal* boundaries as of the census date (city limits on 1 Jan 2020), but:
  - Portland has annexed land since then, and the city boundary layer is current;
  - neighborhood associations aren't census geography at all.

  So block ↔ city and block ↔ neighborhood need **spatial** assignment.
- **Summary levels.** In the ACS file, the `GEO_ID` prefix encodes the geography type: `1500000US` + 12 digits = block group (summary level 150). That lets us filter a nationwide file with a prefix match: `1500000US41051…`.
- **Water and empty blocks.** Many blocks have `POP20 = 0`: parks, rivers, industrial land, freeway ramps. They matter for maps but carry zero weight in population metrics.

### 5.2 Decennial counts vs ACS estimates

| | 2020 Decennial (P.L. 94-171) | ACS 5-year 2020–2024 |
|---|---|---|
| Method | Attempted full count of everyone | Sample survey (~1 in 8 households over 5 years) |
| Smallest geography | Block | Block group (selected tables) |
| Content | Population, race/ethnicity, housing units | Income, poverty, vehicles, age, … |
| Uncertainty | Deliberate privacy noise (§5.3) | Sampling error, published as a 90% **margin of error** |
| Represents | 1 April 2020 | A 5-year *period average*, not a point in time |

**Margins of error, from first principles.**
- An ACS value of 110,927 ± 77,173 means there's a 90% chance the true value is between $33,754 and $188,100. Estimates with MOE ≥ the estimate are common at block-group level.
- **Coefficient of variation:** CV = (MOE / 1.645) / estimate. 1.645 is the z-score for 90%. CV > 30–40% is commonly treated as unreliable.
- **Aggregating** reduces relative error. For a sum of estimates, the Census approximation is MOE_sum ≈ √(Σ MOEᵢ²). We'll use it when rolling block groups up to neighborhoods (Stage 7). It's *approximate*: it ignores covariance, and medians can't be aggregated this way at all.
- **Consequence for design:** we store MOE next to every estimate, never only the estimate.

### 5.3 Differential privacy in the 2020 Census

**Intuition.** With exact block counts, attackers could reconstruct individuals. For example, a block with one 34-year-old Asian woman, cross-referenced with other data. So for 2020 the Census Bureau added **calibrated random noise** to all published counts except state totals, using its Disclosure Avoidance System (the "TopDown" algorithm). The noise is then post-processed so that counts are non-negative integers and nest consistently.

**Consequences:**
- Individual block counts can be odd, e.g. people in a block with 0 housing units, or the reverse.
- Noise roughly *cancels* when you add many blocks, so **neighborhood totals are reliable while single-block values are not**.
- That's why TouchGrass reports numbers at neighborhood level and only *maps* block results as patterns (D-002). Stage 2 will count the odd blocks (`POP20 > 0 and HOUSING20 = 0`) in the validation report rather than "fix" them.

### 5.4 TIGER/Line shapefiles
- TIGER is the Census Bureau's geographic database; TIGER/Line is its public export.
- 2020 blocks are distributed **per state** (Oregon = FIPS 41), in **NAD83 (EPSG:4269)** degrees.
- `ALAND20`/`AWATER20` are land and water area in m², computed by the Census Bureau. That's a free independent check on our own area calculations (Stage 2 will confirm how close our UTM areas come, rather than assume it).
- `INTPTLAT20`/`INTPTLON20` is the Census **internal point**: a point guaranteed to be inside the polygon (§5.7).

### 5.5 The ArcGIS REST query API
Most US city and county GIS data is published through Esri ArcGIS Server. Each layer exposes a `/query` endpoint:

```
GET {layer_url}/query
    ?where=CITYNAME='Portland'      ← SQL-ish attribute filter, evaluated server-side
    &outFields=CITYNAME,AREA        ← columns to return (* = all)
    &outSR=4326                     ← reproject on the server
    &f=geojson                      ← output format
    &resultOffset=0&resultRecordCount=500   ← pagination
    &orderByFields=OBJECTID         ← stable order, required for safe paging
```

How paging works: the server caps each response at `maxRecordCount` and sets `exceededTransferLimit: true` when more remain. The client repeats with `resultOffset += page_size` until the flag is false. Without a stable `orderByFields`, pages can overlap or skip rows.

This one adapter (`arcgis_feature_layer`) will cover the Portland boundary, neighborhoods, and council districts, and most other US cities.

### 5.6 How GeoPandas works underneath

```
GeoDataFrame  =  pandas DataFrame  +  one "active" GeoSeries column  +  .crs (pyproj CRS)
GeoSeries     =  array of Shapely geometry objects
Shapely 2     =  thin vectorised wrapper over GEOS (C++; also used by PostGIS)
I/O           =  pyogrio → GDAL/OGR drivers (Shapefile, GeoJSON, GPKG, zipped files via /vsizip/)
GeoParquet    =  pyarrow writes columns; geometry as WKB; CRS as PROJJSON in file metadata
```

- Shapely 2 operations run as vectorised loops over C arrays, not Python loops: `gdf.geometry.within(poly)` is one call into GEOS for all rows.
- `pyogrio.read_dataframe(path, where="COUNTYFP20 IN ('051','067','005')")` lets **GDAL filter rows while reading**. We never hold all ~200k Oregon blocks in memory, only the ~40k-ish in our counties. (The exact counts get reported by the validation step rather than assumed.)

### 5.7 Centroid vs representative point vs internal point

```
   ┌──────────────┐
   │   ┌──────┐   │      A crescent / L-shaped / ring-shaped block:
   │   │      │   │        centroid  ●  can fall OUTSIDE the polygon (in the hole)
   │   │  ●   │   │        representative point ✓ always inside
   │   └──────┘   │
   │  ✓           │
   └──────────────┘
```

- **Centroid:** the centre of mass. Fast, but can be outside non-convex shapes.
- **`point_on_surface` / `representative_point`** (GEOS): cheap, **guaranteed inside**, but not necessarily central.
- **Census internal point (`INTPT`):** published by the Census Bureau, inside the polygon, and identical for anyone who reproduces our work.

**Proposed decision (amends D-007):** use the Census `INTPT` as each block's representative point, assert it's inside its polygon, and fall back to `point_on_surface` for any that aren't. It's official, reproducible, and cheaper than computing one.

### 5.8 Point-in-polygon and spatial joins

**Point-in-polygon (ray casting).** Shoot a ray from the point in one direction and count how many polygon edges it crosses. An odd count means inside. Points exactly on an edge are a special case: GEOS's `within` treats a point on the boundary as **not** within, `intersects` treats it as touching.

**Spatial join = index filter + exact refine:**

```
for each block point p:
    candidates = STRtree.query(p.bbox)       # R-tree: neighborhoods whose bounding box contains p
    matches    = [n for n in candidates if p.within(n.geometry)]   # exact GEOS test
```

- GeoPandas builds the **STRtree** (a Sort-Tile-Recursive R-tree packed for static data) automatically inside `gpd.sjoin`.
- Without it, matching 40k blocks to 93 neighborhoods costs 3.7M exact tests. With it, each block tests only the 1–3 neighborhoods whose bounding boxes contain it.
- This is the same filter-and-refine idea PostGIS uses with GiST indexes (Stage 4).

**Joins return every match.** A block whose point is in an overlapping (shared) neighborhood area gets **two rows**: exactly the many-to-many membership we want. A block in an unclaimed area gets **zero rows** with an inner join. We must count both cases deliberately rather than discover them by accident.

### 5.9 Overlaps, gaps, and the modifiable areal unit problem (preview)
- Neighborhood polygons **overlap** in shared areas and **leave gaps** (the 4 `MC UNCLAIMED` areas). So `Σ neighborhood populations ≠ city population`.
- City-wide totals must always be computed **from blocks**, never by summing areas. This was decided in D-007; Stage 2 is where it becomes code.
- Council districts are the opposite case: they **partition** the city exactly and are built from blocks. So they're our reconciliation check. Summing their blocks must give 652,503.

### 5.10 Buffers
- The 2 km buffer (D-001) is computed **in the analysis CRS** (UTM 10N, metres), then converted back to EPSG:4326 for storage.
- GEOS builds a buffer as an approximate Minkowski sum: each vertex becomes a round cap approximated by `quad_segs` segments per quarter circle (default 8).
- Buffering 16k vertices is cheap. Buffering in EPSG:4326 would mean "2000 degrees", the Stage 1 bug again, and `assert_analysis_crs` exists precisely to stop that.

### 5.11 Reproducible ETL
- **Raw is immutable.** `data/raw/<region>/<source>/` holds files exactly as downloaded. Processing reads raw and writes processed, never the other way.
- **Manifest per download:** URL, query parameters, retrieval time, byte size, **SHA-256**, HTTP `ETag`/`Last-Modified`, declared licence, data vintage.
- **Cache by default.** `fetch` skips files whose manifest exists and whose checksum still matches; `--refresh` re-downloads.
- **Idempotent processing.** Running `process` twice yields identical outputs. We check this in tests by comparing sorted contents, not file bytes, because Parquet metadata can differ.
- **Sentinels become nulls at the boundary.** `-666666666` must become `NULL` in the parser. Otherwise one missing block group drags a neighborhood's "average income" to −$666 million.

## 6. How the concepts relate

```
   Census hierarchy (GEOID nesting) ──▶ attribute joins: block → block group → ACS
                 │
   TIGER blocks: geometry + POP20 + INTPT ──▶ representative point
                                                 │ spatial join (STRtree + point-in-polygon)
   City ArcGIS layers (outSR=4326) ──▶ boundary, neighborhoods, council districts
                                                 ▼
                         area_membership (many-to-many; overlaps → 2 rows, gaps → 0)
                                                 │
   Validation: Σ blocks in city ≈ 652,503; Σ blocks per council district = pop_total
   Uncertainty: DP noise (blocks) + ACS MOE (block groups) → report at area level
```

## 7. Fit in the architecture

```
config/regions/portland-or.yaml   ← gains: study_boundary, analysis_areas, census, validation
pipeline/src/touchgrass/
  sources/               (NEW)    adapters: download + manifest; no analysis logic
    http.py              streaming download, SHA-256, manifest, cache
    arcgis.py            ArcGIS layer → GeoJSON (paged, outSR=4326)
    tiger.py             TIGER/Line URL from (vintage, state_fips, layer) — generic, not per city
    acs.py               table-based summary file → filtered rows (prefix match), sentinel → null
  core/                  shared, region-agnostic spatial logic
    crs.py               (Stage 1)
    geometry.py   (NEW)  make_valid, buffer_in_crs, representative points
    assign.py     (NEW)  points → areas many-to-many; boundary filter
  pipeline/       (NEW)  orchestration per step: fetch.py, process.py, validate.py
  cli.py                 + `fetch`, `process` commands
data/raw/portland-or/…  (git-ignored)      data/processed/portland-or/*.parquet (git-ignored)
```

TIGER and ACS URLs are **derived** from `state_fips` and vintages, so a new US city needs no new Census configuration beyond its FIPS codes. Only city-published layers (boundary, neighborhoods) are configured per region.

## 8. Implementation approach

### 8.1 Config additions (illustrative)

```yaml
study_boundary:
  adapter: arcgis_feature_layer
  url: https://www.portlandmaps.com/od/rest/services/COP_OpenData_Boundary/MapServer/10
  where: "CITYNAME = 'Portland'"
  attribution: "Metro Data Resource Center (RLIS) via City of Portland"

analysis_areas:
  - area_type: neighborhood
    adapter: arcgis_feature_layer
    url: …/MapServer/125
    where: "ID <> 0"            # excludes the 4 'MC UNCLAIMED' areas (decision Q2)
    key_field: ID
    name_field: NAME
    allow_overlaps: true
  - area_type: council_district
    adapter: arcgis_feature_layer
    url: …/MapServer/1413
    key_field: DISTRICT
    name_field: DISTRICT
    allow_overlaps: false
    population_check_field: pop_total   # validation: Σ block POP20 per district must equal this

census:
  decennial_vintage: 2020
  acs:
    year: 2024                 # ACS 5-year 2020–2024
    tables: [B01003, B19013, B03002, B25044]    # decision Q4

validation:
  expected_population: 652503   # 2020 Census, City of Portland
  population_tolerance: 0.01
```

The Pydantic schema gets discriminated-union adapter models (`adapter: Literal["arcgis_feature_layer"]`), so typos in adapter names or fields fail at load time, as in Stage 1.

### 8.2 Download volume (sizes verified)

| File | Size |
|---|---|
| `tl_2020_41_tabblock20.zip` | 287 MB |
| `tl_2020_41_bg.zip` | 16 MB |
| ACS B01003 / B19013 / B03002 / B25044 | 17 + 17 + 79 + 60 MB |
| ArcGIS layers (3 queries) | a few MB |
| **Total** | **≈ 480 MB** (once; cached) |

ACS files are **filtered while streaming**: only lines starting with `1500000US41{county}` are kept. So the *stored* raw ACS data is small, and the manifest records the source checksum *and* the filter used.

### 8.3 Processing steps (`touchgrass process`)

```
1  boundary  ← GeoJSON; make_valid; dissolve to one MultiPolygon
             → buffer_m in analysis CRS → back to 4326           (study_boundary.parquet)
2  areas     ← each analysis_areas source → standard columns
             (region_id, area_type, source_key, name, geometry); validity; uniqueness of key
3  blocks    ← pyogrio read with where COUNTYFP20 IN (...)  (EPSG:4269 → 4326)
             rep point = INTPT (assert inside; fallback point_on_surface)
             keep blocks whose rep point is within the city boundary
4  membership← sjoin(rep points, areas, predicate="within") → many-to-many table
5  block grp ← TIGER BG geometries + ACS tables (sentinels → null), join on 12-digit GEOID
6  validate  → validation.json + console summary; non-zero exit on any failed hard check
```

### 8.4 Validation checks (hard = fails the run; soft = reported)

| Check | Type | Expected |
|---|---|---|
| CRS of every output is EPSG:4326 | hard | — |
| All geometries valid, non-empty | hard | — |
| Block GEOIDs unique, 15 digits; BG GEOIDs 12 digits | hard | — |
| Every block's `GEOID20[:12]` exists in the BG file | hard | 100% |
| Σ POP20 of blocks assigned to the city | hard | 652,503 ± 1% |
| Σ POP20 per council district vs `pop_total` | hard | **exact match** (they're built from blocks) |
| Each block in exactly one council district | hard | — |
| Neighborhood count after filter | soft | 93 (97 − 4 unclaimed) |
| Blocks / population in 0 neighborhoods, in ≥ 2 neighborhoods | soft | reported, with lists |
| ACS block groups joined / with null estimates / with CV > 40% | soft | reported |
| Blocks with `POP20 > 0 and HOUSING20 = 0` (DP artefacts) | soft | reported |
| Our UTM land area of blocks vs Census `ALAND20 + AWATER20` | soft | within ~0.1% (independent CRS check) |
| INTPT outside its own polygon | soft | expected 0; fallback count reported |

**Prediction to test:** the council-district match may not be exact for blocks cut by post-2020 annexations. If it isn't, the report will show exactly which blocks differ, and we'll explain rather than loosen the check silently.

### 8.5 Testing strategy
- **Unit tests (no network, fast):** synthetic polygons and points for membership rules: overlap → 2 rows, gap → 0 rows, point on edge, straddling block decided by its representative point. Also the ACS line parser (sentinels, prefix filter), GEOID prefix join, buffer-in-CRS distance check, manifest writing and cache hits.
- **Adapter tests with mocked HTTP:** `httpx.MockTransport` serves canned ArcGIS responses, including a **two-page** response with `exceededTransferLimit`, so pagination is tested without the network.
- **Data tests on the real files** (`@pytest.mark.data`, skipped unless the processed files exist): the validation checks above, asserted.
- **Idempotency test:** run `process` twice and assert identical sorted contents.
- **Hardcoding guard** (Stage 1) keeps running: no Portland URLs, names, or numbers may leak into `src/`.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | Neighborhood source layer | **125 "Neighborhoods (regions)"**: 97 polygons, overlap in shared areas. Or 3 "Neighborhood Boundaries": 125 non-overlapping pieces incl. `A/B` shared pieces | **125**: one polygon per neighborhood fits our many-to-many model; shared areas count for both neighborhoods, as the city intends |
| Q2 | The 4 `MC UNCLAIMED` areas | Exclude from neighborhoods (`where: ID <> 0`; residents still counted city-wide and reported as "no neighborhood association"), or keep as pseudo-neighborhoods | **Exclude**, with population reported |
| Q3 | Add council districts as a second `area_type` | Yes / no | **Yes**: 4 polygons, exact population check, proves the multi-area design at near-zero cost |
| Q4 | ACS tables | B01003 total pop · B19013 median household income · B03002 Hispanic/Latino origin by race · B25044 vehicles available by tenure · optional B01001 age (+191 MB) | **First four.** Vehicle availability directly relates to dependence on walking; age can be added later |
| Q5 | Census access | Bulk files (no key; ~480 MB once; fully reproducible) or Census API (small downloads; you register a free key stored in `.env`) | **Bulk files**: no credentials, cacheable, checksum-able |
| Q6 | Representative point (amends D-007) | Census `INTPT` (with inside check) or computed `point_on_surface` | **Census INTPT** |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)

- [ ] `touchgrass fetch portland-or` downloads every source once; each has a manifest with URL, SHA-256, size, timestamp, licence/attribution, vintage. A second run downloads nothing.
- [ ] `touchgrass process portland-or` runs offline and writes the 5 GeoParquet files + `validation.json`; all **hard** checks pass.
- [ ] City population from blocks is within ±1% of **652,503**; the council-district reconciliation is exact or every discrepancy is explained.
- [ ] Overlap and gap behaviour is documented with **real counts** (blocks and people in 0 / 1 / ≥2 neighborhoods).
- [ ] Re-running `process` produces identical contents.
- [ ] `ruff`, `mypy --strict`, and `pytest` pass; the hardcoding guard passes.
- [ ] ARCHITECTURE §9 licence entries for City/Metro layers updated from **[verify]** to what the sources actually state.

## 11. Proposed implementation checklist

- [ ] Add deps (`geopandas`, `pyogrio`, `pyarrow`, `httpx`) under the date cutoff; confirm GDAL wheels install on arm64
- [ ] Extend config schema: `study_boundary`, `analysis_areas` (discriminated adapters), `census`, `validation`; update `portland-or.yaml`
- [ ] `sources/http.py`: streaming download, SHA-256, manifest, cache, `--refresh`
- [ ] `sources/arcgis.py`: paged query with stable order, `outSR=4326`, GeoJSON; mocked-HTTP tests incl. 2 pages
- [ ] `sources/tiger.py` and `sources/acs.py`: derived URLs, streaming prefix filter, sentinel → null, MOE kept
- [ ] `core/geometry.py`: `make_valid`, `buffer_in_crs`, INTPT with inside check and fallback
- [ ] `core/assign.py`: points → areas many-to-many; boundary filter
- [ ] `pipeline/fetch.py`, `process.py`, `validate.py`; CLI commands `fetch`, `process`
- [ ] Unit, adapter and data tests; idempotency test
- [ ] Run against real data; review `validation.json`; investigate any discrepancy
- [ ] Docs: implementation log, status, ARCHITECTURE (sources/ layout, licences), D-007 amendment, this lesson's "what was built" section

## 12. Questions to think about before we start
1. Why is block → block group an attribute join but block → neighborhood a spatial join?
2. A neighborhood's population is the sum of its blocks. Why can't the city's population be the sum of its neighborhoods?
3. If a block straddles the city boundary, what decides whether its residents count as Portlanders in our model, and what's the bias?
4. What would happen to a neighborhood's average median income if one block group's `-666666666` slipped through as a number?

## References
- TIGER/Line 2020 technical documentation (record layouts incl. POP20/HOUSING20): https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.2020.html
- TIGER/Line 2020 shapefiles: https://www.census.gov/geographies/mapping-files/2020/geo/tiger-line-file.html
- ACS 2024 table-based summary file: https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/
- ACS 5-year API (requires key): https://www.census.gov/data/developers/data-sets/acs-5year.html
- Census, "Understanding and Using ACS Data" (MOE, CV, aggregation formulas): https://www.census.gov/programs-surveys/acs/library/handbooks.html
- 2020 Census Disclosure Avoidance System: https://www.census.gov/programs-surveys/decennial-census/decade/2020/planning-management/process/disclosure-avoidance.html
- Esri ArcGIS REST API, Query (Feature Service / Map Service layer): https://developers.arcgis.com/rest/services-reference/enterprise/query-feature-service-layer/
- City of Portland boundary services: https://www.portlandmaps.com/od/rest/services/COP_OpenData_Boundary/MapServer
- GeoPandas spatial joins: https://geopandas.org/en/stable/docs/user_guide/mergingdata.html
