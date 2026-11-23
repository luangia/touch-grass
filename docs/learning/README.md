# Lessons

Detailed, stage-by-stage technical lessons.

| Stage | Lesson | Status |
|---|---|---|
| 1 | [Foundations, environment & region config](stage-01-foundations.md) — vector vs raster, formats, CRS, PyProj, config validation, reproducible environments | Implemented (2026-11-22) |
| 2 | [Boundaries & population data](stage-02-boundaries-population.md) — census geography and GEOIDs, decennial vs ACS, MOE, differential privacy, TIGER, ArcGIS REST, GeoPandas internals, spatial joins, reproducible ETL | Implemented (2026-11-22) |
| 3 | [Parks & green space](stage-03-parks-green-space.md) — OSM data model and PBF streaming, reproducible snapshots, tag classification, overlap merging as a graph problem, area precision/recall against ORCA | Prepared, not implemented |

## Deep dives

| Topic | Lesson | Related stages |
|---|---|---|
| Coordinate Reference Systems | [deep-dive-crs.md](deep-dive-crs.md): ellipsoids, datums, Transverse Mercator internals, PROJ operation selection, axis order, performance, debugging | 1 (and every later stage) |
