# Learning Guide — TouchGrass

A structured index of the concepts this project teaches, **where** each is learned (stage), and **how** you'll know you understand it. Detailed lessons are written to [`docs/learning/`](learning/) as each stage is completed.

Legend: ☐ not started · ◐ introduced in an implemented stage · ☑ learned (you mark this once you can explain and use it unaided)

## Learning path at a glance

```
GIS fundamentals ──▶ Geodata engineering ──▶ Spatial databases ──▶ Network analysis ──▶ Spatial statistics ──▶ Web GIS
   (Stages 1–3)          (Stages 2–3)            (Stage 4)            (Stages 5–6)          (Stage 7)        (Stages 8–10)
```

Each concept is taught with: **Intuition → Problem → Mechanics → Implementation → Architecture → Trade-offs → Real-world usage → Pitfalls** (AGENTS.md §6).

## 1. GIS fundamentals

| Concept | Stage | You should be able to… | Status |
|---|---|---|---|
| Vector vs raster data | 1 | Say when to use each; why parks are vector and canopy is raster | ◐ |
| Geospatial formats (GeoJSON, Shapefile, GeoPackage, GeoParquet, MVT) | 1, 8 | Pick a format and defend it; explain RFC 7946 lon/lat order | ◐ |
| Coordinate reference systems, EPSG codes, datums | 1 | Read a CRS definition; explain NAD83 vs WGS 84 | ◐ |
| Geographic vs projected coordinates | 1, 3 | Explain why `area` in degrees is meaningless; why Web Mercator distorts area | ◐ |
| Geometry validity (OGC Simple Features) | 2, 3 | Diagnose and repair a self-intersecting polygon | ◐ |
| Spatial joins (point-in-polygon, intersects, nearest) | 2, 4 | Write the same join in GeoPandas and PostGIS | ◐ |
| Spatial intersection / overlay | 3 | Measure overlap between two park datasets | ◐ |
| Buffers and edge effects | 2, 6 | Explain why the study area is buffered; why buffers ≠ walk sheds | ◐ |
| Spatial indexing (R-tree, KD-tree) | 4, 5 | Explain filter-and-refine; when an index won't help | ◐ |

## 2. Spatial analysis

| Concept | Stage | You should be able to… | Status |
|---|---|---|---|
| Proximity analysis | 6 | Compute nearest-feature distance correctly in a projected CRS | ☐ |
| Graph representation of street networks | 5 | Explain nodes/edges/weights and OSMnx topology simplification | ☐ |
| Dijkstra & multi-source shortest paths | 6 | Trace Dijkstra by hand on a small graph; explain the super-source trick | ☐ |
| Network vs Euclidean distance; detour index | 6 | Explain where and why they differ (rivers, freeways, hills) | ☐ |
| Access points vs centroids | 5 | Explain why Forest Park's centroid is a bad destination | ☐ |
| Spatial aggregation | 7 | Aggregate block results to neighborhoods with overlaps | ☐ |
| Population-weighted metrics | 7 | Show numerically how weighting changes a ranking | ☐ |
| MAUP & ecological fallacy | 7 | Explain why results depend on boundaries and what you can't infer about individuals | ☐ |
| Uncertainty (ACS margins of error) | 7 | Decide whether two estimates are meaningfully different | ◐ |

## 3. Spatial databases

| Concept | Stage | You should be able to… | Status |
|---|---|---|---|
| PostgreSQL basics, schemas, constraints, migrations | 4 | Design and migrate the TouchGrass schema | ☐ |
| PostGIS `geometry` vs `geography` | 4 | Choose correctly and explain the cost of each | ☐ |
| SRIDs and `ST_Transform` | 4 | Avoid mixed-SRID errors | ☐ |
| Spatial SQL (`ST_Intersects`, `ST_DWithin`, `ST_Contains`, `<->`) | 4 | Write and explain index-aware predicates | ☐ |
| GiST indexes | 4 | Explain bounding-box filtering vs exact refinement | ☐ |
| Query optimization with `EXPLAIN ANALYZE` | 4 | Read a plan; show an index scan vs seq scan | ☐ |
| Vector tiles in SQL (`ST_AsMVT`, `ST_TileEnvelope`) | 8 | Explain tile coordinates and geometry clipping | ☐ |

## 4. Geospatial data engineering

| Concept | Stage | You should be able to… | Status |
|---|---|---|---|
| Data acquisition (APIs, FeatureServers, bulk files) | 2, 3 | Write an adapter with provenance and checksums | ◐ |
| ETL pipeline design (raw → processed → loaded) | 2–4 | Explain idempotency and immutable raw data | ◐ |
| Data cleaning & classification rules | 3 | Justify each park inclusion/exclusion rule | ◐ |
| Coordinate transformations in practice (PyProj) | 1–3 | Transform safely; know `always_xy` | ◐ |
| Reproducibility (manifests, pinned deps, run records) | 2, 6 | Re-run and get identical results | ◐ |
| Data validation (invariants, reconciliation) | all | Write checks that catch silent spatial bugs | ◐ |
| Configuration-driven multi-region design | 1, 2 | Explain how a new city is onboarded | ◐ |

## 5. Web GIS

| Concept | Stage | You should be able to… | Status |
|---|---|---|---|
| Web map architecture (tiles, styles, sources, layers) | 9 | Explain how MapLibre draws a frame | ☐ |
| Display projection (Web Mercator) | 9 | Explain why it's used for display but not measurement | ☐ |
| GeoJSON vs vector tiles | 8, 9 | Choose based on size and interactivity | ☐ |
| Data-driven styling & colour scales | 9 | Build a colorblind-safe threshold choropleth | ☐ |
| Frontend ↔ backend integration | 9 | Trace a click to a SQL query and back | ☐ |

## 6. Software engineering

| Concept | Stage | You should be able to… | Status |
|---|---|---|---|
| API design (REST, versioning, error codes) | 8 | Defend the endpoint design | ☐ |
| Database modeling | 4 | Explain normalization choices and the `region_id` model | ☐ |
| Application architecture (batch vs serving) | all | Explain why analysis is precomputed | ☐ |
| Testing spatial code (fixtures, invariants, property tests) | all | Write a test that would catch a CRS bug | ☐ |
| Deployment & CI | 11 | Deploy and explain cost/complexity trade-offs | ☐ |

## Recommended references (primary sources)

- PostGIS documentation — https://postgis.net/documentation/
- OSMnx documentation — https://osmnx.readthedocs.io/ (v2 migration notes in the GitHub changelog)
- GeoPandas user guide — https://geopandas.org/
- Shapely 2 manual — https://shapely.readthedocs.io/
- PyProj — https://pyproj4.github.io/pyproj/
- MapLibre GL JS docs — https://maplibre.org/maplibre-gl-js/docs/
- GeoJSON spec, RFC 7946 — https://datatracker.ietf.org/doc/html/rfc7946
- Census ACS 5-year API — https://www.census.gov/data/developers/data-sets/acs-5year.html
- TIGER/Line technical documentation — https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.html
- OpenStreetMap wiki (tags: `leisure=park`, `entrance=*`, RLIS import history) — https://wiki.openstreetmap.org/
- Boeing, G. — OSMnx papers and blog (geoffboeing.com)

## Lessons index

- [Stage 1 — Foundations, environment & region config](learning/stage-01-foundations.md) (implemented)
- [Stage 2 — Boundaries & population data](learning/stage-02-boundaries-population.md) (implemented)
- [Stage 3 — Parks & green space](learning/stage-03-parks-green-space.md) (implemented)
- [Stage 4 — PostGIS database & spatial SQL](learning/stage-04-postgis-spatial-sql.md) (complete; measured plans in §14)
- [Stage 5 — Pedestrian network & park access points](learning/stage-05-walk-network-access-points.md) (complete; results §14, field check §16)
- [Stage 6 — Walking accessibility analysis](learning/stage-06-walking-accessibility.md) (complete; results §14, route check §16)
- [Stage 7 — Neighborhood aggregation & underserved metrics](learning/stage-07-neighborhood-metrics.md) (complete)
- [Stage 8 — FastAPI backend](learning/stage-08-fastapi-backend.md) (complete)
- [Stage 9 — Interactive map](learning/stage-09-interactive-map.md) (prepared)
- [Deep dive: Coordinate Reference Systems](learning/deep-dive-crs.md)
