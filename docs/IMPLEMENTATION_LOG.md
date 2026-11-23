# Implementation Log — TouchGrass

Chronological record of stages, changes, tests, and outcomes. Only work that actually happened is recorded here.

---

## 2026-11-22 — Project initialization (planning only)

**Type:** Planning. No application code written, no dependencies installed, no data downloaded.

**Inspected:** project root contained only `CLAUDE.md` (references the learning-system `AGENTS.md`). Not a git repository. `CLAUDE.md` preserved unchanged.

**Research performed:** web checks of data availability — City of Portland neighborhood boundaries (open data portal / PortlandMaps), Metro RLIS ORCA, 2020 P.L. 94-171 + TIGER/Line blocks, ACS 2020–2024 5-year (released 2026-01-29; block group for select tables), TPL ParkServe, Annual NLCD 1.1 (1985–2024), USFS Tree Canopy Cover v2023.5, OSMnx 2.x API changes. Several license texts and exact layer URLs remain marked **[verify]** in ARCHITECTURE §9.

**Created:**
- `README.md`
- `docs/PROJECT_PLAN.md` — 11 stages with acceptance criteria
- `docs/ARCHITECTURE.md` — system, DB schema, pipeline, API, multi-region design, data sources
- `docs/DECISIONS.md` — D-001…D-012, all *Proposed*
- `docs/LEARNING_GUIDE.md`
- `docs/CURRENT_STATUS.md`
- `docs/IMPLEMENTATION_LOG.md` (this file)
- `docs/learning/README.md` (empty lessons index)

**Tests:** none (no code).

**Outcome:** awaiting approval of proposed decisions before Stage 1.

---

## 2026-11-22 — Planning decisions approved

**Type:** Planning. The project owner approved all proposed decisions (D-001…D-012) as written. DECISIONS.md and CURRENT_STATUS.md updated. Stage 11 stays optional.

**Not done:** Stage 1 not prepared or implemented (needs its own approval).

---

## 2026-11-22 — Stage 1 prepared (no code)

**Type:** Teaching and preparation. Wrote `docs/learning/stage-01-foundations.md`. Checked the PyProj facts it uses in a throwaway environment, outside the project: EPSG 26910/4326/3857/2269 properties; the axis swap without `always_xy` returns `inf`; the Web Mercator area factor at 45.5°N is 2.04.

**Found:** Docker is not installed on the dev machine (uv 0.10.2, Python 3.12.7, git, Node present).

**Doc fixes:** D-008 area-inflation figure corrected (≈2.0×, previously "~1.9×"); ARCHITECTURE §2 paths updated to the `src/` layout.

---

## 2026-11-22 — Stage 1 implemented: Foundations, environment & region config

**Approved by:** project owner ("I approve implementation of Stage 1"), choosing Docker Desktop, Typer, and the `center_lonlat` area-of-use check.

**Built**
- `.gitignore` (kept your `CLAUDE.md` entry; added env, data, Python, Node, OS rules), `.env.example`, `data/.gitkeep`.
- `docker-compose.yml`: a single `db` service, `postgis/postgis:17-3.6` (`linux/amd64`, D-013), named volume `tg_pgdata`, `pg_isready` healthcheck. A local `.env` was generated with a random password (git-ignored, not committed).
- `pipeline/`: uv package (`src/` layout), Python ≥ 3.12. Runtime deps: pydantic, pyyaml, pyproj, typer. Dev: pytest, ruff, mypy (strict + pydantic plugin), types-PyYAML. `uv.lock` resolved with `exclude-newer = 2026-11-08` (D-014).
- `touchgrass.core.crs`: `assert_analysis_crs` (known code → projected → meter axes → not normal-aspect Mercator), `assert_point_in_area_of_use`, `LonLatBounds` (antimeridian-aware), `make_transformer` (`always_xy=True`).
- `touchgrass.config`: frozen, `extra="forbid"` Pydantic models (`RegionConfig`, `AnalysisParams` with derived `threshold_distances_m`); loader with config-dir discovery (`--config-dir` → `TOUCHGRASS_CONFIG_DIR` → walk up for `config/regions/`), `region_id` = filename rule, readable error aggregation.
- `touchgrass` CLI (Typer): `regions list | validate | show`, plus `--version`.
- `config/regions/portland-or.yaml`.

**Tests and checks (all run; actual results)**
- `uv run pytest`: **55 passed**.
- `uv run ruff check .` and `ruff format --check .`: pass. `uv run mypy` (strict, 13 files): no issues.
- `touchgrass regions validate portland-or`: ✓, thresholds 400/800/1200 m.
- `docker compose up -d db`: healthy. PostgreSQL 17.11, PostGIS 3.6.4 (x86_64 under emulation).
- Cross-check: PostGIS `ST_Transform` and PyProj give identical UTM 10N coordinates for the same point (525263.6807, 5041113.7426).
- Manual grep of `pipeline/src/` for region values: none, after fixes.

**Issues found during implementation**
- The new hardcoding-guard test caught example strings ("Portland", `portland-or`) in my own docstrings. Replaced with generic examples.
- A swapped `(lat, lon)` center passed the coordinate range checks (−87.63 is a valid latitude). Only the area-of-use check caught it. A test now covers this.

**Not done / deferred (by design):** no data sources, downloads, DB schema, or GeoPandas (Stages 2–4). Nothing committed or pushed.

---

## 2026-11-22 — Deep dive: CRS (teaching only, no code changes)

Wrote `docs/learning/deep-dive-crs.md`. All numbers came from experiments run in the project's pinned environment (pyproj 3.8.0, PROJ 9.8.1): PROJ pipelines and operation candidates, UTM and Web Mercator scale factors at Portland, geodesic vs projected distance error, and transformer creation vs bulk-transform cost. Exercise 2 (EPSG:32611) was verified on a temporary copy of the config; the real config was untouched.

**Correction:** the Stage 1 lesson implied that WGS 84 → NAD83 applies a datum shift. PROJ's default is a zero-shift operation (EPSG "NAD83 to WGS 84 (1)", 4 m accuracy). The Stage 1 lesson is annotated.

---

## 2026-11-22 — Stage 1 marked complete; Stage 2 prepared (no code)

**Stage 1 verification (rerun):** 55 tests pass; ruff and mypy clean; `regions validate portland-or` ✓; PostGIS healthy; working tree clean and in sync with `origin`. Marked complete after the project owner's review (`/learn:advance`).

**Stage 2 research against live sources (read-only HTTP queries; nothing saved to the project):**
- City of Portland `COP_OpenData_Boundary` MapServer:
  - layer 10 City Boundaries (Metro-maintained; `CITYNAME='Portland'` → 4-part MultiPolygon, 16,420 vertices)
  - layer 3 Neighborhood Boundaries (125 partition pieces)
  - layer 125 Neighborhoods (regions) (97 overlapping, incl. 4 `MC UNCLAIMED` with `ID=0`)
  - layer 1413 Council Districts (4; `pop_total` sums to 652,503)
  - Native SRS 3857; supports GeoJSON, `outSR`, pagination (max 500).
- The city's `Shape_Area`/`AREA` ratio is 2.04 (Web Mercator inflation), a real-data CRS example.
- Census API returns 302 → `missing_key.html` without a key.
- TIGER 2020 block file includes POP20/HOUSING20/INTPT (287 MB statewide).
- ACS 2024 table-based summary files are key-free; 1,240 block groups in the 3 counties; sentinel values confirmed.
- The City layer metadata page timed out, so licence wording is still to be verified during Stage 2.

**Created:** `docs/learning/stage-02-boundaries-population.md` (decisions Q1–Q6 pending).

---

## 2026-11-22 — Stage 2 implemented: Boundaries & population data

**Approved by:** project owner ("Approve as recommended": D-015 Q1–Q6).

**Built**
- Dependencies (under the D-014 cutoff): geopandas 1.2.0, pyogrio 0.13.0 (GDAL 3.12.4), shapely 2.2.0 (GEOS 3.14.1), pyarrow 26.0.1, httpx 0.28.1, pandas 3.1.1. mypy treats the untyped geo libraries as `Any`.
- Config schema: `study_boundary` and `analysis_areas` (typed ArcGIS sources; unique `area_type`), `census` (decennial vintage, ACS year and tables), `validation` (expected population, tolerance). `portland-or.yaml` filled in (layers 10, 125 with `ID <> 0`, 1413).
- `touchgrass.paths`: raw/processed layout; `--data-dir` / `TOUCHGRASS_DATA_DIR`.
- `sources/http.py`: streaming download, atomic write, SHA-256 + manifest, cache with checksum check, optional line filter (records the full-source checksum *and* the filter).
- `sources/arcgis.py`: paged `/query` with stable `orderByFields`, `outSR=4326`, GeoJSON; total cross-checked with `returnCountOnly`; publisher copyright/description stored in the manifest.
- `sources/tiger.py` (derived URLs; GDAL `where` county filter while reading) and `sources/acs.py` (prefix-filtered streaming; sentinels → null; unexpected negatives raise).
- `core/geometry.py` (`to_multipolygon`/`clean_polygons`, `buffer_in_crs`, `area_m2`, `representative_points` with fallback) and `core/assign.py` (prepared point-in-polygon; many-to-many `sjoin` with `intersects`).
- `pipeline/fetch.py`, `process.py`, `validate.py`; CLI `fetch`, `process`, `validate`.

**Real-data results (Portland)**
- `fetch`: 9 sources, 313 MB stored (ACS streamed ~175 MB, stored 0.4 MB after filtering), 9.4 s. Second run: 0 downloaded, 9 cached.
- `process`: about 14 s; 16/16 hard checks passed.
  - **City population from blocks = 652,503, exactly the official 2020 total** (relative difference 0).
  - **All 4 council districts reconcile exactly** (159,498 / 160,716 / 167,642 / 164,647).
  - 12,357 blocks (10,372 populated); 0 interior-point fallbacks; 533 block groups, all with ACS data (25 null median incomes).
  - 93 neighborhoods: 161 blocks / 680 people in none (unclaimed areas); 233 blocks / 14,702 people in two. The neighborhood sum is 666,525, which ≠ 652,503, as predicted.
  - UTM block area / Census ALAND+AWATER = 0.99922, matching UTM 10N's areal scale factor at Portland (0.99922, from the CRS deep dive).
  - Privacy-noise signals: 178 blocks with population but 0 housing units (5,940 people).
  - Median income median CV 0.22; 12.6% of block groups have CV > 40%.
- Boundary diagnostics: 328 county blocks touch the boundary but are represented outside; 194 of them overlap by < 1% (digitising slivers); 26 blocks (401 people) overlap ≥ 10%.
- Idempotency: a second `process` produced **byte-identical** Parquet files.

**Tests:** 92 passed (unit; mocked HTTP incl. 2-page ArcGIS and count mismatch; synthetic end-to-end region with hand-computed answers, reconciliation failure, idempotency, tampered raw file; 2 real-data tests marked `data`). ruff, ruff format, mypy --strict (30 files): clean.

**Fixed during implementation**
- Called the non-existent `GeoSeries.point_on_surface()`; the GeoPandas name is `representative_point()`.
- The first boundary statistic ("328 straddlers, 20,914 people") overstated exclusion. Replaced with an overlap-fraction breakdown.

**Licences:** City layers verified (public use, City Data Distribution Policy, "as is"). The Metro RLIS terms page is JavaScript-rendered: **still to verify manually**.

**Not done (by design):** no PostGIS loading (Stage 4), parks (Stage 3), network (Stage 5). Nothing committed.

---

## 2026-11-22 — Metro RLIS licence verified

**Source:** RLIS Open Database End User License Agreement, dated 2023-02-01 (PDF supplied by the project owner: `https://www.oregonmetro.gov/sites/default/files/2025-10/rlis-open-database-end-user-license-agreement-20230201.pdf`, retrieved 2026-11-22, 3 pages, SHA-256 `dbdead62d5e05718bab336fbcbe54a822a8a75171c9f177b53e70ba37926f7fd`).

**Terms that matter for TouchGrass:**
- **Allowed:** a royalty-free, non-exclusive, revocable licence to copy, publish, distribute, extract, modify, adapt, and combine the data in our own product.
- **Required:** follow the licence, and show Metro's attribution statement with a link to the licence. Where listing many attributions isn't practical, a link to a page that lists them is acceptable.
- **Forbidden:** sublicensing; implying official status or Metro endorsement; adding restrictions on the data. We may license our own derivative works on our own terms.
- **Disclaimers:** "as is", no warranty, and an indemnity (hold-harmless) clause in Metro's favour. Oregon law; venue Multnomah County.
- **Compatibility:** stated compatible with CC BY 4.0 and ODC-By, so it's attribution-only, **not** ODbL share-alike (correcting the planning assumption in D-003 and ARCHITECTURE §9).

**Applied:** the boundary source `attribution` in `portland-or.yaml` now carries the required statement + licence URL; README attribution, ARCHITECTURE §9 (City Boundaries and ORCA rows), D-003, status and the Stage 2 lesson updated. The same terms will apply to ORCA in Stage 3.

**Note:** the existing raw manifest for `study_boundary` still records the old attribution text (it was written at download time); the next `fetch --refresh` will rewrite it. Processing doesn't read the attribution, so outputs are unaffected.

---

## 2026-11-22 — ParkServe terms of use verified

**Source:** "Legal Disclosure & Terms of Use", ParkServe, © 2023 The Trust for Public Land (PDF supplied by the project owner: `https://parkserve.tpl.org/downloads/ParkServe_Terms_of_Use_May_2023.pdf`, retrieved 2026-11-22, 1 page, SHA-256 `8a3328c7982527551d1685bf2f36a1589b29915e55a34cd8f19d04dac05bf6ae`).

**Terms:**
- All rights reserved. No copying, modifying, publishing or distributing without TPL's prior written consent.
- Exception: one copy may be downloaded for personal, non-commercial use, but not modified, used commercially or for public display, or transferred.
- Contains OSM data (ODbL) and Esri material. Provided "as is".

**Impact:** this conflicts with the planned use of ParkServe as a downloaded benchmark in a public project. ARCHITECTURE §9 updated (not ingested); **D-016 proposed** (cite TPL's published figures instead; optionally request consent). Nothing was ever downloaded from ParkServe, so there's nothing to remove.

---

## 2026-11-23 — D-016 accepted; ParkServe consent requested

The project owner accepted D-016 (cite TPL's published Portland figures; don't ingest ParkServe) and sent TPL a written-consent request for a limited, Portland-only comparison. Status: awaiting a response. No ParkServe data has been downloaded or used.

---

## 2026-11-23 — Stage 2 marked complete; Stage 3 prepared (no project code)

**Stage 2 verification (rerun):** 92 tests (incl. 2 real-data) pass; lint and types clean; `validate` passes all hard checks; `fetch` fully cached; git clean and in sync. Open, non-blocking: ParkServe consent reply (D-016); the cached `study_boundary` manifest still carries the old attribution text.

**Stage 3 research (in the scratchpad and throwaway environments; nothing added to the project):**
- **Metro ORCA** (RLIS ArcGIS): parcels layer (15,093; `UNITTYPE`/`STATUS`/`RECREATION`/…) and Sites layer (6,465; `TYPE`). Validation subset `UNITTYPE IN ('Park','Natural Area') AND STATUS <> 'Planned'` = 4,129 in the buffer envelope.
- **Overpass API:** HTTP 502 "currently at capacity", which recommends a local instance for automated use.
- **Geofabrik:** dated Oregon extracts (yearly 1 Jan since 2014, monthly recent, daily last ~10 days). `oregon-261101.osm.pbf` (255 MB) downloaded to the scratchpad in 6 min 43 s; MD5 `7087d34f0aa8453c5a74f533196fe7de` matched the published checksum.
- **OSMnx** 2.1.1 (`features_from_xml`, `graph_from_xml`) and **pyosmium** 4.3.2 checked in a clean uv-managed Python. A NumPy-1 ABI warning seen first came from the Anaconda base interpreter leaking into the *throwaway* env, not from either package.
- The project venv is based on `/opt/anaconda3` (packages isolated; interpreter not reproducible). Housekeeping proposed (Q7).
- **Tag census in the 2 km buffer:** one pyosmium pass, 52 s. Forest Park (4,821 acres) is `leisure=nature_reserve` + `boundary=protected_area`, **not** `leisure=park`; Washington Park is `protected_area` only. Reserve and protected-area polygons overlap 46.1 of 47.4 km². 1,304 entrance nodes.

**Created:** `docs/learning/stage-03-parks-green-space.md` (decisions Q1–Q7 pending).
