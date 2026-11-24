# Lessons

Detailed, stage-by-stage technical lessons.

| Stage | Lesson | Status |
|---|---|---|
| 1 | [Foundations, environment & region config](stage-01-foundations.md) — vector vs raster, formats, CRS, PyProj, config validation, reproducible environments | Implemented (2026-11-22) |
| 2 | [Boundaries & population data](stage-02-boundaries-population.md) — census geography and GEOIDs, decennial vs ACS, MOE, differential privacy, TIGER, ArcGIS REST, GeoPandas internals, spatial joins, reproducible ETL | Implemented (2026-11-22) |
| 3 | [Parks & green space](stage-03-parks-green-space.md) — OSM data model and PBF streaming, reproducible snapshots, tag classification, overlap merging as a graph problem, area precision/recall against ORCA | Implemented (2026-11-23) |
| 4 | [PostGIS database & spatial SQL](stage-04-postgis-spatial-sql.md) — geometry vs geography, SRID typmods, GiST/R-tree internals, filter-and-refine, reading query plans, multi-region schema, migrations, COPY loading; measured plans (§14) | Complete |
| 5 | [Pedestrian network & park access points](stage-05-walk-network-access-points.md) — graphs and simplification, the OSMnx walk filter and its biases, connected components, KD-trees/R-trees, edge snapping with offsets, virtual-source Dijkstra, access points; results (§14), field check (§16) | Complete |
| 6 | [Walking accessibility analysis](stage-06-walking-accessibility.md) — Dijkstra and priority queues, the virtual source, block distances incl. the same-edge case, cutoffs, detour index, invariants, reproducible runs; results (§14), route check (§16) | Complete |
| 7 | [Neighborhood aggregation & underserved metrics](stage-07-neighborhood-metrics.md) — population weighting, share vs count, overlaps, MAUP, ACS allocation and MOE propagation, reliability, context not cause, priority | Complete |
| 8 | [FastAPI backend](stage-08-fastapi-backend.md) — REST resources, dependency injection, connection pools, GeoJSON (RFC 7946) and simplification, vector tiles (z/x/y, `ST_AsMVT`), SQL injection and identifiers, caching, CORS | Complete |
| 9 | [Interactive map](stage-09-interactive-map.md) — WebGL maps, sources vs layers, data-driven styling, feature state, classification and colorblind-safe color, React + an imperative map, typed API client, URL state, accessibility | Complete |
| 10 | [Comparison, underserved view, methodology](stage-10-comparison-and-polish.md) — question-first views, honest rankings, straight line vs network, demographic context without causal claims, resident-based disparity with MOE, small SVG charts, licences as data | Prepared, not implemented |

## Deep dives

| Topic | Lesson | Related stages |
|---|---|---|
| Coordinate Reference Systems | [deep-dive-crs.md](deep-dive-crs.md): ellipsoids, datums, Transverse Mercator internals, PROJ operation selection, axis order, performance, debugging | 1 (and every later stage) |
