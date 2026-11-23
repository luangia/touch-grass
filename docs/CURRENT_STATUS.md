# Current Status — TouchGrass

_Last updated: 2026-11-23_

## Where we are

| Item | Status |
|---|---|
| Phase | **Stages 1–5 complete. Stage 6 prepared**, awaiting implementation approval |
| Completed stages | 1 Foundations · 2 Boundaries & population · 3 Parks · 4 PostGIS · 5 Walk network & access points (2026-11-23) |
| Next stage | 6: Walking accessibility analysis. [Lesson](learning/stage-06-walking-accessibility.md). **Not approved for implementation**; decisions Q1–Q7 pending |
| Code | `pipeline/`: config, adapters, spatial core, parks, network, db migrate/status/load; 182 tests |
| Database | schema at migration 0006; portland-or loaded (85,931 rows in 11 tables) |
| Git | `main`, 3 commits ahead of `origin` (Stage 4, Stage 5 planning, Stage 5 `f03f451`). Stage 6 prep docs uncommitted |

## Currently being worked on

- Stage 6 preparation done. Waiting on decisions Q1–Q7 and implementation approval. The prototype found that 85.0% of residents live within a 10-minute walk of a park (straight line would say 97.2%), and that two small Stage 5 contact bugs need fixing first.
- ParkServe consent request (D-016): awaiting TPL's reply. Not blocking.

## Accepted decisions (summary)

See [DECISIONS.md](DECISIONS.md): Portland city limits + 2 km buffer; 2020 blocks + ACS 2020–2024 block groups; OSM parks validated against Metro ORCA; multi-source Dijkstra over the OSM walk network; "underserved" = > 10-minute (800 m) walk; proposed stack. Stage 1 added D-013 (amd64 PostGIS image under emulation) and D-014 (`exclude-newer` dependency cutoff). Stage 2 added D-015 (data-source choices) and amended D-007 (Census interior points). Stage 3 added D-017 (dated OSM extracts + pyosmium; uv-managed Python) and D-018 (park rules and merging; amendment 2 adds `veto_tags`). Stage 4 added D-019 (COPY loading, SQL migrations, 4326-only storage, long ACS table, test database, settings). Stage 5 added D-020 (offline OSMnx build, walk filter with shared-use paths re-admitted, largest component plus a report, edge snapping, UTM edge weights) and amended D-006 (no sampled fallback).

## How to run what exists

```bash
cd pipeline
uv run touchgrass fetch portland-or      # ~480 MB streamed once, then cached
uv run touchgrass process portland-or    # offline; writes data/processed/portland-or/
uv run touchgrass db migrate             # schema (needs `docker compose up -d db`)
uv run touchgrass network portland-or    # ~2 min; walk graph + origins + access points
uv run touchgrass load portland-or       # ~3 s; replaces the region atomically
uv run pytest                            # -m data / -m db to select; db tests use touchgrass_test
```

## Next steps

1. Answer Stage 6 decisions Q1–Q7 (lesson §9).
2. Approve implementation: `/learn:implement-stage 6`.

## Blockers / risks being watched

- City of Portland and Metro RLIS licences verified (2026-11-22).
- ParkServe terms verified and **restrictive** (personal, non-commercial use only; no modification or public display without consent). D-016 accepted: don't ingest; cite TPL's published figures. A consent request was emailed to TPL on 2026-11-23, **awaiting a response**. Don't use ParkServe data unless written consent arrives.
- The Census Data API now requires a key; Stage 2 plans to use key-free bulk files instead.
- Schedule is at the top of the 1–2 week range; deferral order is in PROJECT_PLAN §3.

---

## Resume section

> Draft — **only claims that are true today are listed as done.** Bullets below are *target* statements to be finalized as stages are completed; do not use them on a resume until the corresponding stage is marked complete.

**TouchGrass — Walkable Park Access Analysis Platform** · Python, FastAPI, PostgreSQL/PostGIS, GeoPandas, OSMnx, NetworkX, React, TypeScript, MapLibre GL JS

Target bullets (pending implementation):
- ☐ Built a reproducible geospatial ETL pipeline ingesting US Census (2020 blocks, ACS 5-year), TIGER/Line, and OpenStreetMap data with provenance tracking and automated validation. _(Stages 2–3)_
- ☐ Computed pedestrian-network walking distance from ~N census blocks to nearest park access points using multi-source Dijkstra over an OSM walk graph, contrasting it with straight-line distance (median detour ratio X). _(Stages 5–6)_
- ☑ Designed a multi-region PostGIS schema with GiST-indexed spatial joins, measuring a ~60× speedup via `EXPLAIN ANALYZE`. _(Stage 4)_
- ☐ Produced population-weighted 5/10/15-minute park-access metrics for ~95 Portland neighborhoods, identifying X residents beyond a 10-minute walk. _(Stage 7)_
- ☐ Delivered a typed FastAPI service (GeoJSON + PostGIS-generated vector tiles) and a React/MapLibre interactive map for neighborhood comparison. _(Stages 8–10)_
- ☐ Architected configuration-driven region onboarding so new cities require only a YAML config and data sources. _(Stages 1–2)_

Completed so far:
- Project planning and architecture design.
- Stage 5: offline walk-network build from a dated OSM snapshot (105k nodes, 8,370 km); found and fixed a walk-filter bias that dropped 119 km of shared-use trails and disconnected Hayden Island; edge-based snapping puts 99.35% of residents within 100 m; 11,878 park access points.
- Stage 4: multi-region PostGIS schema with checksummed SQL migrations, typed SRID columns and constraints; atomic binary-COPY loader (63,817 rows in 1.7 s); PostGIS and GeoPandas membership agree exactly (24,785 pairs); GiST point-in-polygon join about 60× faster than without the index, measured with `EXPLAIN ANALYZE`.
- Stage 3: OSM park extraction from a checksummed snapshot with config-driven classification rules; overlapping features merged with union-find; validated against Metro ORCA (area precision 0.90, recall 0.93), with the 20 largest disagreements explained.
- Stage 2: reproducible Census/TIGER/ArcGIS ETL; population reconciles exactly to the official 2020 total (652,503) and to all 4 council districts; overlapping-neighborhood membership model; 92 tests.
- Stage 1: config-driven region model with validated CRS rules (projected, metric, non-Mercator, area-of-use check), a Typer CLI, a 55-test suite, and a containerised PostGIS environment. *Foundation only, not yet resume-worthy on its own.*
