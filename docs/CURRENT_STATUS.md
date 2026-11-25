# Current Status — TouchGrass

_Last updated: 2026-11-24_

## Where we are

| Item | Status |
|---|---|
| Phase | **All 11 stages complete.** Live at https://luangia.github.io/touch-grass/ |
| Completed stages | 1 Foundations · 2 Boundaries & population · 3 Parks · 4 PostGIS · 5 Walk network · 6 Walking accessibility · 7 Neighborhood metrics · 8 API · 9 Interactive map (2026-11-23) · 10 Comparison, rankings, methodology (2026-11-24) · 11 Hardening, deployment, portfolio (2026-11-25) |
| Current stage | 11: Testing hardening, deployment and portfolio. [Lesson](learning/stage-11-hardening-deployment.md) §13–16; decisions D-026. Your steps: [DEPLOYMENT.md](DEPLOYMENT.md) |
| Code | `pipeline/`: … + `analyze`, `metrics`, `reference`, `db create-reader`; 253 tests. `api/`: FastAPI, 10 endpoints, Dockerfile; 42 tests. `web/`: map, Rankings, Compare, About, light/dark; 52 tests (2 skip without a live API). `e2e/`: 8 Playwright smoke tests. CI green on GitHub; deployed (Pages, Render, Neon) |
| Database | schema at migration 0009; portland-or loaded (latest run `7c1fc6efaf00effa`; the loaded metrics record still shows Stage 10's uncommitted code, while the metrics files on disk are from the clean commit `698066d`). A fresh-clone rehearsal reproduced every figure (lesson §14) |
| Git | `main` at `131d3f1` (Stage 11 planning). Stage 11 implementation uncommitted |

## Currently being worked on

- Deployed and verified: CI green, 8/8 smoke tests pass against the live site. Next: `/learn:portfolio` for the write-up.
- ParkServe consent request (D-016): awaiting TPL's reply. Not blocking.

## Accepted decisions (summary)

See [DECISIONS.md](DECISIONS.md): Portland city limits + 2 km buffer; 2020 blocks + ACS 2020–2024 block groups; OSM parks validated against Metro ORCA; multi-source Dijkstra over the OSM walk network; "underserved" = > 10-minute (800 m) walk; proposed stack. Stage 1 added D-013 (amd64 PostGIS image under emulation) and D-014 (`exclude-newer` dependency cutoff). Stage 2 added D-015 (data-source choices) and amended D-007 (Census interior points). Stage 3 added D-017 (dated OSM extracts + pyosmium; uv-managed Python) and D-018 (park rules and merging; amendment 2 adds `veto_tags`). Stage 4 added D-019 (COPY loading, SQL migrations, 4326-only storage, long ACS table, test database, settings). Stage 6 added D-021 (SciPy engine, no cutoff, same-edge rule, run ids from content) and fixed two Stage 5 contact bugs. Stage 10 added D-025 (Rankings and Compare pages, straight line vs network, resident comparison with MOE, licences as data). Stage 9 added D-024 (Vite + React + MapLibre; colors break at the underserved line; fixed share breaks; URL state; OpenFreeMap). Stage 8 added D-023 (separate read-only API project; SQL-built GeoJSON and MVT; z12–14 tiles; ETag by run). Stage 7 added D-022 (areas, rankings, priority, reachable park area, ACS allocation, reliability) and amended D-011 (priority formula). Stage 5 added D-020 (offline OSMnx build, walk filter with shared-use paths re-admitted, largest component plus a report, edge snapping, UTM edge weights) and amended D-006 (no sampled fallback).

## How to run what exists

```bash
cd pipeline
uv run touchgrass fetch portland-or      # ~480 MB streamed once, then cached
uv run touchgrass process portland-or    # offline; writes data/processed/portland-or/
uv run touchgrass db migrate             # schema (needs `docker compose up -d db`)
uv run touchgrass network portland-or    # ~2 min; walk graph + origins + access points
uv run touchgrass analyze portland-or    # ~1 s; distance from every block to the nearest park
uv run touchgrass metrics portland-or    # ~9 s; area metrics, ACS context, report
uv run touchgrass load portland-or       # ~3 s; replaces the region atomically
uv run pytest                            # -m data / -m db to select; db tests use touchgrass_test
```

## Next steps

1. Portfolio notes written (`docs/PORTFOLIO.md`, local only).
2. Optional: check the wake notice after the API has slept (15 min idle).

## Blockers / risks being watched

- City of Portland and Metro RLIS licences verified (2026-11-22).
- ParkServe terms verified and **restrictive** (personal, non-commercial use only; no modification or public display without consent). D-016 accepted: don't ingest; cite TPL's published figures. A consent request was emailed to TPL on 2026-11-23, **awaiting a response**. Don't use ParkServe data unless written consent arrives.
- The Census Data API now requires a key; Stage 2 plans to use key-free bulk files instead.
- Schedule is at the top of the 1–2 week range; deferral order is in PROJECT_PLAN §3.

---

## Resume section

> **Final, verified bullets and interview notes are in `docs/PORTFOLIO.md`, kept local (git-ignored).** The draft below is kept as the stage-by-stage record.

> Draft — **only claims that are true today are listed as done.** Bullets below are *target* statements to be finalized as stages are completed; do not use them on a resume until the corresponding stage is marked complete.

**TouchGrass — Walkable Park Access Analysis Platform** · Python, FastAPI, PostgreSQL/PostGIS, GeoPandas, OSMnx, NetworkX, React, TypeScript, MapLibre GL JS

Target bullets (pending implementation):
- ☑ Built a reproducible geospatial ETL pipeline ingesting US Census (2020 blocks, ACS 5-year), TIGER/Line, and OpenStreetMap data with provenance tracking and automated validation. _(Stages 2–3)_
- ☑ Computed pedestrian-network walking distance from 10,372 census blocks to the nearest of 15,601 park access points with a multi-source Dijkstra (24 ms), showing 85% of residents within a 10-minute walk vs 97% by straight line (median detour ratio 1.35); verified against OSRM. _(Stages 5–6)_
- ☑ Designed a multi-region PostGIS schema with GiST-indexed spatial joins, measuring a ~60× speedup via `EXPLAIN ANALYZE`. _(Stage 4)_
- ☑ Produced population-weighted 5/10/15-minute park-access metrics for 93 neighborhoods and 4 council districts, identifying 97,753 residents beyond a 10-minute walk, with ACS context carrying propagated margins of error and reliability labels. _(Stage 7)_
- ☑ Delivered a typed FastAPI service (GeoJSON + PostGIS-generated vector tiles) and a React/MapLibre interactive map for neighborhood comparison. _(Stages 8–10)_
- ☑ Shipped with GitHub Actions CI (PostGIS-backed tests, Playwright browser tests) and deployed at $0 (GitHub Pages, a containerized API on Render, Neon PostGIS); results reproduce from a fresh clone, checked against committed input checksums and headline figures. _(Stage 11)_
- ☐ Architected configuration-driven region onboarding: the pipeline is driven by a per-region YAML config, verified by a synthetic second region in the test suite and a test that no code names Portland. _(Only Portland is built; don't claim more.)_

Completed so far:
- Project planning and architecture design.
- Stage 5: offline walk-network build from a dated OSM snapshot (105k nodes, 8,370 km); found and fixed a walk-filter bias that dropped 119 km of shared-use trails and disconnected Hayden Island; edge-based snapping puts 99.35% of residents within 100 m; 11,878 park access points.
- Stage 4: multi-region PostGIS schema with checksummed SQL migrations, typed SRID columns and constraints; atomic binary-COPY loader (63,817 rows in 1.7 s); PostGIS and GeoPandas membership agree exactly (24,785 pairs); GiST point-in-polygon join about 60× faster than without the index, measured with `EXPLAIN ANALYZE`.
- Stage 3: OSM park extraction from a checksummed snapshot with config-driven classification rules; overlapping features merged with union-find; validated against Metro ORCA (area precision 0.90, recall 0.93), with the 20 largest disagreements explained.
- Stage 2: reproducible Census/TIGER/ArcGIS ETL; population reconciles exactly to the official 2020 total (652,503) and to all 4 council districts; overlapping-neighborhood membership model; 92 tests.
- Stage 1: config-driven region model with validated CRS rules (projected, metric, non-Mercator, area-of-use check), a Typer CLI, a 55-test suite, and a containerised PostGIS environment. *Foundation only, not yet resume-worthy on its own.*
