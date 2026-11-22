# Current Status — TouchGrass

_Last updated: 2026-11-22_

## Where we are

| Item | Status |
|---|---|
| Phase | **Planning approved** — ready to prepare Stage 1 |
| Current stage | None started. Next: **Stage 1 — Foundations, environment & region config** (preparation not started; implementation not yet approved) |
| Code | None |
| Data downloaded | None |
| Git | Not initialized (proposed for Stage 1, D-012) |

## Currently being worked on

- Nothing in progress. Planning decisions D-001…D-012 accepted 2026-11-22.

## Accepted decisions (summary)

See [DECISIONS.md](DECISIONS.md): Portland city limits + 2 km buffer; 2020 blocks + ACS 2020–2024 block groups; OSM parks validated against Metro ORCA; multi-source Dijkstra over the OSM walk network; "underserved" = > 10-minute (800 m) walk; proposed stack; git initialized in Stage 1. Stage 11 (deployment) stays optional, done only if time allows.

## Next steps

1. `/learn:prepare-stage` — teach Stage 1 concepts and propose the exact implementation.
2. After your explicit approval: `/learn:implement-stage 1`.

## Blockers / risks being watched

- License texts for City of Portland layers, Metro ORCA, and ParkServe not yet confirmed (marked **[verify]**).
- Schedule is at the top of the 1–2 week range; deferral order is in PROJECT_PLAN §3.

---

## Resume section

> Draft — **only claims that are true today are listed as done.** Bullets below are *target* statements to be finalized as stages are completed; do not use them on a resume until the corresponding stage is marked complete.

**TouchGrass — Walkable Park Access Analysis Platform** · Python, FastAPI, PostgreSQL/PostGIS, GeoPandas, OSMnx, NetworkX, React, TypeScript, MapLibre GL JS

Target bullets (pending implementation):
- ☐ Built a reproducible geospatial ETL pipeline ingesting US Census (2020 blocks, ACS 5-year), TIGER/Line, and OpenStreetMap data with provenance tracking and automated validation. _(Stages 2–3)_
- ☐ Computed pedestrian-network walking distance from ~N census blocks to nearest park access points using multi-source Dijkstra over an OSM walk graph, contrasting it with straight-line distance (median detour ratio X). _(Stages 5–6)_
- ☐ Designed a multi-region PostGIS schema with GiST-indexed spatial joins, measuring an X× speedup via `EXPLAIN ANALYZE`. _(Stage 4)_
- ☐ Produced population-weighted 5/10/15-minute park-access metrics for ~95 Portland neighborhoods, identifying X residents beyond a 10-minute walk. _(Stage 7)_
- ☐ Delivered a typed FastAPI service (GeoJSON + PostGIS-generated vector tiles) and a React/MapLibre interactive map for neighborhood comparison. _(Stages 8–10)_
- ☐ Architected configuration-driven region onboarding so new cities require only a YAML config and data sources. _(Stages 1–2)_

Completed so far:
- Project planning and architecture design (no implementation yet).
