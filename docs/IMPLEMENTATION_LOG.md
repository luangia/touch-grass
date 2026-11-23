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
