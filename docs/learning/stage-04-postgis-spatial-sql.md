# Stage 4: PostGIS Database & Spatial SQL

> Status: **implemented 2026-11-23, awaiting your review.** §13 says what was built; §14 records the real query plans. Where a measurement contradicted this lesson's prediction, the prediction is corrected in place and marked **(corrected)**.
> Facts marked **(verified 2026-11-23)** were checked against the running database or the package index while preparing this lesson.

## 1. Objective and expected outcome

Move the validated Stage 2–3 outputs from GeoParquet files into a **well-designed, constrained, indexed PostGIS schema**, and learn to query it efficiently, including **measuring** what a spatial index does.

At the end of the stage:
- `touchgrass db migrate` creates or upgrades the schema from versioned SQL files.
- `touchgrass load portland-or` loads one region **atomically and idempotently**: run it twice and you get the same rows.
- `sql/explore/` holds documented example queries, and the lesson records their real `EXPLAIN ANALYZE` plans with and without a GiST index.
- An integration test proves that **PostGIS and GeoPandas agree** on block → neighborhood membership.

No new analysis happens in this stage. The database becomes the system of record that Stages 6–8 write to and serve from.

## 2. Why this stage exists

GeoParquet files are great for a single pipeline run, but the application needs things files can't give:

| Need | Files | Database |
|---|---|---|
| Many regions, one place | A folder per region | One schema, rows keyed by `region_id` |
| Integrity | Nothing stops a bad row | Types, SRIDs, keys, foreign keys, checks |
| Answering questions ("which blocks are in neighborhood X?") | Load everything into memory | An indexed query returns just the answer |
| Serving an API (Stage 8) | Re-read files per request | Connection pool, SQL, vector tiles generated in SQL (`ST_AsMVT`) |
| Concurrent readers | Fragile | Designed for it |

It's also where the project's heaviest learning lives: **how a spatial index works, and how to prove it's being used.**

## 3. Dependencies

- **Stages 2–3 (complete):** `data/processed/portland-or/` with blocks, areas, membership, block groups + ACS, green candidates, parks.
- **PostGIS container:** it had been removed; restarted while preparing this lesson on the existing `touchgrass_tg_pgdata` volume. PostgreSQL 17.11, PostGIS 3.6.4, GEOS 3.14.1, PROJ 9.8.1, **healthy (verified 2026-11-23)**. The database holds no tables of ours yet; `spatial_ref_sys` has 8,500 CRS definitions.
- **New Python dependency:** `psycopg[binary]` 3.x (3.3.8 resolves under the D-014 cutoff, bundling libpq 18).
- **Decisions:** D-004 (shared tables keyed by `region_id`), D-008 (storage in EPSG:4326), D-010 (MVT via `ST_AsMVT` later), plus new decisions in §9.

## 4. What the environment showed (verified 2026-11-23)

- The image enables extra extensions by default: `postgis_topology` and `postgis_tiger_geocoder`. They create their own schemas (`topology`, `tiger`, `tiger_data`). Our tables will live in a dedicated `touchgrass` schema, so we never mix with them.
- Every function needed later exists: `ST_DWithin`, `ST_PointOnSurface`, `ST_Subdivide`, `ST_AsMVT`, `ST_AsMVTGeom`, `ST_TileEnvelope`.
- Defaults: `shared_buffers 128MB`, `work_mem 4MB`, `random_page_cost 4`. These are untuned, which is fine at our data size. We'll note in query plans where they matter.
- **GDAL's PostgreSQL driver is not included in the pyogrio wheel**, so `ogr2ogr`-style loading would mean installing GDAL separately.
- The container runs **amd64 under emulation** (D-013), so absolute timings will be slower than native. *Ratios* between indexed and unindexed plans are what we'll compare.

## 5. Concepts

### 5.1 What PostGIS adds to PostgreSQL

PostgreSQL is extensible: you can add new **types**, **functions**, **operators**, and **index methods** without changing the core. PostGIS uses all four:

| PostgreSQL extension point | PostGIS addition | Example |
|---|---|---|
| Type | `geometry`, `geography` (+ `raster`) | `geom geometry(MultiPolygon, 4326)` |
| Function | ~1,000 `ST_*` functions, most backed by GEOS (the same C++ library Shapely uses) | `ST_Intersects(a, b)` |
| Operator | Bounding-box operators: `&&` (boxes overlap), `<->` (distance, for nearest-neighbour ordering) | `a.geom && b.geom` |
| Index support | Operator classes so the **GiST** index method understands geometries | `CREATE INDEX … USING gist (geom)` |

Because Shapely and PostGIS share GEOS, the same predicate should give the same answer in both. That's exactly what the Stage 4 parity test checks.

### 5.2 `geometry` vs `geography`

| | `geometry` | `geography` |
|---|---|---|
| Model | Cartesian plane in the column's SRID | Coordinates on the WGS 84 ellipsoid (lon/lat only) |
| `ST_Distance` units | The CRS's units (degrees for 4326!) | **Metres**, computed geodesically |
| Functions | Everything | A subset (distance, DWithin, area, length, intersects, buffer via casts…) |
| Cost | Cheap planar maths | More expensive spheroidal maths |
| Best for | Topology, overlays, containment, tiles | Correct metres from lon/lat data without choosing a projection |

```sql
-- Same two points (downtown Portland → near Gresham), three answers:
SELECT ST_Distance(a, b)                       AS degrees,   -- 0.2475 "degrees": meaningless
       ST_Distance(a::geography, b::geography) AS metres,    -- ≈ 19,441 m geodesic
       ST_Distance(ST_Transform(a, 26910), ST_Transform(b, 26910)) AS utm_m   -- ≈ 19,434 m
FROM (SELECT ST_SetSRID(ST_MakePoint(-122.6765, 45.5231), 4326) a,
             ST_SetSRID(ST_MakePoint(-122.4302, 45.4984), 4326) b) t;
```

(Those three numbers are the ones measured in the [CRS deep dive](deep-dive-crs.md). We'll re-measure them in PostGIS as the first exploration query.)

**Multi-region consequence.** A column typed `geometry(MultiPolygon, 26910)` can't hold Chicago (EPSG:26916). So the database stores **one canonical CRS, 4326** (D-008). Metric questions use `geography` casts or a transform of the *constant* side (§5.6). Heavy metric analysis stays in Python, in the region's analysis CRS (Stages 5–6).

### 5.3 SRIDs and typed geometry columns

`geometry(MultiPolygon, 4326)` is a **type modifier**: PostgreSQL rejects inserts with a different geometry type or SRID. That's free integrity checking:

```sql
INSERT INTO touchgrass.parks (…, geom) VALUES (…, ST_GeomFromText('POINT(0 0)', 3857));
-- ERROR: Geometry SRID (3857) does not match column SRID (4326)
```

Mixing SRIDs in one function call is also refused: `ERROR: Operation on mixed SRID geometries`. PostGIS forces you to transform explicitly, just as `assert_analysis_crs` does in Python.

### 5.4 How a GiST spatial index works

**The problem.** "Which neighborhood contains this point?" checked exactly against all 93 polygons is 93 point-in-polygon tests, each costing O(vertices). For 12,357 blocks that's 1.15 M exact tests. An index has to avoid most of them.

**The idea: filter with boxes, refine with geometry.**

```
          query: ST_Intersects(block.rep_point, area.geom)
                         │
   1. INDEX FILTER   area.geom && block.rep_point     ← cheap: compare 4 numbers (bounding boxes)
                         │  candidates (usually 1–3 areas)
   2. RECHECK        exact GEOS point-in-polygon      ← expensive: walk the polygon's edges
                         │
                       result
```

`ST_Intersects` is defined so the planner adds the `&&` box test automatically. That's why it's "index-aware", while something like `ST_Distance(a, b) < 100` isn't.

**What GiST stores.** GiST (Generalized Search Tree) is a balanced tree, a framework that the PostGIS operator class turns into an **R-tree**:

```
                    [root: boxes covering everything]
                   /                │                \
      [box covering NW]     [box covering NE]     [box covering S]       ← internal nodes: bounding boxes
       /     |     \              …                    …
   leaf: (bbox, row pointer) (bbox, row pointer) …                      ← one entry per row
```

- **Search:** descend into every child whose box overlaps the query box. Boxes may overlap each other, so several branches may be visited, unlike a B-tree's single path.
- **Insert:** the `penalty` function picks the child whose box grows least. When a page overflows, `picksplit` divides entries into two groups with small, low-overlap boxes.
- **Index entries are boxes, not geometries**, so the index is small and fast but *lossy*. Every hit must be rechecked against the real geometry (step 2 above). In `EXPLAIN` you'll see `Index Cond: (geom && …)` followed by a recheck or filter on the exact predicate.

This is the same **filter-and-refine** idea as Shapely's STRtree in Stages 2–3. The difference is that GiST is *persistent* (stored on disk, maintained on insert), while STRtree is built in memory for one join.

### 5.5 When an index doesn't help, and why big polygons hurt

- **Low selectivity:** if most rows match anyway, a sequential scan is cheaper than jumping around the index. The planner decides using statistics, so run `ANALYZE` after loading, or the planner is guessing.
- **Huge, complex polygons:** the city boundary has 16,422 vertices, and Forest Park's bounding box covers half of northwest Portland. The box filter passes almost everything, and each exact recheck walks thousands of edges. The fix is `ST_Subdivide`: split one big polygon into many small ones with tight boxes, so both filter and refine get cheap.
  - **(corrected)** The measurements (§14, query 03) show this is true for **polygon-against-big-shape** tests, which ran 39× faster subdivided. It is *not* true for **points in one big polygon**. There PostGIS builds a *prepared geometry* once (an internal edge index, cached across the rows of the join), so the raw boundary was already fast and subdividing made it slower. Measure before you subdivide.
- **Functions on the indexed column:** `WHERE ST_DWithin(ST_Transform(geom, 26910), :pt, 800)` can't use an index on `geom`. The index is on `geom`, not on the transformed value ([CRS deep dive §8](deep-dive-crs.md)). Fixes: transform the constant side, use `geography`, or create an **expression index** on exactly that expression.

### 5.6 Reading a query plan

`EXPLAIN (ANALYZE, BUFFERS)` runs the query and shows the plan tree, with *estimated* vs *actual* rows and timings. The plan below is an **illustrative shape, not a measurement**; real plans are recorded during implementation:

```
Nested Loop  (cost=0.15..4321.00 rows=12357 width=…) (actual time=0.05..210.3 rows=12597 loops=1)
  ->  Seq Scan on census_blocks b  (actual rows=12357 loops=1)
  ->  Index Scan using analysis_areas_geom_gist on analysis_areas a  (actual rows=1 loops=12357)
        Index Cond: (geom && b.rep_point)
        Filter: st_intersects(b.rep_point, geom)
        Rows Removed by Filter: 0.4
```

What to read:
- **Node types:** `Seq Scan` (read every row), `Index Scan` / `Bitmap Index Scan` (use an index), `Nested Loop` (for each outer row, probe the inner side).
- **`loops`:** how many times a node ran. Per-loop numbers multiply.
- **Estimated vs actual rows:** a big mismatch means stale statistics, so the planner made a poor choice.
- **`Rows Removed by Filter`:** the index's false positives, i.e. the cost of lossiness.
- **`Buffers: shared hit/read`:** pages from memory vs disk.

To *prove* the index matters, we'll run the same join with the index and with it disabled (`SET enable_indexscan = off; SET enable_bitmapscan = off;` in a transaction) and record both plans and times.

### 5.7 Schema design for many regions

Following D-004, every table has `region_id` and a **composite primary key** that starts with it:

```
regions             (region_id PK, name, analysis_crs, config jsonb, loaded_at)
datasets            (region_id, source_id, url, sha256, retrieved_at, vintage, attribution)   provenance from manifests
study_boundaries    (region_id PK, boundary geometry(MultiPolygon,4326), analysis_buffer geometry(MultiPolygon,4326), buffer_m)
analysis_areas      (region_id, area_id, area_type, source_key, name, check_population, geom)     PK (region_id, area_id)
census_blocks       (region_id, geoid20, county_fips, pop20, housing20, aland20, awater20, area_m2,
                     frac_in_boundary, rep_point geometry(Point,4326), rep_point_source, geom)   PK (region_id, geoid20)
area_membership     (region_id, area_id, geoid20)  PK all three; FKs → analysis_areas, census_blocks
block_groups        (region_id, geoid, geom)                                                     PK (region_id, geoid)
acs_estimates       (region_id, geoid, variable, estimate, moe, vintage)                         PK (region_id, geoid, variable)
green_candidates    (region_id, osm_type, osm_id, name, tags jsonb, included, park_class, reason, rule_tag, area_m2, geom)
parks               (region_id, park_id, name, park_class, area_m2, subtracted_m2, n_members, member_ids text[], member_names, geom)
schema_migrations   (version PK, filename, sha256, applied_at)
```

Design choices worth understanding:
- **Why `(region_id, geoid20)` and not just `geoid20`?** A future "Portland Metro" region would contain the same blocks as "Portland". Each region owns its copy, so reloading one region never touches another's rows.
- **Why a long ACS table?** ACS tables have different numbers of variables (B19013: 1; B03002: 21). A long `(geoid, variable, estimate, moe)` table never needs a migration when a region adds a table, and it keeps estimate and MOE together on one row. A view can pivot it for convenience.
- **Why keep `green_candidates`?** Auditability: the API (Stage 8) can show *why* a place isn't counted as a park.
- **Constraints as documentation:** `CHECK (pop20 >= 0)`, `CHECK (area_type ~ '^[a-z0-9_]+$')`, `CHECK (reason IN (…))`, `CHECK (ST_IsValid(geom))`. The database enforces what validation checked.
- **Indexes:** GiST on every geometry column (`geom`, plus `rep_point` on blocks); B-tree on `(region_id, area_type)` and on the `area_membership` foreign keys.

### 5.8 Migrations

Schema changes are versioned SQL files applied in order, exactly once, recorded in a table:

```
db/migrations/0001_schema.sql        CREATE SCHEMA touchgrass; regions; datasets; …
db/migrations/0002_census.sql        census_blocks, block_groups, acs_estimates, area_membership
db/migrations/0003_parks.sql         green_candidates, parks
db/migrations/0004_indexes.sql       GiST + B-tree indexes
```

The runner applies each pending file **inside a transaction** (PostgreSQL DDL is transactional), records `(version, filename, sha256)`, and **refuses to run if an already-applied file has changed** (its checksum no longer matches). That's the same "trust only verified inputs" rule as the raw-data manifests.

### 5.9 Loading data fast and safely

- **`COPY` vs `INSERT`.** `INSERT` is one statement per row: parse, plan, execute, and a network round trip each time. `COPY` streams rows in bulk through one statement and is typically **an order of magnitude faster**. psycopg 3 exposes it with `cursor.copy(...)` and `write_row(...)`.
- **Geometry over the wire:** send **WKB** (the binary form from Stage 1) and convert with `ST_GeomFromWKB(..., 4326)` on the way in. That's no text parsing and no precision loss.
- **Atomic, idempotent reload of one region:**

```sql
BEGIN;
  DELETE FROM touchgrass.area_membership WHERE region_id = 'portland-or';   -- children first (FKs)
  … other tables …
  COPY touchgrass.census_blocks (...) FROM STDIN (FORMAT binary);         -- via a staging table
  … all tables …
COMMIT;      -- readers see the old data or the new data, never half of each
ANALYZE touchgrass.census_blocks;  …                                     -- fresh planner statistics
```

If anything fails midway, `ROLLBACK` leaves the previous load untouched.

## 6. How the concepts relate

```
GeoParquet (Stages 2–3) ──WKB──▶ psycopg COPY ──▶ typed tables (SRID + type checks, PK/FK/CHECK)
                                                        │
                       migrations (versioned SQL, checksummed) define them
                                                        │
                    GiST index = R-tree of bounding boxes ──▶ filter (&&) → refine (GEOS)
                                                        │
               EXPLAIN ANALYZE proves which path the planner chose, and why
                                                        │
               parity test: PostGIS membership == GeoPandas membership (same GEOS predicate)
```

## 7. Fit in the architecture

```
db/migrations/000N_*.sql         (NEW) schema, versioned; built inside the package at
                                 pipeline/src/touchgrass/db/migrations/ (see §13)
sql/explore/*.sql                (NEW) documented example queries + recorded plans
pipeline/src/touchgrass/
  db/connection.py               (NEW) settings from .env (POSTGRES_*) or DATABASE_URL; psycopg connect
  db/migrate.py                  (NEW) apply pending migrations; checksum guard
  db/load.py                     (NEW) GeoParquet → staging COPY → replace region rows, one transaction
  cli.py                         + `db migrate`, `db status`, `load <region>`
```

The pipeline stays the only writer. The API (Stage 8) will connect read-only to the same schema.

## 8. Implementation approach

### 8.1 Commands (planned)

```
touchgrass db migrate              # apply pending migrations; prints applied versions
touchgrass db status               # applied/pending migrations; row counts per region
touchgrass load portland-or        # load processed outputs; ANALYZE; print counts + timings
```

### 8.2 Load mapping

| Processed file | Table(s) | Notes |
|---|---|---|
| `study_boundary.parquet` | `study_boundaries` | Two rows (boundary, buffer) → one row, two columns |
| `analysis_areas.parquet` | `analysis_areas` | |
| `blocks.parquet` | `census_blocks` | `rep_point` built from `rep_lon` / `rep_lat` |
| `area_membership.parquet` | `area_membership` | |
| `block_groups.parquet` | `block_groups` + `acs_estimates` | Wide columns melted to long rows; nulls (sentinels) stay null |
| `green_candidates.parquet` | `green_candidates` | `tags` JSON → `jsonb` |
| `parks.parquet` | `parks` | `member_ids` `;`-joined → `text[]` |
| raw manifests | `datasets` | Provenance: URL, SHA-256, retrieval time, attribution, vintage |
| config | `regions` | `config` stored as `jsonb` for traceability |

`park_discrepancies.parquet` and `validation.json` stay as files: they're reports, not data the app serves.

### 8.3 Exploration queries (`sql/explore/`, each with its recorded plan in this lesson)

1. **CRS check in SQL:** the three distances from §5.2 (degrees, geography, UTM).
2. **Point-in-polygon join:** blocks per neighborhood via `ST_Intersects(rep_point, geom)`, with vs without GiST, using `EXPLAIN (ANALYZE, BUFFERS)`.
3. **Big-polygon effect:** blocks inside the city boundary, raw vs `ST_Subdivide(boundary, 256)`.
4. **KNN:** the 5 nearest parks to a point with `ORDER BY geom <-> :pt LIMIT 5`. This is an index-assisted nearest-neighbour search by *bounding-box* distance, with a note on why that isn't walking distance (Stage 6).
5. **Metres from 4326:** parks within 800 m of a point, three ways: `ST_DWithin(geography)`, transform the constant side, and the anti-pattern `ST_Transform(geom)`. Compare plans.

### 8.4 Testing strategy
- **Migration tests:** apply all migrations to an empty **test database** (`touchgrass_test`, created and dropped by the tests); re-running is a no-op; a modified applied migration is refused.
- **Constraint tests:** wrong SRID, wrong geometry type, a negative population, and an unknown reason are each rejected by the database.
- **Load tests (synthetic region from Stages 2–3):** load → counts equal the GeoParquet row counts; load again → identical counts and identical content (ordered checksums per table); a failure injected midway leaves the previous load intact (atomicity).
- **Parity test (real data, `-m db and data`):** `area_membership` computed in SQL (`ST_Intersects(rep_point, geom)`) equals the Stage 2 GeoPandas membership, row for row.
- **Index test:** `EXPLAIN (FORMAT JSON)` of the join contains an index scan on the GiST index (asserted on the plan structure, not on timings).
- Tests needing the database are marked `db` and **skip cleanly** if it isn't reachable, so the default test run stays offline.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | How to load | **psycopg 3 binary `COPY` (WKB) + transactional replace per region** · GeoPandas `to_postgis` (SQLAlchemy + GeoAlchemy2; convenient but it creates its own tables, which conflicts with migrations, and hides the SQL) · `ogr2ogr` (needs a separate GDAL install) | **psycopg COPY.** Explicit SQL, fastest, and the loading mechanics are themselves a learning goal |
| Q2 | Migrations | **Numbered plain SQL files + small runner with checksums** · Alembic | **Plain SQL + runner.** You read and write the real DDL; Alembic's value (autogenerate from ORM models) doesn't apply without an ORM |
| Q3 | Metric queries in the DB | **Store 4326 only; use `geography` casts or transform the constant side** · per-region analysis-CRS columns · per-region partial expression indexes | **4326 only** (heavy metric work stays in Python). Expression indexes are shown as an exercise, not built into the schema |
| Q4 | ACS storage | **Long table `acs_estimates`** · wide columns · `jsonb` per block group | **Long table** (+ a convenience view) |
| Q5 | What to load | **Everything processed except reports** (discrepancies, validation stay as files) · parks only · everything | **Everything except reports** |
| Q6 | Test database | **Separate `touchgrass_test` database in the same container, created and dropped by tests** · reuse the main database | **Separate test database**: tests never touch your loaded data |
| Q7 | Connection settings | **Read the repo's `.env` (`POSTGRES_*`), `DATABASE_URL` overrides** (no new dependency) · add `pydantic-settings` · environment variables only | **`.env` + override** |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)

- [ ] `touchgrass db migrate` builds the schema on an empty database; re-running applies nothing; editing an applied migration is detected and refused.
- [ ] `touchgrass load portland-or` loads all tables in one transaction; row counts equal the GeoParquet counts; every geometry has SRID 4326 and passes `ST_IsValid`.
- [ ] Reloading gives identical table contents (ordered checksums), and a failed load leaves the previous data intact.
- [ ] **Parity:** PostGIS `ST_Intersects(rep_point, geom)` membership equals the GeoPandas membership exactly.
- [ ] **Index evidence:** the point-in-polygon join recorded with and without GiST (plans + timings + speed-up) in this lesson, and a test asserts the planner uses the index.
- [ ] The five exploration queries are in `sql/explore/` with their plans explained here.
- [ ] ruff, mypy --strict, pytest pass; `db` tests skip cleanly without a database; the hardcoding guard passes.
- [ ] Docs: decisions recorded (D-019), ARCHITECTURE §4 updated to the real schema, lesson "what was built".

## 11. Proposed implementation checklist

- [ ] Add `psycopg[binary]` under the date cutoff
- [ ] `db/connection.py`: settings (`.env` / `DATABASE_URL`), connect, reachable check
- [ ] Migrations `0001`–`0004` (schema, census, parks, indexes) + `db/migrate.py` (checksums, transactional apply) + `touchgrass db migrate|status`
- [ ] `db/load.py`: per-table loaders (WKB COPY into temp staging tables), transactional replace by `region_id`, provenance from manifests, `ANALYZE`; `touchgrass load`
- [ ] Tests: migrations, constraints, synthetic load + idempotency + atomicity, parity (real data), index-use assertion; `db` marker with skip
- [ ] Run on real data; write `sql/explore/01–05` and record plans and timings in this lesson
- [ ] Docs: D-019, ARCHITECTURE §4 schema, implementation log, status, lesson "what was built"

## 12. Questions to think about before we start
1. Why is a GiST index on polygons called *lossy*, and where does the planner pay for that?
2. `ST_Intersects` can use an index but `ST_Distance(a, b) < 800` can't. What's the difference in how they're defined?
3. Why does a composite primary key `(region_id, geoid20)` make per-region reloads safe in a way `geoid20` alone wouldn't?
4. Our loader deletes and re-inserts a region inside one transaction. What does another session reading `parks` at the same moment see, and which PostgreSQL feature guarantees that?

## References
- PostGIS manual: geometry vs geography, spatial indexes, `ST_Subdivide`, `ST_AsMVT`. https://postgis.net/docs/
- PostGIS workshop, "Spatial Indexing" and "Geography". https://postgis.net/workshops/postgis-intro/
- PostgreSQL docs: GiST (internals, `penalty`, `picksplit`), `EXPLAIN`, `COPY`, transactional DDL, MVCC. https://www.postgresql.org/docs/17/
- psycopg 3: COPY (`cursor.copy`, `write_row`, binary format). https://www.psycopg.org/psycopg3/docs/basic/copy.html
- Guttman, A. (1984). *R-trees: a dynamic index structure for spatial searching.* SIGMOD.
- Hellerstein, Naughton, Pfeffer (1995). *Generalized Search Trees for Database Systems.* VLDB.

## 13. What was built (2026-11-23)

| Piece | Where | What it does |
|---|---|---|
| Settings and connection | `pipeline/src/touchgrass/db/connection.py` | `DATABASE_URL` → `POSTGRES_*` env → repo `.env` → defaults. `describe()` prints `user@host:port/db` and never the password. |
| Migrations | `pipeline/src/touchgrass/db/migrations/0001_core.sql` … `0004_indexes.sql` | The schema in ARCHITECTURE §4: 10 tables, 1 view, 7 GiST and 3 B-tree indexes, and the constraints from §5.7. |
| Migration runner | `pipeline/src/touchgrass/db/migrate.py` | Bootstraps `schema_migrations`, applies each pending file in its own transaction under an advisory lock, and refuses edited history (checksum) or a database newer than the code. |
| Loader | `pipeline/src/touchgrass/db/load.py` | Re-validates outputs, reads provenance manifests, then runs one transaction: `DELETE` the region (cascade) → binary `COPY` into temp staging tables → `INSERT … SELECT` with conversions → `ANALYZE`. Also provides `row_counts()` and `content_digests()`. |
| CLI | `touchgrass db migrate`, `touchgrass db status`, `touchgrass load <region>` | |
| Exploration | `sql/explore/01–05_*.sql` | The five queries from §8.3, runnable with `psql` (see the README there). |
| Tests | `tests/test_db.py` (18), `tests/test_db_settings.py` (4) | Migrations (no-op rerun, edited file, newer database, failed file rolls back), 6 constraint rejections, load counts, idempotent reload with region isolation, atomic failure, refusal of unvalidated outputs or an unmigrated database, ACS reshaping, **parity** and **index use** on real data. |

**Two changes from the plan:**
- **Migrations live in the package, not at the repo root.** An installed `touchgrass` then always carries the schema it was written for (D-019).
- **`area_membership` has no `area_type` column.** It is derivable by joining to `analysis_areas`, and storing it twice would let the two copies disagree.

**Real Portland load:** 63,817 rows across 9 tables (63,813 and 473 parks after D-018 amendment 2), committed in **1.7 s**. The tables are datasets 11, study_boundaries 1, analysis_areas 97, census_blocks 12,357, area_membership 24,785, block_groups 533, acs_estimates 20,254 (533 block groups × 38 variables), green_candidates 5,302 and parks 477. Every geometry column has SRID 4326 only and `ST_IsValid` everywhere. `sum(pop20)` = 652,503. A reload produced identical content digests for all 10 tables.

**Parity:** the SQL membership `ST_Intersects(rep_point, geom)` gives 24,785 pairs, and the Stage 2 GeoPandas membership gives 24,785. **Zero differ in either direction.**

## 14. Measured query plans (2026-11-23, PostGIS under amd64 emulation)

Times are `EXPLAIN ANALYZE` execution times. Where the same query was run three times, the range is given. Compare **ratios**, not absolute times (§4).

### 01: one distance, three answers
| degrees | geodesic m | UTM 10N m |
|---|---|---|
| 0.2475 | 19,441.5 | 19,434.0 |

This matches the CRS deep dive exactly. UTM is 7.5 m (0.04 %) short here, which is the zone's scale factor near its central meridian.

### 02: point-in-polygon join (population per neighborhood), with vs without GiST
| | Plan | Time (3 runs) |
|---|---|---|
| **With index** | Nested Loop: Seq Scan on 93 neighborhoods → **Index Scan `census_blocks_rep_point_gist`** 93 times, `Index Cond: (rep_point && a.geom)`, about 134 hits per neighborhood, **74 removed by the exact recheck** | **25–31 ms** |
| Index scans disabled | Nested Loop: Parallel Seq Scan of 12,357 blocks × Seq Scan of 93 areas, `Join Filter: st_intersects(...)`, **1,136,772 exact tests** (568,386 rows removed per worker × 2) | 1,615–1,706 ms |

**About 55–65× faster with the index.** Two things to notice:
- The planner put the *small* table (93 areas) on the outside and probed the *point* index once per area. For each neighborhood, the R-tree returned the points inside its bounding box (about 208), and the exact test kept about 134.
- `Rows Removed by Filter: 74` is the lossiness from §5.4 made visible: those points were inside the neighborhood's box but outside its polygon.

The `test_planner_uses_a_gist_index_for_the_join` test asserts this plan shape on every run.

### 03: the big-polygon effect (city boundary: 16,422 vertices → 174 pieces of ≤ 256)
| Question | Raw boundary | Subdivided | Ratio |
|---|---|---|---|
| A/B: which block **points** are in the city? (12,357) | **19–22 ms** | 108–114 ms (59 ms of it is `ST_Subdivide` itself) | raw wins, 5× |
| C: which block **polygons** touch the city's edge? (331 blocks) | 1,237–1,261 ms | **32–33 ms** | subdivided wins, **39×** |

- **Points:** each block's point is tested against the *same* boundary row, so PostGIS caches a **prepared** version of the boundary (an index over its edges) and each test is cheap. The index filter still passes every point (the boundary's box covers the city), but that didn't matter.
- **Polygons vs the boundary line:** `ST_Boundary(s.boundary)` is recomputed inside the join, so there is no reusable prepared geometry. Each candidate block, and the box test passes nearly all of them (12,026 removed by the recheck), is compared with a 16,422-vertex line. After subdivision, each of the 224 short pieces probes the block index with a tight box and finds about 2 blocks.
- Both versions return the **same 331 blocks** (checked set against set: 0 differ). The subdivided plan shows `rows=480` before its `DISTINCT` because a block touching two adjacent pieces is found twice. That's why the query needs `count(DISTINCT geoid20)`.
- **A trap we hit:** the first version of query B used an *un*materialized CTE inside `EXISTS`. PostgreSQL 12+ inlines a CTE used once, so `ST_Subdivide` re-ran for every one of the 12,357 blocks. It was cancelled after more than 2 minutes. `WITH … AS MATERIALIZED` fixed it, and the file explains why.

### 04: KNN, the 5 nearest parks to Pioneer Courthouse Square
The plan is `Limit → Index Scan using parks_geom_gist, Order By: (geom <-> point)`. It touched **31 buffers and took 4.9 ms**: the index walks outward in distance order and stops after 5 rows. The results:

| park | class | metres (geography) |
|---|---|---|
| Pioneer Courthouse | natural_area | 60 |
| South Park Blocks | park | 247 |
| Lownsdale Square | park | 308 |
| Chapman Square | park | 366 |
| North Park Blocks | park | 420 |

**Data finding, now fixed (D-018 amendment 2):** the first run ranked "Pioneer Courthouse" first, at 60 m, as a **natural_area**. It's a federal courthouse *building*, which OSM tags `boundary=protected_area` because it's a National Historic Landmark (`heritage=2`, `protection_title=protected_site`). The Stage 3 rules included every protected area. Searching the loaded candidates found 4 such false positives, about 7 ha in total:

| Excluded now | Veto |
|---|---|
| US Government Moorings Historic District (66,349 m²) | `heritage_site_not_park` (`protect_class=22`) |
| Fido's Indoor Dog Park (1,868 m²) | `building_not_open_space` (`building=yes`) |
| Pioneer Courthouse (1,430 m²) | `building_not_open_space` (`building=office`) |
| Aubrey Watzek House (729 m²) | `building_not_open_space` (`building=yes`) |

After the fix, the five nearest are South Park Blocks 247 m, Lownsdale Square 308 m, Chapman Square 366 m, North Park Blocks 420 m and Terry Schrunk Plaza 432 m. The lesson here goes beyond SQL: **querying your data the way a user would is a validation method.** No aggregate check caught a 1,430 m² building among 76 km² of parks, but one nearest-park question did.

### 05: parks within 800 m of a point, on 4326 storage
| Method | Plan | Time | Count |
|---|---|---|---|
| A: `ST_DWithin(geom::geography, pt::geography, 800)` | **Seq Scan**: the cast on the column hides the geometry index, and geodesic distance to big polygons is costly | 114–116 ms | 10 |
| B: transform the **constant** side (800 m circle in UTM → 4326), `ST_Intersects` | **Bitmap Index Scan `parks_geom_gist`**: 12 candidates, 2 removed by recheck | 7.7 ms | 10 |
| C: `ST_DWithin(ST_Transform(geom, 26910), …)` (anti-pattern) | **Seq Scan** | 7.5 ms | 10 |

**Honest reading:** B uses the index and C doesn't, as predicted. But with only 477 parks, a sequential scan is so cheap that C ties B. The difference would show with many more rows, such as all blocks rather than all parks. A is slowest because geodesic distance to large polygons is expensive, not because of the scan. An expression index `USING gist ((geom::geography))` would let A use an index. That's left as the exercise from Q3.

## 15. Answers to §12

1. **Lossy:** the index stores only bounding boxes, so a box hit may not be a real hit. The planner pays in the **recheck**: the exact GEOS predicate on every candidate (`Rows Removed by Filter: 74` per neighborhood in query 02).
2. `ST_Intersects` is an *index-aware* SQL function. PostGIS attaches a support function that tells the planner "this implies `a && b`", so the planner can add the box condition and use GiST. `ST_Distance(a, b) < 800` is just a number compared to a constant, with no operator the index understands. `ST_DWithin` is the index-aware form, expanding the box by the distance.
3. With `(region_id, geoid20)`, the same block can exist once per region, and `DELETE … WHERE region_id = X` touches only region X's rows. With `geoid20` alone, a second region containing the same blocks would collide, and reloading one region would have to delete or overwrite rows another region relies on.
4. They see the **previous** complete load until the loader commits, then the new one. They never see a mix, and they're never blocked by the delete. That's **MVCC** (multi-version concurrency control): the delete marks old row versions as dead *as of* the loader's transaction, and other snapshots keep seeing them until commit. `test_failed_load_keeps_previous_data` checks the rollback half of this.
