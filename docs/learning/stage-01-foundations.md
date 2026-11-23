# Stage 1 — Foundations, Environment & Region Config

> Status: **implemented 2026-11-22.** §1–10 are the preparation lesson; §12 describes what was actually built and how it differs from the plan.

## 1. Objective and expected outcome

At the end of Stage 1 you have an **empty but correct skeleton**:

- a Git repo with sensible ignores,
- a Python package `touchgrass` (managed by `uv`) with lint, type-check and test tooling,
- a local PostGIS database started by `docker compose`,
- `config/regions/portland-or.yaml`, validated by a typed schema,
- a CLI command `touchgrass regions validate portland-or`.

Nothing geographic is downloaded or computed yet. The outcome is a foundation where **wrong spatial setups fail fast**. The main example: a region configured with a coordinate system in degrees is rejected before any analysis runs.

## 2. Why this stage exists

Every later stage depends on three things set here:

1. **Where region-specific facts live.** If the Portland CRS or FIPS codes get typed into a function in Stage 2, the multi-region requirement is broken from day one. Stage 1 creates the one place they're allowed to live.
2. **Which coordinate system measurements use.** Most silent GIS bugs are CRS bugs: a distance in degrees, or an area in Web Mercator. We decide the rule once and enforce it in code.
3. **A reproducible environment.** Pinned dependencies and a containerised database mean "it worked yesterday" stays true, and a reviewer can reproduce your results.

## 3. Dependencies

- Accepted decisions D-001 (study area), D-004 (multi-region config), D-008 (CRS rules), D-009 (stack), D-012 (git).
- **Machine prerequisites:** `uv` 0.10.2 ✅, Python 3.12.7 ✅, git 2.53 ✅, Node 25 ✅ (not needed until Stage 9), **Docker ❌ not installed** (see §8.1).

## 4. Concepts

### 4.1 Vector vs raster data

**Intuition.** There are two ways to describe the world:

- **Vector:** "here is the outline of Laurelhurst Park". A list of coordinates forming a polygon, plus attributes (name, area).
- **Raster:** "here is a grid over Portland; each 30 m cell holds a value" (land cover class, % tree canopy).

**Mechanics.**

| | Vector | Raster |
|---|---|---|
| Primitive | Point, LineString, Polygon (+ Multi-) | Grid of cells (pixels) with an origin, cell size, and CRS |
| Good for | Discrete objects with sharp edges: parks, streets, census blocks | Continuous fields: elevation, temperature, canopy |
| Precision | As precise as the coordinates | Limited by cell size (a 30 m cell can't represent a 10 m pocket park) |
| Typical operations | Joins, intersections, buffers, network analysis | Map algebra, zonal statistics, resampling |
| Formats | GeoJSON, Shapefile, GeoPackage, GeoParquet | GeoTIFF / Cloud-Optimized GeoTIFF |

**In TouchGrass.** The MVP is entirely vector: parks, blocks, neighborhoods, the street network. Raster arrives with the deferred tree-canopy and heat features. That's why ARCHITECTURE lists NLCD and canopy as "future", accessed with *zonal statistics* (summarising raster cells inside each vector polygon).

**Pitfall.** Rasterising vectors, or vectorising rasters, loses information. Keep data in its native model until an operation truly needs the other.

### 4.2 Geospatial file formats

The same parks polygon can be stored in several formats.

| Format | What it is | Strengths | Weaknesses | Our use |
|---|---|---|---|---|
| **GeoJSON** (RFC 7946) | JSON text: `{"type":"Feature","geometry":{...},"properties":{...}}` | Human-readable, the web's lingua franca, MapLibre reads it natively | Verbose, slow for big data, **WGS 84 lon/lat only** by spec | API responses (Stage 8) |
| **Shapefile** | 1990s Esri format; really 3+ files: `.shp` geometry, `.shx` index, `.dbf` attributes, `.prj` CRS | Universally supported (TIGER/Line ships as shapefiles) | 10-char field names, 2 GB limit, weak types, multiple files | Input only (TIGER) |
| **GeoPackage** | OGC standard, a single SQLite file | One file, many layers, real types | Row-oriented, so slower for analytics than columnar formats | Possible graph export (Stage 5) |
| **GeoParquet** | Apache Parquet (columnar) + geometry as WKB + CRS metadata (PROJJSON) | Fast, compressed, typed, CRS travels with the data | Not human-readable; newer | **All intermediates** in `data/processed/` |
| **MVT** (Mapbox Vector Tile) | Protobuf tiles of clipped, quantised geometry for one z/x/y tile | Efficient map rendering of large layers | Display-only (lossy) | Block layer (Stage 8) |

**Key idea: WKB/WKT.** Under most formats, geometry is encoded as *Well-Known Text* (`POLYGON((x1 y1, x2 y2, …))`) or its binary form, *Well-Known Binary*. PostGIS, Shapely, and GeoParquet all speak WKB. That shared encoding is why data moves between them cheaply.

**GeoJSON gotchas:**
- Coordinate order is `[longitude, latitude]` (x, y). Google Maps and many humans write lat, lon, which is a classic swap bug that puts Portland in Antarctica.
- The right-hand rule: exterior rings should be counterclockwise.

### 4.3 Coordinate Reference Systems (CRS)

**Intuition.** A coordinate pair like `(-122.68, 45.52)` is meaningless until you know *what system it's in*. A CRS answers three questions:

1. **What shape is the Earth?** A *datum* and its *ellipsoid*. The Earth is lumpy; an ellipsoid is a smooth mathematical approximation, and a datum pins it to the real planet.
2. **What are the units and axes?** Degrees of latitude/longitude, or meters east/north on a flat plane.
3. **If flat, how was the globe flattened?** The *projection*.

**EPSG codes** are IDs in a registry of CRS definitions. Three matter to us:

| EPSG | Name | Type | Units | Role in TouchGrass |
|---|---|---|---|---|
| 4326 | WGS 84 | Geographic | degrees | Storage, API, GeoJSON |
| 4269 | NAD83 | Geographic | degrees | What TIGER/Line ships in |
| 3857 | WGS 84 / Pseudo-Mercator | Projected | "meters" (distorted) | Web map display only |
| **26910** | NAD83 / UTM zone 10N | Projected | meters | **All Portland measurements** |

### 4.4 Geographic vs projected coordinates — the most important concept in this stage

**Geographic coordinates** are angles on an ellipsoid. A degree of latitude is always about 111 km. A degree of longitude shrinks with latitude: it is about 111 km × cos(latitude). At Portland (~45.5°N), cos(45.5°) ≈ 0.70, so 1° of longitude is only about 78 km.

That means "distance" computed as √(Δlon² + Δlat²) mixes two different units:

```python
# WRONG: two points 0.01° apart east-west vs north-south in Portland
dist_ew_deg = 0.01   # really ≈ 780 m
dist_ns_deg = 0.01   # really ≈ 1,110 m
# Both report "0.01" — a 42% error, and the unit "degrees" isn't a length.
```

**Projected coordinates** come from a mathematical function that maps the curved surface to a plane. On the plane, Euclidean geometry works and the units are meters. Every projection distorts something (area, shape, distance, or direction). You choose one that keeps distortion negligible for your area and purpose.

**UTM (Universal Transverse Mercator).**
- It splits the world into 60 zones, each 6° of longitude wide.
- Portland (~122.7°W) falls in **zone 10** (126°W–120°W). Its central meridian is 123°W.
- Within a zone, scale error stays within roughly ±0.1% (a scale factor of 0.9996 at the central meridian). That's tiny compared with an 800 m walking threshold: under a meter.
- **NAD83 / UTM 10N = EPSG:26910.**
- Why UTM rather than Oregon State Plane? State Plane zones are even more accurate locally, but the Oregon ones use **feet**. A UTM zone exists for every US city and always uses meters, which keeps code and configs uniform across regions.

**Why Web Mercator (3857) must never be used for measurement.** Mercator stretches both axes by 1 / cos(latitude):
- At Portland that's ≈ 1.43× in length and ≈ **2.0× in area**.
- A 10-acre park would measure as 20 acres.
- MapLibre still *displays* in Web Mercator, which is fine because display isn't measurement.

**Datums: NAD83 vs WGS 84.** They began nearly identical. Because NAD83 is fixed to the North American plate, they now differ by about 1–2 m in Oregon. That's irrelevant at our analysis scale, but it's why transformations between them exist, and why we always transform rather than "relabel".

### 4.5 How PyProj works underneath

PyProj is the Python binding to **PROJ**, the C library that nearly all open-source GIS (GDAL, PostGIS, QGIS, GeoPandas) uses for coordinate operations.

```
          ┌───────────── PROJ database (proj.db, SQLite) ─────────────┐
          │ EPSG definitions, datum shift grids, transformation paths │
          └───────────────────────────────┬───────────────────────────┘
CRS.from_epsg(26910) ───────────────────▶ │ parse definition → CRS object (WKT2 / PROJJSON)
Transformer.from_crs(4326, 26910) ──────▶ │ pick best operation pipeline:
                                          │   WGS84 → NAD83 (datum op) → UTM projection
transformer.transform(lon, lat) ────────▶ │ apply pipeline (vectorised over arrays)
```

The checks we'll use in Stage 1:

```python
from pyproj import CRS

crs = CRS.from_epsg(26910)
crs.is_projected                 # True
crs.is_geographic                # False
crs.axis_info[0].unit_name       # 'metre'
crs.name                         # 'NAD83 / UTM zone 10N'

CRS.from_epsg(4326).is_projected  # False → our validator rejects it for analysis
CRS.from_epsg(3857).is_projected  # True!  → "projected" alone isn't enough (see §8.3)
```

**The axis-order trap.**
- Officially, EPSG:4326 lists **latitude first**. PROJ respects that by default.
- GeoJSON, Shapely, and almost all web software use longitude first.
- So we always build transformers with `always_xy=True`:

```python
from pyproj import Transformer
t = Transformer.from_crs(4326, 26910, always_xy=True)
x, y = t.transform(-122.6765, 45.5231)   # lon, lat in → easting, northing (meters) out
```

Forgetting `always_xy` silently swaps axes. The resulting points land in the ocean or fail to transform (`inf`). A test will guard this.

### 4.6 Configuration-driven design (and validating config with types)

**Intuition.** Separate *what is true about a place* (data) from *how we analyse places* (code). Code asks the config "what CRS?", "what thresholds?", and never assumes.

**Why validate?** A YAML file is untyped text. Without validation, a typo (`analysis_crs: 4362`) surfaces three stages later as a nonsense number. With validation, it fails immediately with a readable message. That's the "fail fast" principle.

**Pydantic v2 mechanics.**
- You declare a class with type-annotated fields.
- Pydantic parses the input dict, coerces and checks types, runs your custom validators, and either returns a typed object or raises `ValidationError` listing *every* problem.
- In v2 the core is written in Rust (`pydantic-core`), so validation is fast. Irrelevant for us, but it's why v2 is standard now.

Planned shape (illustrative):

```python
class AnalysisParams(BaseModel):
    walk_speed_kmh: float = Field(4.8, gt=0, le=10)
    thresholds_min: list[PositiveInt] = [5, 10, 15]
    max_snap_m: float = Field(500, gt=0)

    @field_validator("thresholds_min")
    @classmethod
    def sorted_unique(cls, v: list[int]) -> list[int]:
        if v != sorted(set(v)):
            raise ValueError("thresholds must be unique and ascending")
        return v

class RegionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")     # unknown keys = typo → error
    region_id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str
    country: Literal["US"]
    analysis_crs: int
    state_fips: str = Field(pattern=r"^\d{2}$")
    county_fips: list[str]
    buffer_m: float = Field(gt=0)
    analysis: AnalysisParams

    @field_validator("analysis_crs")
    @classmethod
    def must_be_metric_projected(cls, epsg: int) -> int:
        ...  # uses touchgrass.core.crs.assert_analysis_crs (see §8.3)
```

`extra="forbid"` matters: `bufer_m: 2000` would otherwise be silently ignored and the default used.

A neat derived value: walking thresholds in meters are computed (5 min × 4.8 km/h = 400 m), never stored. That keeps a single source of truth.

### 4.7 Reproducible environments: uv, lockfiles, and containers

- **`uv`** creates the virtual environment, resolves dependencies, and writes **`uv.lock`**, which pins exact versions and hashes of every transitive package. `uv sync` on another machine reproduces the same environment. `uv run <cmd>` runs inside it without activating anything.
- **`pyproject.toml`** declares direct dependencies and tool settings (ruff, mypy, pytest) in one file.
- **Docker / Compose.** PostGIS is PostgreSQL plus a C extension linked against GEOS (geometry ops), PROJ, and GDAL. Installing those natively is version-sensitive. A container image (`postgis/postgis:<pg>-<postgis>`) bundles a tested combination. `docker-compose.yml` records the port, credentials (from `.env`), and a **named volume** so data survives restarts.

```
host (macOS)                         container
┌─────────────────────┐             ┌─────────────────────────────┐
│ uv run touchgrass … │──TCP 5432──▶│ PostgreSQL 17 + PostGIS 3.5 │
│ psql / DBeaver      │             │   data → volume tg_pgdata   │
└─────────────────────┘             └─────────────────────────────┘
```

(Exact image tag to be verified when the file is written.)

### 4.8 Quality tooling

| Tool | Catches | Why for GIS code specifically |
|---|---|---|
| **ruff** | Lint and formatting | Consistency; catches unused imports and shadowed names |
| **mypy** (strict on our package) | Type errors | Distinguishes `int` EPSG from `CRS`, meters from minutes (via clear names and types) |
| **pytest** | Behaviour | Spatial bugs are silent, so tests with *hand-computed* expectations are our main defence |

## 5. How the concepts relate

```
          real world ──(vector model)──▶ geometries + attributes
                                             │ encoded as WKB / GeoJSON / GeoParquet
                                             ▼
   coordinates mean nothing without ──▶  CRS (datum + units + projection)
                                             │ geographic (4326): store & exchange
                                             │ projected metric (26910): measure
                                             ▼
   which CRS, thresholds, sources ──▶  region config (YAML) ──validated by──▶ Pydantic + PyProj
                                             │
                                             ▼
                             every later stage reads config, never hardcodes
```

## 6. Where Stage 1 sits in the architecture

```
config/regions/portland-or.yaml  ◀── Stage 1 creates
          │
          ▼
pipeline/src/touchgrass/
  config/  (schema + loader)     ◀── Stage 1 creates
  core/crs.py                    ◀── Stage 1 creates (CRS rules, shared by all regions)
  cli.py   (regions validate)    ◀── Stage 1 creates; Stage 2 adds `fetch`, Stage 4 `load`, …
  sources/ (adapters)            ◀── Stage 2
docker-compose.yml → PostGIS     ◀── Stage 1 starts it; Stage 4 creates tables
```

**Note on config scope.** In Stage 1 the YAML holds region identity, CRS, FIPS codes, buffer, and analysis parameters. The `study_boundary`, `analysis_areas`, `parks`, `population` sections from ARCHITECTURE §7 are **added in the stage that implements their adapters** (Stages 2–3). That way the schema never accepts settings that nothing uses yet.

## 7. Alternatives considered

| Choice | Alternative | Why not (for now) |
|---|---|---|
| YAML config | TOML / JSON / Python module | YAML handles nested lists of sources readably; a Python config could execute code, which we want to avoid |
| Pydantic | dataclasses + manual checks, JSON Schema | Pydantic gives typed objects *and* error aggregation; FastAPI (Stage 8) uses it too, so it's one model library across the project |
| Typer CLI | argparse (stdlib), Click | Typer is a thin layer over Click that uses type hints and matches the FastAPI style. argparse adds no dependency but is more verbose — **your call (§9)** |
| UTM 10N | Oregon State Plane North (ft), custom equal-area | Meters and a uniform rule for every US city |
| Docker PostGIS | Homebrew Postgres + PostGIS, Postgres.app | Native installs drift by version; Docker matches deployment. Postgres.app is a fine fallback |

## 8. Implementation approach

### 8.1 Prerequisite: Docker is not installed

PostGIS needs a container runtime. Options (you install; I won't install system software):
- **Docker Desktop:** official and most documented. Free for personal use.
- **OrbStack:** lightweight on macOS, docker-compatible CLI. Free for personal use.
- **Colima + docker CLI (Homebrew):** fully open source, command-line only.
- **No Docker: Postgres.app** (bundles PostGIS). Then `docker-compose.yml` still ships for reproducibility, but you run Postgres natively.

### 8.2 Planned repository layout

```
TouchGrass/
├── .gitignore               # data/raw, data/processed, .env, .venv, caches, node_modules
├── .env.example             # POSTGRES_USER/PASSWORD/DB/PORT (no real secrets committed)
├── docker-compose.yml       # one service: db (postgis), named volume, healthcheck
├── config/regions/portland-or.yaml
├── data/.gitkeep            # data itself is never committed
├── pipeline/
│   ├── pyproject.toml       # deps + ruff/mypy/pytest config; console script `touchgrass`
│   ├── uv.lock
│   ├── src/touchgrass/
│   │   ├── __init__.py
│   │   ├── cli.py           # `regions validate`, `regions show`
│   │   ├── config/models.py # Pydantic schema
│   │   ├── config/loader.py # find + parse + validate YAML
│   │   └── core/crs.py      # assert_analysis_crs, make_transformer(always_xy=True)
│   └── tests/
│       ├── fixtures/regions/   # valid + deliberately broken YAML files
│       ├── test_config.py
│       └── test_crs.py
└── docs/…                   # existing
```

This uses the `src/` layout, which prevents tests from importing the package from the working directory by accident. ARCHITECTURE §2 will be updated to match (`pipeline/src/touchgrass/...`).

Dependencies, kept minimal for this stage:
- runtime: `pydantic`, `pyyaml`, `pyproj`, `typer`
- dev: `pytest`, `ruff`, `mypy`, `types-PyYAML`
- **Not yet:** GeoPandas, OSMnx, psycopg. They arrive in the stage that needs them.

### 8.3 The CRS rule (shared core)

```python
def assert_analysis_crs(epsg: int) -> CRS:
    crs = CRS.from_epsg(epsg)                  # raises CRSError for unknown codes
    if not crs.is_projected:
        raise ValueError(f"EPSG:{epsg} is geographic (degrees); analysis needs meters")
    if crs.axis_info[0].unit_name not in ("metre", "meter"):
        raise ValueError(f"EPSG:{epsg} uses {crs.axis_info[0].unit_name}; meters required")
    if epsg == 3857:                           # projected + meters, yet area ×2 at 45°N
        raise ValueError("Web Mercator distorts distance/area; use a local projection")
    return crs
```

Possible extension: check that the region's center falls inside the CRS's *area of use* (`crs.area_of_use` gives the bounds). That would catch "UTM zone 18 for Portland". The region center only becomes known once the boundary is downloaded (Stage 2), so for now the config can carry an optional rough `center_lonlat` used just for this check. **Your call (§9).**

### 8.4 Example CLI behaviour (planned)

```
$ uv run touchgrass regions validate portland-or
✓ portland-or (Portland, Oregon)
  analysis CRS: EPSG:26910 — NAD83 / UTM zone 10N (metre)
  thresholds:   5 / 10 / 15 min → 400 / 800 / 1200 m at 4.8 km/h
  buffer:       2000 m

$ uv run touchgrass regions validate broken-geographic
✗ broken-geographic: 1 error
  analysis_crs: EPSG:4326 is geographic (degrees); analysis needs meters
(exit code 1)
```

## 9. Decisions for you before implementation (resolved: Docker Desktop, Typer, `center_lonlat` check included)

1. **Container runtime:** Docker Desktop / OrbStack / Colima, or Postgres.app (no Docker). *Recommendation: OrbStack or Docker Desktop.* Either way, you install it.
2. **CLI library:** Typer (*recommended*) or stdlib argparse.
3. **Optional `center_lonlat` check against the CRS area of use:** include (*recommended*, cheap and catches wrong-zone errors) or skip.

## 10. Testing and acceptance criteria

**Tests (pytest):**
- The valid Portland config loads, and derived thresholds equal 400/800/1200 m.
- Rejected configs: geographic CRS (4326), Web Mercator (3857), feet-based State Plane (2269), unknown EPSG, unknown key (typo), non-ascending thresholds, bad `region_id` slug, bad FIPS.
- CRS transformer: `(-122.6765, 45.5231)` → easting/northing in plausible UTM 10N ranges (easting ≈ 525 km, northing ≈ 5,040 km). A swapped-axis call does **not** produce those values. This guards `always_xy`.
- If the area-of-use check is approved, a Portland center with UTM zone 18N (EPSG:26918) is rejected.

**Manual checks:**
- `docker compose up -d db`, then `SELECT postgis_full_version();` via `docker compose exec db psql …`.

**Acceptance criteria** (from PROJECT_PLAN, Stage 1):
- [ ] `touchgrass regions validate portland-or` succeeds; invalid configs fail with clear messages and a non-zero exit code.
- [ ] PostGIS reachable; version printed.
- [ ] `ruff check`, `ruff format --check`, `mypy`, `pytest` all pass.
- [ ] No Portland-specific value outside `config/` (and test fixtures). Checked with a `grep` for `26910`, `Portland`, `41` FIPS in `src/`.
- [ ] Docs updated (implementation log, status, architecture path fix); this lesson gets a "what was built" section.

## 11. Check your understanding (answer after implementation)

1. Why is computing `area` on a GeoDataFrame in EPSG:4326 wrong, and what does GeoPandas warn?
2. EPSG:3857 is "projected and in meters". Why do we still reject it?
3. What does `always_xy=True` protect against, and why does the problem exist?
4. Why is `extra="forbid"` important for a config file?
5. A colleague wants to onboard Chicago. Which file do they create, and which value in it changes from Portland's?

## References

- PROJ / PyProj docs: https://pyproj4.github.io/pyproj/stable/
- EPSG registry: https://epsg.io/26910 (community viewer) and https://epsg.org (official)
- GeoJSON RFC 7946: https://datatracker.ietf.org/doc/html/rfc7946
- GeoParquet spec: https://geoparquet.org/
- Pydantic v2 docs: https://docs.pydantic.dev/
- uv docs: https://docs.astral.sh/uv/
- PostGIS Docker images: https://hub.docker.com/r/postgis/postgis

## 12. What was built (post-implementation)

### Differences from the plan
- **CRS rule generalised.** Instead of `if epsg == 3857`, the rule rejects any *normal-aspect Mercator* by inspecting `crs.coordinate_operation.method_name` ("Popular Visualisation Pseudo Mercator", "Mercator (variant A)"). "Transverse Mercator" (UTM) is still allowed. This catches EPSG:3395 and other Mercator variants without a code list.
- **PostGIS image runs under emulation** on Apple Silicon (D-013). The official image is amd64-only.
- **Dependency cutoff** `exclude-newer` added to `pyproject.toml` (D-014).
- **Automated hardcoding guard** (`tests/test_no_region_hardcoding.py`) instead of only a manual grep. It reads every region config and fails if its id, CRS code, or city name appears in `src/`.

### Walkthrough: `touchgrass regions validate portland-or`

```
cli.regions_validate("portland-or")
  └─ loader.load_region
       ├─ _resolve: not a path → find_config_dir() walks up from cwd → <repo>/config
       ├─ yaml.safe_load(...)            # text → dict (no code execution)
       ├─ RegionConfig.model_validate     # dict → typed, frozen object
       │    ├─ field checks: slug pattern, FIPS patterns, ranges, Literal["US"], extra=forbid
       │    ├─ analysis_crs → core.crs.assert_analysis_crs(26910)
       │    │     known? → projected? → meter axes? → not normal Mercator? ✓
       │    └─ after all fields pass: center_lonlat inside 26910's area of use? ✓
       └─ region_id == file stem? ✓
  └─ _summary: derived thresholds 5/10/15 min × 80 m/min = 400/800/1200 m
```

Errors are collected, not raised one by one: Pydantic gathers every field error, the loader turns each into a `loc: message` line, and the CLI prints them all and exits 1.

### Two things the tests taught us
1. **Swapped (lat, lon) passes range checks.** `[41.88, -87.63]` has a valid longitude *and* a valid latitude. Only the area-of-use check caught it. Range validation is necessary but not sufficient for coordinates.
2. **Leaks happen in docstrings first.** The guard flagged "Portland" in my own examples. A city name in shared code, even in a comment, is often the first step towards city-specific logic.

### Independent verification
PostGIS (its own PROJ 9.8.1 build inside the container) and PyProj on the host produced **identical** UTM 10N coordinates for the same point. Two independent implementations agreeing is stronger evidence than either alone.

### Files
| File | Role |
|---|---|
| `pipeline/src/touchgrass/core/crs.py` | Region-agnostic CRS rules and transformer factory |
| `pipeline/src/touchgrass/config/models.py` | Pydantic schema (`RegionConfig`, `AnalysisParams`) |
| `pipeline/src/touchgrass/config/loader.py` | Config discovery, YAML parsing, error formatting |
| `pipeline/src/touchgrass/cli.py` | Typer CLI (`regions list/validate/show`) |
| `config/regions/portland-or.yaml` | The only place Portland facts live |
| `pipeline/tests/*` | 55 tests: CRS, schema, loader, CLI, hardcoding guard |
| `docker-compose.yml`, `.env.example` | Local PostGIS |

### Known limitations
- `center_lonlat` is hand-entered. From Stage 2 it can be checked against the downloaded boundary's actual centroid.
- The area-of-use check tests one point, not the whole region. A region straddling a UTM zone edge would pass. That's acceptable because UTM distortion just outside a zone is still small, but it's worth knowing.
- `country: Literal["US"]` is intentionally narrow until non-US population adapters exist.
- Docker's CLI is in `~/.docker/bin`. If `docker` isn't found in your terminal, add it to your PATH (Docker Desktop → Settings → Advanced can do this).

### Exercises
1. **Practical.** Without looking at `tests/conftest.py`, write `config/regions/seattle-wa.yaml` (pick the UTM zone yourself and find the King County FIPS code). Run `uv run touchgrass regions validate seattle-wa`. Then delete it, or keep it if you want the hardcoding guard to cover Seattle too.
2. **Break it on purpose.** Set Portland's `analysis_crs` to `32611` (WGS 84 / UTM 11N). Predict which check fails and why, then run it.
3. **Scenario.** A colleague's buffer of Portland parks reports Forest Park as ~10,400 acres; the official figure is about 5,200. What is the most likely cause, and which line of our code would have prevented it?
