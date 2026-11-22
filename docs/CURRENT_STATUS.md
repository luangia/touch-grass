# Current Status — TouchGrass

_Last updated: 2026-11-22_

## Where we are

| Item | Status |
|---|---|
| Phase | **Planning complete — awaiting your feedback** |
| Current stage | None started. Next: **Stage 1 — Foundations, environment & region config** (not approved yet) |
| Code | None |
| Data downloaded | None |
| Git | Not initialized (proposed for Stage 1, D-012) |

## Currently being worked on

- Reviewing the proposed plan and decisions with you.

## Open decisions needing your approval

See [DECISIONS.md](DECISIONS.md). Most important:
1. **D-001** Study area: Portland city limits (+2 km analysis buffer).
2. **D-003** Park source: OSM primary + validation vs Metro ORCA, *or* ORCA primary.
3. **D-002** 2020 blocks for population weighting, ACS 2020–2024 block groups for demographics.
4. **D-011** "Underserved" = > 10-minute (800 m) network walk to the nearest park access point.
5. **D-009** Stack as proposed.
6. Whether Stage 11 (deployment) is in scope for the 2-week window.

## Next steps

1. You review / adjust decisions.
2. On approval: `/learn:prepare-stage` for Stage 1, then `/learn:implement-stage 1`.

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
