# Current Status — TouchGrass

_Last updated: 2026-11-22_

## Where we are

| Item | Status |
|---|---|
| Phase | **Stage 3 implemented**, awaiting your review |
| Completed stages | 1: Foundations · 2: Boundaries & population data |
| In review | 3: Parks & green space. All hard checks pass incl. 5 sentinel parks; OSM vs ORCA precision 0.895 / recall 0.931 (after cutting golf/zoo out of parks) |
| Next stage | 4: PostGIS database & spatial SQL. **Not started; needs your approval** |
| Code | `pipeline/`: + OSM adapter (pyosmium), park rules, overlap merging, excluded-area subtraction, dataset comparison; 117 tests |
| Data | raw 568 MB (incl. OSM snapshot 243 MB, ORCA 47 MB); processed: 8 GeoParquet files + reports; git-ignored |
| Git | Stage 3 changes **uncommitted** |

## Currently being worked on

- Waiting on your review of Stage 3. The golf/cemetery/zoo subtraction rule was approved and is implemented (D-018 amendment).
- ParkServe consent request (D-016): awaiting TPL's reply. Not blocking.

## Accepted decisions (summary)

See [DECISIONS.md](DECISIONS.md): Portland city limits + 2 km buffer; 2020 blocks + ACS 2020–2024 block groups; OSM parks validated against Metro ORCA; multi-source Dijkstra over the OSM walk network; "underserved" = > 10-minute (800 m) walk; proposed stack. Stage 1 added D-013 (amd64 PostGIS image under emulation) and D-014 (`exclude-newer` dependency cutoff). Stage 2 added D-015 (data-source choices) and amended D-007 (Census interior points). Stage 3 added D-017 (dated OSM extracts + pyosmium; uv-managed Python) and D-018 (park rules and merging).

## How to run what exists

```bash
cd pipeline
uv run touchgrass fetch portland-or      # ~480 MB streamed once, then cached
uv run touchgrass process portland-or    # offline; writes data/processed/portland-or/
uv run pytest                            # add -m data for real-data checks only
```

## Next steps

1. Review Stage 3 and decide on committing.
2. When ready: `/learn:advance` to prepare Stage 4.

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
- ☐ Designed a multi-region PostGIS schema with GiST-indexed spatial joins, measuring an X× speedup via `EXPLAIN ANALYZE`. _(Stage 4)_
- ☐ Produced population-weighted 5/10/15-minute park-access metrics for ~95 Portland neighborhoods, identifying X residents beyond a 10-minute walk. _(Stage 7)_
- ☐ Delivered a typed FastAPI service (GeoJSON + PostGIS-generated vector tiles) and a React/MapLibre interactive map for neighborhood comparison. _(Stages 8–10)_
- ☐ Architected configuration-driven region onboarding so new cities require only a YAML config and data sources. _(Stages 1–2)_

Completed so far:
- Project planning and architecture design.
- Stage 3: OSM park extraction from a checksummed snapshot with config-driven classification rules; overlapping features merged with union-find; validated against Metro ORCA (area precision 0.90, recall 0.93), with the 20 largest disagreements explained.
- Stage 2: reproducible Census/TIGER/ArcGIS ETL; population reconciles exactly to the official 2020 total (652,503) and to all 4 council districts; overlapping-neighborhood membership model; 92 tests.
- Stage 1: config-driven region model with validated CRS rules (projected, metric, non-Mercator, area-of-use check), a Typer CLI, a 55-test suite, and a containerised PostGIS environment. *Foundation only, not yet resume-worthy on its own.*
