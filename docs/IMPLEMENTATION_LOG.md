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

---

## 2026-11-23 — Stage 3 implemented: Parks & green space

**Approved by:** project owner ("Approve as recommended": Q1–Q7 → D-017, D-018).

**Built**
- Venv recreated on uv-managed CPython 3.12.12 (`python-preference = "only-managed"`); 92 existing tests passed on it before any Stage 3 code.
- Dependency: `osmium` 4.3.2 (pyosmium).
- Config: optional `osm` (Geofabrik path + snapshot) and `parks` (include/exclude tag rules with reasons, access exclusions, natural-area tags, min area, validation source, sentinel parks); `parks` requires `osm`. Portland config filled in.
- `sources/osm.py`: Geofabrik URL, download + **MD5 verification** (mismatch deletes and fails), pyosmium area extraction (`with_locations` + `with_areas(KeyFilter)`), study-area filter by representative point, broken-relation detection.
- `core/parks.py`: rule engine (first-match, documented order; `access` beats include tags; size check after); overlap merging via STRtree pairs + union-find (interiors overlapping > 1 m², touching stays separate); sentinel check.
- `core/compare.py`: area precision/recall, feature coverage, discrepancy pieces.
- Pipeline: two new sources (OSM extract, ORCA), a parks step in `process` (`green_candidates`, `parks`, `park_discrepancies`), park checks in `validate`.

**Real data**
- `fetch`: OSM snapshot 243 MB in about 10 min (MD5 `7087d34f0aa8453c5a74f533196fe7de` verified); ORCA 7,520 features (47 MB).
- `process`: 48 s; all hard checks pass incl. **all 5 sentinel parks**. 531 included candidates → 478 units (75.1 km²). OSM vs ORCA area **precision 0.877, recall 0.931**. Byte-identical on rerun.
- Top 10 discrepancies in each direction investigated with live ORCA lookups (lesson §14). Finding A: park polygons enclosing excluded fee facilities (Heron Lakes golf courses ~308 acres in Delta Park West; Oregon Zoo in Washington Park). **A subtraction rule is proposed, not implemented** (would change D-018).

**Tests:** 114 passed (rules, merge incl. transitivity, comparison, real-PBF fixtures with a multipolygon hole and a broken relation, synthetic end-to-end with parks, missing-sentinel failure, data tests). ruff, mypy --strict (36 files) clean.

**Fixed during implementation:** mypy caught the untyped pyosmium object union (now `isinstance` narrowing). Two test-fixture mistakes (see lesson §13). The parks code was correct in both cases.

---

## 2026-11-23 — D-018 amendment: cut golf courses, cemeteries and the zoo out of parks

**Approved by:** project owner ("Yes, plus the zoo").

**Changed:**
- `ParksConfig.subtract_reasons` (validated against `exclude_tags` reasons).
- `core.parks.subtract_excluded` (difference after merging; re-measure; drop units < min area).
- Process stats `parks.subtraction`.
- Portland config: `tourism: [zoo]` under `not_open_space`, `subtract_reasons: [not_open_space]`.

**Real data:**
- 1.51 km² removed from 2 units: Delta Park West −307.8 acres (Heron Lakes golf), Washington Park −65.1 acres (Oregon Zoo).
- 1 unit dropped (0.16-acre recreation ground inside Waverly Country Club).
- Precision 0.877 → 0.895; recall 0.931 unchanged.
- All hard checks pass; outputs byte-identical on rerun.

**Tests:** 117 passed (3 new). ruff and mypy clean.

---

## 2026-11-23 — Stage 3 marked complete; Stage 4 prepared (no project code)

**Stage 3 verification (rerun):** 117 tests (incl. 2 real-data) pass; `validate` passes all hard checks; `fetch` fully cached (11/11); working tree clean. Committed as `5005b57`; `main` 2 ahead of `origin`.

**Environment finding:** the `touchgrass-db` container no longer existed (the data volume `touchgrass_tg_pgdata` did). It was recreated with `docker compose up -d db`: healthy, PostgreSQL 17.11 / PostGIS 3.6.4, no project tables.

**Stage 4 research (read-only):**
- Catalog: 8,500 `spatial_ref_sys` rows; extensions include `postgis_topology` and `postgis_tiger_geocoder`; `ST_AsMVT`, `ST_TileEnvelope`, `ST_Subdivide` etc. present; untuned defaults (`shared_buffers` 128 MB, `work_mem` 4 MB).
- Python: pyogrio's GDAL has **no PostgreSQL driver**; psycopg 3.3.8 (libpq 18, binary COPY), SQLAlchemy 2.1.8 and GeoAlchemy2 0.21.0 resolve under the date cutoff (checked in a throwaway env).

**Created:** `docs/learning/stage-04-postgis-spatial-sql.md` (decisions Q1–Q7 pending).

## 2026-11-23 — Stage 4 implemented: PostGIS schema, migrations, loader, exploration

**Approval:** "Approve as recommended" (Q1–Q7, recorded as D-019).

**Added**
- `psycopg[binary]>=3.2` (resolved 3.3.8 under the D-014 cutoff).
- `touchgrass/db/`: `connection.py` (settings), `migrate.py` (checksummed runner), `load.py` (staged binary COPY, one transaction, re-validation, provenance, digests), `migrations/0001–0004`.
- CLI: `db migrate`, `db status`, `load <region>`.
- `sql/explore/01–05` + README.
- Tests: `test_db.py` (18, marker `db`; 2 also `data`), `test_db_settings.py` (4). Marker `db` registered.

**Deviations from the lesson plan:** migrations live in the package instead of `db/migrations/` at the repo root; `area_membership` drops the redundant `area_type`.

**Results (real data)**
- Load: 63,817 rows / 9 tables, transaction 1.7 s; all geometry SRID 4326 and valid; population 652,503; reload digests identical.
- Parity: SQL membership = GeoPandas membership, 24,785 = 24,785 pairs, 0 differences.
- Plans: point-in-polygon join 25–31 ms with GiST vs 1,615–1,706 ms without (about 60×); polygon-vs-boundary 39× faster subdivided; points-in-boundary *slower* subdivided (prepared-geometry cache), so the lesson's §5.5 prediction was corrected; KNN 5 nearest parks in 4.9 ms / 31 buffers.

**Problems hit**
- An unmaterialized CTE inside `EXISTS` was inlined by PostgreSQL and re-ran `ST_Subdivide` per block. It was cancelled after more than 2 minutes and fixed with `AS MATERIALIZED` (documented in query 03).
- I first wrote two wrong explanations in the lesson: a spurious "331 vs 480 blocks" difference (480 was pre-`DISTINCT` rows) and a guess about Pioneer Courthouse's tags. Both were checked against the data and corrected before finishing.
- Test bug: read a non-spatial parquet with GeoPandas; switched to pandas.

**Data finding:** 4 likely park false positives through `boundary=protected_area` heritage sites or `building=*` (lesson §14). Proposed a D-018 amendment; not applied.

**Verification:** ruff and mypy --strict clean; 139 tests pass with the database up; with it unreachable, 122 pass and 17 skip cleanly; the hardcoding guard passes. Nothing committed.

## 2026-11-23 — D-018 amendment 2: veto tags (approved "Yes, amend D-018")

- `ParksConfig.veto_tags` (`TagVeto`: `tags` + `unless`; `"*"` wildcard) is applied after the access rule. A validator rejects veto reasons that clash with exclude reasons or the built-in ones.
- Portland config: `building_not_open_space` and `heritage_site_not_park`. I checked the candidate set in PostGIS first, and exactly 4 features match.
- Tests: +5 (4 rule cases, 1 clash validator). Total 144, all passing.
- Reprocessed and validated (all hard checks pass), then reloaded: parks 477 → 473 with no other unit changed; precision 0.8952 → 0.8961, recall 0.9309 unchanged; load 63,813 rows.
- Nothing committed.

## 2026-11-23 — Stage 4 committed and complete; Stage 5 prepared (no project code)

**Stage 4:** committed as `5a55c70`, which includes D-018 amendment 2. Verified afterwards: 144 tests, `validate` passes all hard checks, schema current, tree clean. `main` is 1 ahead of `origin`.

**Stage 5 research** (throwaway scratch venv with osmnx 2.1.1 / networkx 3.7 / scipy 1.18.1 under the date cutoff; probe scripts outside the repo; read-only on data):
- OSMnx has no PBF reader. `graph_from_xml` on pyosmium-filtered XML works: 28 s scan + 40 s build → 107,113 nodes / 8,375 km after clipping.
- The OSMnx walk filter drops 119 km of `cycleway` + `foot=designated|yes` (I-205 MUP, Springwater, Marine Drive Trail) and leaves Hayden Island disconnected. Re-admitting foot-legal cycleways raises the main component from 97.4% to 98.4% of nodes, reconnects Hayden (26/27 blocks), and lowers the stranded population from 6,891 to 4,244.
- Snapping: nodes 93.1% of blocks ≤ 100 m (median 51 m); edges 99.45% of residents ≤ 100 m (median 33 m).
- Access: 463/473 parks have network within 15 m; the 10 without are mostly river islands. 208 entrance/gate nodes near parks, 19 private/no.

**Created:** `docs/learning/stage-05-walk-network-access-points.md` (Q1–Q7 pending).

## 2026-11-23 — Stage 5 implemented: walk network, block origins, park access points

**Approval:** "Approve as recommended" (Q1–Q7, recorded as D-020 plus the D-006 amendment).

**Added**
- Dependencies `osmnx>=2.0`, `networkx>=3.2`, `scipy>=1.11` (2.1.1 / 3.7 / 1.18.1 resolved); mypy overrides for them.
- Config: `WalkFilter`, `NetworkConfig`, `RegionConfig.network` (requires `parks`); Portland `network:` section with the shared-use `readmit`.
- `sources/osm.py`: `walk_exclusion`, `extract_walk_ways`. `core/network.py`, `core/snap.py`, `core/access.py`. `pipeline/network.py`. CLI `touchgrass network`.
- Migration `0005_network.sql`; loader loads `block_origins` and `park_access_points`, with a freshness guard.
- Tests: `test_network.py` (26) + `network_fixture.py`; `osm_fixture.py` gains shared-node lines and tagged nodes; +2 db, +1 data. 173 total.

**Problems hit**
- The temp file was named `walk.osm.tmp`: osmium chooses the format by extension and refused it. Renamed to `walk.tmp.osm`.
- Unnamed parks wrote `NaN` into the JSON report (invalid JSON). Fixed with `_records()`; the writer now uses `allow_nan=False`.
- A test on edge lengths failed by 0.24%. Cause: OSMnx's spherical haversine. Weights switched to UTM lengths (D-020).
- I miscounted the fixture's walkable ways in a test (13 vs the correct 10).
- The hardcoding guard caught "Portland" in a docstring in `core/snap.py`; reworded.
- DB tests assumed exactly 4 migrations; now derived from `discover()`.

**Results:** see the lesson, §14. All 6 network hard checks pass; 99.35% of residents within 100 m; 463/473 parks reachable; reruns byte-identical; load 86,063 rows.

**Not verified:** a visual map check of the access points against OSM (asked of the user). Nothing committed.

## 2026-11-23 — Stage 5 revised after the field check

**Your findings:** Hayden, Laurelhurst and Mount Tabor pass. Rivervilla, Cottonwood Creek, Stephens Creek and Ravine Woods are walkable. Three unnamed "parks" aren't parks. Hardtack Island is an island. Ross Island is closed to the public.

**Diagnosis** (probes in the scratch area): a clipping pocket (Rivervilla); no mapped network within 15 m (3 parks); a weak park tag (the 3 non-parks); a straight connector over the Willamette (Hardtack). OSM water separated islands from set-back parks; a distance threshold could not (Moore and Wright Islands is 26 m away, Cottonwood 28 m).

**Changes (approved A as is, B general, C drop + show named):**
- `extract_areas(keep="intersects")`; `NetworkConfig.water_tags`, `max_park_gap_m`.
- `core/snap.py`: `snap_to_edges_dry`, `water_crossing_m`. `core/access.py`: `gap` method. `core/network.py`: `touches_clip`.
- `pipeline/network.py`: snap statuses, all components kept, no-access reasons. Migration `0006`; loader updated.
- Portland config: dropped the two landuse include tags, added the exclude reason `sports_or_private_ground`.
- Tests: +5 (dry snap, park gap vs island, origin statuses, intersects mode, clip marking). 178 total.

**Bugs:** `df[mask] = df` sets columns, not rows (fixed with explicit `iloc`); an ambiguous test geometry (fixed with a triangle); "Portland" in a docstring again (the guard caught it).

**Results:** lesson §16. Nothing committed.

## 2026-11-23 — Ross Island excluded as closed to the public (approved "Exclude via config")

- `ClosedPark` + `ParksConfig.closed_to_public`; the reason `closed_to_public` is applied in `classify_candidates` after the size rule. The hard check `parks.closed_list_matched` requires that the id exists and the name matches.
- Portland lists Ross Island Natural Area (`w1365628608`).
- Tests +4 (exclusion, missing id, wrong name, duplicate). 182 total, all passing.
- Reprocessed, reran the network and reloaded: 448 parks, 73.21 km²; precision 0.8972, recall 0.9284 (ORCA includes Ross Island); 2 parks without network contact. Nothing committed.

## 2026-11-23 — Stage 5 committed and complete; Stage 6 prepared (no project code)

**Stage 5:** committed as `f03f451`. Verified afterwards: 182 tests; both validation reports pass all hard checks; network outputs fresh; schema at 0006; tree clean.

**Stage 6 prototype** (scratch script, read-only on data):
- Speed: SciPy csgraph search 0.02 s vs NetworkX 0.30 s; identical distances on 104,785 nodes.
- Same-edge rule matters for 614 blocks (up to 624 m).
- The invariant `network ≥ straight − 15 m` caught **two Stage 5 bugs**: one contact span covering two separate touches (4 blocks, up to 44.6 m too short), and interior park trails not counted as contact (a Forest Park block at 2,628 m).
- First results: within 800 m, network 85.0% vs straight line 97.2%; median detour 1.35.

**Created:** `docs/learning/stage-06-walking-accessibility.md` (Q1–Q7 pending).

## 2026-11-23 — Stage 6 implemented: walking accessibility

**Approval:** "Approve as recommended" (Q1–Q7, recorded as D-021 plus D-020 amendment 2).

**Added:**
- `core/routing.py`, `pipeline/analyze.py`, CLI `touchgrass analyze`.
- Migration `0007_analysis.sql`; the loader loads runs.
- Stage 5 contact fixes in `core/access.py`.
- Tests: `test_routing.py`, `test_analyze.py`, +2 network, +1 db. 198 total.

**Verified SciPy behaviour before relying on it:** an explicit zero stays an edge unless `eliminate_zeros()` runs; duplicate entries are summed (3 + 4 → 7).

**Results:** lesson §14. 85.0% within 10 min by network vs 97.2% by straight line; all invariants hold; 1 unreachable block (Hardtack); search 24 ms.

**Housekeeping:** I deleted two development runs (`5fd4a8c77acd5bd3`, `b603963fe2dabbbb`) that I had created minutes earlier from intermediate code, so they wouldn't load as history.

**Not verified:** the route comparison against a web router, asked of you (lesson §14). Nothing committed.

## 2026-11-23 — Route check; access/foot fix (D-020 amendment 3)

- **Your route check:** 289 / 913 / 921 m and 7.0 km. I queried OSRM foot for geometry, sending only the public coordinates (5 requests, one of them with placeholder coordinates sent before I'd read the real destination; its result was ignored). Three samples match our street portion to within 1 m. The fourth started on an `access=no` service road.
- **Fix:** `WalkFilter` defaults add `access=no` and `foot=private`; `foot_overrides` lets `foot=yes|designated|permissive` beat an `access` closure. Tests +6 cases. 204 total, all passing.
- **Reran** network → analyze (run `8205a92013b15324`) → load; effects in D-020 amendment 3. Nothing committed.

## 2026-11-23 — Stage 6 committed and complete; Stage 7 prepared (no project code)

**Stage 6:** committed as `d3fd91d`; `analyze` rerun on the clean commit gives run `7c1fc6efaf00effa` (rows identical to the previous run apart from run_id); reloaded. Verified: 204 tests; all hard checks pass; network and run fresh; tree clean.

**Stage 7 prototype** (scratch, read-only):
- Citywide 85.0% within 10 min, 97,753 underserved.
- Lowest shares are small areas (Sunderland 0%, 698 people); the most underserved people are in East Portland (Powellhurst-Gilbert 11,867).
- Weighting effects up to 45 points (blocks) and 67 points (land).
- Inside-boundary park area is misleading (Alameda 0 m² per resident).
- A rank-sum priority is dominated by density.
- ACS allocation must divide by whole-block-group population (168 of 533 block groups below 90% in-city).
- My prototype's proportion-MOE formula was simplified, so the reliability count (~28 of 93 with CV > 40%) is rough.

**Created:** `docs/learning/stage-07-neighborhood-metrics.md` (Q1–Q8 pending).

## 2026-11-23 — Stage 7 implemented (approved "Approve as recommended", Q1–Q8)

**Decisions:** D-022 (Q1–Q8) and the D-011 amendment (priority = underserved residents per km² of populated-block land).

**Built:** lesson §13.
- Modules: `core/aggregate.py`, `core/acs_alloc.py`, `routing.parks_within`, `tiger.read_block_population`, `pipeline/metrics.py`.
- CLI `touchgrass metrics`; config `metrics:` section; migration 0008 + loader.
- Tests: +38, 242 in total. Ruff and mypy clean.

**Verified before building on it:**
- The per-park bounded search reproduces Stage 6's nearest-park distance exactly (max difference 0.0 m, 9,086 blocks).
- Reading TIGER block attributes without geometry takes < 1 s. GDAL's WHERE field must be among the read columns, or it silently returns 0 rows; this caught me once.

**Results (lesson §14):**
- All 8 hard checks pass; reruns are byte-identical.
- 97,753 underserved; North Tabor tops the priority list.
- Three prototype figures corrected:
  - land-weighting max 35.7 points, not 67.1 (water was included);
  - 59 straddling block groups, not 168;
  - zero-vehicle reliability low in 44 of 97 areas.

**Bugs hit:** undefined (suppressed) ACS estimates crashed the report; an index/column name clash; a draft caveat that misstated the park rules.

**Not verified:** the North Tabor and Alameda results against the map (asked of you, lesson §14). The metrics record shows uncommitted code (`dirty: true`) until Stage 7 is committed and `metrics` rerun. Nothing committed.

## 2026-11-23 — Stage 7 committed and complete; Stage 8 prepared (no project code)

**Stage 7:**
- Your field check confirmed Alameda. North Tabor's low share comes from distance: the network has the SE Yamhill access to Mt. Tabor Park (lesson §17).
- Committed as `0b7fd58`; `metrics` rerun on the clean commit (`dirty: false`) and reloaded.
- Verified: 242 tests; ruff and mypy clean; network, run and metrics fresh; tree clean.

**Stage 8 prototype** (read-only SQL against the dev database, scratch scripts):
- Neighborhood GeoJSON: 441 KiB raw → 120 KiB simplified to 5 m with 6 decimals (28 KiB gzipped). All blocks as GeoJSON would be 4.8 MB.
- Block vector tiles: every z12 tile < 50 ms and < 182 KiB; a z10 tile takes 393 ms and 661 KiB. Tiles outside the region are empty.
- Resolving the latest run once beats joining `is_latest` inside the tile query.
- Per-polygon simplification at 20 m opens 87,761 m² of gaps between council districts; `ST_CoverageSimplify` opens 366 m², but it needs non-overlapping polygons.

**Created:** `docs/learning/stage-08-fastapi-backend.md` (Q1–Q9 pending).

## 2026-11-23 — Stage 8 implemented (approved "Approve as recommended", Q1–Q9)

**Decisions:** D-023.

**Built:** lesson §13.
- The `api/` uv project (package `touchgrass_api`): settings, a read-only pool, a schema-version check, dependencies, models, 3 routers + `/health`; 10 endpoints in all.
- Pipeline: `metrics.caveats()` is the single source of the caveat text (record + report); `metrics` rerun and the region reloaded.
- `.claude/launch.json` for the dev server.
- Tests: 35 in `api/` (one runs on real data), plus 1 pipeline assertion. Ruff and mypy are clean in both projects; pipeline 242 passed.

**Performance finding:** the tile query's outer `IS NOT NULL` filter was pushed into the inlined CTE, so `ST_AsMVTGeom` ran twice per block. `AS MATERIALIZED` took z12 p95 from 108 to 69 ms. Decoded tiles are identical. Parallelism and the attribute count were ruled out first.

**Results (lesson §14):** areas p95 197 ms (target 500); rankings and metrics < 10 ms; parks 90 ms.

**Not verified:** a browser client against the API (Stage 9). Nothing committed. The metrics record shows uncommitted code until Stage 8 is committed and `metrics` rerun.

## 2026-11-23 — Stage 8 committed and complete; Stage 9 prepared (no project code)

**Stage 8:**
- Committed as `0747c94` (amended once to include `api/.python-version`); `metrics` rerun on the clean commit and reloaded.
- Verified: api 35 and pipeline 242 tests pass; ruff and mypy clean in both; `/health` ok; tree clean.

**Stage 9 prototype** (a single-file page in scratch, served on :5173 through a temporary `.claude/launch.json` entry, reverted afterwards):
- OpenFreeMap positron works without a key, and MapLibre shows its attribution automatically. Its ToS: no SLA.
- MapLibre 6.17 ships only as an ES module; the global-script CDN URL returns 404.
- Cold style load 8.3 s in the in-app browser; neighborhoods 222 ms; threshold toggle 74 ms; 899 blocks at z13.5.
- Hover needs `promoteId` (string ids).
- My prototype's colors made 800–1,200 m look "fine". The lesson now breaks colors at the 10-minute line.
- Fixed share breaks spread neighborhoods 3/14/14/22/40 at 10 min. Overlapping neighborhoods cover 23.5 km² (5.8%).
- The browser first failed on `localhost` because the server listened on IPv4 only; binding to `::` fixed it.

**Created:** `docs/learning/stage-09-interactive-map.md` (Q1–Q11 pending).

## 2026-11-23 — Stage 9 implemented (approved "Approve as recommended", Q1–Q11)

**Decisions:** D-024 (with version notes: TypeScript 5.9.3 for openapi-typescript; jsdom 29 for Node 25).

**Built:** lesson §13.
- `web/`: the API client and generated types, pure style functions, the MapLibre bridge, search and toggles, side panel, legend, URL state, and a phone layout.
- `.claude/launch.json` gains a `web` entry.
- 25 tests; tsc strict and Prettier are clean; `vite build` works.

**Bugs found in the browser:** missing default export; worker URL under Vite pre-bundling (`setWorkerUrl`); a dark-mode bleed; a double `pushState` from a side effect in a state updater (StrictMode); the legend covering the map on phones. Two test-side fixes: the combobox role, and Prettier reformatting the generated types. Details in lesson §13.

**Browser check (lesson §14):** search → North Tabor (54% within 10 min, 2,602 beyond) → z14 blocks; toggle, click, back/forward, area-type switch and phone layout all work, with no console errors.

**Not verified:** a real phone, and browsers other than the in-app Chromium. Nothing committed.

## 2026-11-23 — Stage 9 review changes ("go ahead with all four")

**What changed** (D-024 amendment; lesson §17):
- two explicit views;
- plain-language wording;
- a pan limit from the bbox + 600 km (400 km, tried first, cut off eastern Oregon);
- block highlight, with a clearer panel section that includes the nearest park's name.

**API:** tiles carry `nearest_park_name`. The ETag includes `RESPONSE_VERSION = 2`, because cached tiles without names were "unchanged" under a run-only ETag.

**Tests:** web 31, api 35, all passing.

**Browser-checked:**
- the residents view at z14 and the new wording;
- the blocks view: walk-time choice hidden, block legend, block outline (feature state `selected: true`), panel leading with the block "In Mt. Tabor";
- no console errors.

**Park name, verified after a caching detour:**
- The panel first showed no names. Direct fetches had them, but MapLibre's worker kept getting pre-change tiles from the HTTP cache.
- A revalidation made before the ETag was versioned had answered 304, renewing the nameless copies' 5-minute freshness.
- With a cache-busting tile URL, 2,955 of 3,144 blocks carry a name (the rest have unnamed nearest parks). The panel shows "1,222 m to **Frazer Park**".
- With the versioned ETag this can't recur past one `max-age` (5 min).
- **Possible follow-up:** put the run and format version in the tile URL itself. Tiles could then be cached as immutable and changes would show instantly.

**Pan limit measured:** 130.5–114.8°W, 40.0–51.0°N with 600 km. 400 km, tried first, cut off eastern Oregon.

## 2026-11-23 — Stage 9 second review: About page, zoom overlay, palette, design, dark mode

**Decisions:** D-024 amendment 2. Lesson §18 has the reasoning.

**Built:**
- `components/AboutPage.tsx`, `theme.ts`, and the routing helpers in `state/url.ts`.
- The redesigned `styles.css` (light and dark tokens), Inter (`@fontsource-variable/inter` 5.3.0).
- Themed MapView: basemap, parks, lines, camera kept on rebuild, faded buildings.
- The zoom overlay and the shared RdYlBu palette.

**Checked in the browser:**
- dark and light residents views;
- the theme switch keeping the camera;
- the About page (live figures, 6 caveats, sources, credit link);
- the block-view overlay at z10;
- the narrow layout (the pane was narrow);
- no console errors.

**Tests:** web 36, build OK. Nothing committed.

## 2026-11-23 — Stage 9 committed and complete; Stage 10 prepared (no project code)

**Stage 9:**
- Committed as `2635677` (yours), `8442cc7` and `6000586`.
- Verified: web 36, api 35 and pipeline 242 tests pass; Prettier clean; build OK; tree clean.
- The Stage 2–3 resume bullet is now ticked: it was true and had been left unticked.

**Stage 10 prototype** (read-only SQL, scratch):
- **Walking route vs straight line:** 85.0% vs 97.2% within 10 min city-wide. Per neighborhood the median gap is 7.4 points; the largest is Sylvan-Highlands at 65; 20 of 87 neighborhoods show none.
- **Neighborhood correlations:** zero-vehicle +0.27, people of color −0.14.
- **Resident-based comparison:** people of color are 37.7% of residents beyond 10 min vs 32.9% within; zero-vehicle households 9.7% vs 12.4%. No MOE yet.
- **No licences are stored** for any source.

**Created:** `docs/learning/stage-10-comparison-and-polish.md` (Q1–Q8 pending).

## 2026-11-24 — Stage 10 implemented (approved "Approve as recommended", Q1–Q8)

**Decisions:** D-025.

**Built:** lesson §13.
- **Pipeline:** straight-line shares; the resident comparison with MOE; licences in config and adapters; migration 0009; the loader.
- **API:** schema 9 and response version 3; the barrier ranking, tile attributes, and licences plus the comparison in `/methodology`.
- **Web:** Rankings, Compare, the charts, context, panel additions, and About as the full methodology.

**Results (lesson §14):**
- City straight-line vs walking route at 10 min: 97.2% vs 85.0%. The largest barrier gap is Sylvan-Highlands, 65 points.
- People of color are +5.2 ± 1.3 points among residents farther than 10 min (clear). Households with no vehicle are −4.5 ± 1.2 points (clear).
- All 11 sources carry a licence.

**Fixes found while building:** generated types caught fixture drift; the car-free wording misstated a household share; the prototype's car-free figures used person weights; a chart label was clipped.

**Tests:** pipeline 247, API 39, web 47. Ruff, mypy, tsc and Prettier are clean; the build works. Browser-checked in light and dark mode, at 700 px.

**Not verified:** a desktop-width browser check (the pane stayed at 700 px); a real phone. Nothing committed. The metrics record shows uncommitted code until committed and rerun.

**Stage 10:** committed as `698066d` ("Commit and /learn:advance"). Verified on the commit: pipeline 247, API 39, web 45 + 2 skipped (the live-API contract test); tsc clean. The metrics rerun on the clean commit was stopped at your request; the Stage 11 fresh-clone rehearsal recomputes metrics from committed code instead.

## 2026-11-24 — Stage 11 prepared

**Checked:** database 99 MB; raw data 595 MB; 4 live ArcGIS sources (City ×3, Metro ORCA) whose bytes can change; `pageFromPath` assumes the site root, so it breaks under GitHub Pages' `/touch-grass/`; the API sets its read-only and timeout safeguards through startup `options`, which some poolers reject. Hosting facts (Neon Free 1 GB with PostGIS; Render free spins down after 15 min) from the providers' docs, lesson §15.

**Created:** `docs/learning/stage-11-hardening-deployment.md` (Q1–Q8 pending; account, secret and push steps listed in §11 as yours).

## 2026-11-24 — Stage 11 implemented (approved "Approve as recommended", Q1–Q8)

**Decisions:** D-026.

**Built (lesson §13):**
- **CI:** `ci.yml` with pipeline, api, web, e2e and audit jobs; PostGIS service.
- **Pages:** `pages.yml`, inactive until `VITE_API_URL` is set.
- **Reproducibility:** `touchgrass reference` and `results/portland-or/reference.json`.
- **Database:** `touchgrass db create-reader`; `db status` shows the PostGIS version.
- **API:** pool and session-option settings; Dockerfile; `render.yaml`.
- **Web:** base-path routing, `404.html`, the wake retry and notice.
- **Browser tests:** `e2e/` Playwright smoke tests; screenshots.
- **Docs:** portfolio README, `DEPLOYMENT.md`, ARCHITECTURE §8.

**Results (lesson §14):**
- **Fresh-clone rehearsal:** reproduced. All 11 source checksums and every headline figure match, from a fresh download; 687 s from an empty folder to a loaded database.

**Fixes found while building:**
- Phones scrolled sideways on every page (a `1fr` grid column, then a long URL on About). Fixed, and now tested at 390 px.
- The run id includes the commit, so reproduction is judged on checksums and figures.
- uvicorn re-raises SIGTERM, which skipped cleanup; it moved to a Playwright teardown.

**Tests:** pipeline 253, API 42, web 52, browser 8. Linters clean; no known vulnerabilities reported.

**Not verified:** CI on GitHub; the deployment (needs your accounts); the live smoke test. Nothing committed.

## 2026-11-25 — Deployed

- Stage 11 committed as `fe0cf72` and pushed. **CI:** all five jobs green on GitHub.
- **Deployed:** Neon, Render and Pages. `/health` ok, schema 9; CORS allows only the Pages origin.
- **Live smoke tests:** 8/8 pass against https://luangia.github.io/touch-grass/.
- **Fixed during deployment:**
  - Render's Root Directory set to `api`.
  - `VITE_API_URL` moved to a repository variable.
  - The smoke test now allows the 404 status of Pages' deep-link fallback page, and only that request.
- **Not verified:** the wake notice on a sleeping host.

## 2026-11-25 — Portfolio notes

- **Created** `docs/PORTFOLIO.md`: descriptions, architecture, stack rationale, data flow, challenges, trade-offs, screenshots to capture, validation, lessons learned, future work, resume bullets, an interview walkthrough and 22 interview questions.
- **Checked:** every figure against the database, run records, `reference.json` or this log. Where older notes disagree with today's data (park access points: 11,878 in an early Stage 5 run, 15,601 in the current network report), today's figure is used.
- **Kept local:** you git-ignored `docs/PORTFOLIO.md`, so the README doesn't link it.
