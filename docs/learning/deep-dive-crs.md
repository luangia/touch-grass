# Deep Dive: Coordinate Reference Systems (CRS)

> Companion to [stage-01-foundations.md](stage-01-foundations.md) §4.3–4.5, which covers the basics. This lesson goes underneath: what PROJ actually does, why, and how it shapes TouchGrass's code.
>
> Every number marked **(measured)** was produced on this machine with the project's pinned environment (pyproj 3.8.0, PROJ 9.8.1) or the project's PostGIS container (PROJ 9.8.1), on 2026-11-22.

---

## 1. Intuition

A coordinate is an **answer without its question**. `(525263.68, 5041113.74)` means nothing until you know:

- Where is zero? (the origin)
- What does one unit mean? (meters? degrees? US survey feet?)
- Which way do the axes point, and which comes first?
- What shape of Earth were the numbers measured on?

A **Coordinate Reference System** is that question. It's metadata that turns numbers into a position on the planet.

An analogy: "Meet me at 12th and 300" is useless until you know the city (datum), whether the numbers are street numbers or block counts (units), and which is the street and which is the avenue (axis order). Two people with different assumptions will each be "right" and still end up in different places. That's exactly how CRS bugs behave: **nothing crashes, the numbers are just wrong.**

---

## 2. The problem a CRS solves

Three facts make CRSs unavoidable:

1. **The Earth is not a sphere, and not even an ellipsoid.** It's a lumpy, rotating, deforming body. To do maths we approximate it with an ellipsoid, and different approximations fit different continents best.
2. **You can't flatten a curved surface without distortion.** Gauss's *Theorema Egregium* (1827) proves a sphere and a plane have different intrinsic curvature, so **no map projection preserves all of distance, area, and angle**. Every projection chooses what to sacrifice.
3. **Data comes from everywhere.** In TouchGrass:
   - TIGER/Line ships in **NAD83** degrees (EPSG:4269)
   - OSM data is in **WGS 84** degrees (EPSG:4326)
   - web maps display in **Web Mercator** (EPSG:3857)
   - our analysis runs in **UTM 10N** meters (EPSG:26910)

   Combining them is only correct if each is labelled and converted properly.

What's at stake, using a real Portland example **(measured)**: downtown (−122.6765, 45.5231) to a point near Gresham (−122.4302, 45.4984).

| Method | Result | Error vs. true distance |
|---|---|---|
| Geodesic on the GRS80 ellipsoid (ground truth) | 19,441.45 m | — |
| Euclidean distance in UTM 10N (EPSG:26910) | 19,433.97 m | **−0.04%** |
| Euclidean distance in Web Mercator (EPSG:3857) | 27,697.31 m | **+42.5%** |
| Euclidean distance in raw degrees | 0.24754 "degrees" | meaningless unit |

A 42.5% error on an 800 m threshold is 340 m. That would reclassify thousands of residents. The UTM error on 800 m is about 0.3 m.

---

## 3. Underlying mechanics

A CRS is built from layers. Here's the stack for our analysis CRS, printed from `CRS.from_epsg(26910).to_wkt()` **(measured)**:

```
PROJCRS["NAD83 / UTM zone 10N",                    ← projected CRS (planar, meters)
  BASEGEOGCRS["NAD83",                             ← its geographic base (degrees)
    DATUM["North American Datum 1983",             ← how the ellipsoid is tied to Earth
      ELLIPSOID["GRS 1980", 6378137, 298.257222101]]   ← shape: semi-major axis, inverse flattening
    PRIMEM["Greenwich", 0]]                        ← where longitude 0 is
  CONVERSION["UTM zone 10N",                       ← the projection (flattening function)
    METHOD["Transverse Mercator"],
    PARAMETER["Latitude of natural origin", 0],
    PARAMETER["Longitude of natural origin", -123],  ← central meridian
    PARAMETER["Scale factor at natural origin", 0.9996],
    PARAMETER["False easting", 500000],            ← keeps eastings positive
    PARAMETER["False northing", 0]]
  CS[Cartesian, 2], AXIS["easting", east], AXIS["northing", north], LENGTHUNIT["metre", 1]
```

Each layer has its own job.

### 3.1 Ellipsoid: the shape

An ellipsoid of revolution is defined by two numbers: the semi-major axis *a* (equatorial radius) and the flattening *f* = (a − b)/a.

- GRS 1980: *a* = 6,378,137 m, 1/*f* = 298.257222101.
- WGS 84's ellipsoid differs from GRS80 by ~0.1 mm in the polar radius. Practically identical.
- A sphere is the special case *f* = 0. It's fine for display, but costs ~0.3–0.5% in distance.

### 3.2 Datum: anchoring the shape to the real Earth

The ellipsoid says *what shape*. The datum says *where it sits*: its centre, its orientation, and which physical monuments it's tied to.

- **WGS 84** is the GPS datum. It's tied to the global average and realised by GPS satellites.
- **NAD83** is tied to the North American tectonic plate. As the plate drifts (a few cm/year), NAD83 coordinates of a fixed Portland building stay constant, while its WGS 84 coordinates slowly change.
- In 1986 the two were nearly coincident. Today the current realisations differ by roughly 1–2 m in Oregon.

Converting between datums is a **transformation** (empirical, has an accuracy) rather than a **conversion** (exact maths). Common methods:

| Method | Parameters | Typical accuracy |
|---|---|---|
| Null / "ballpark" | 0 (assume identical) | metres |
| Helmert 3-parameter | 3 translations | ~1–5 m |
| Helmert 7-parameter | 3 translations, 3 rotations, 1 scale | ~0.1–1 m |
| Time-dependent 14-parameter | the 7 above plus their rates | cm, needs an epoch date |
| Grid shift (NADCON, NTv2, NADCON5) | lookup grid of offsets | cm |

**A surprise from our measurements.**
- PROJ's chosen path for `EPSG:4326 → EPSG:26910` is "Inverse of NAD83 to WGS 84 (1) + UTM zone 10N", declared accuracy **4.0 m** **(measured)**.
- "NAD83 to WGS 84 (1)" (EPSG:1188) is a **zero-parameter** transformation: it treats the two datums as identical, and is honest that this is only good to about 4 m.
- That's why WGS 84 / UTM 10N (EPSG:32610) and NAD83 / UTM 10N (EPSG:26910) give the same coordinates for downtown Portland to within 0.1 mm **(measured)**.
- `TransformerGroup` also reported **49 more accurate operations that are unavailable** because their grid files aren't installed **(measured)**. `PROJ_NETWORK` is off, so PROJ won't download grids.

**Is 4 m acceptable for TouchGrass?** Yes:
- Our smallest threshold is 400 m.
- OSM geometry itself is typically only accurate to a few metres.
- More importantly, the error is a *near-uniform shift*: every point in Portland moves by about the same vector. **Relative distances between points, which are what we measure, are essentially unaffected.**

A survey-grade project, such as a property boundary, would need the grid-based transformation and an epoch date.

### 3.3 Projection: flattening the ellipsoid

A projection is a pair of functions:

```
forward:  (λ, φ) on the ellipsoid  →  (E, N) on a plane       (E = easting, N = northing)
inverse:  (E, N)                    →  (λ, φ)
```

**Transverse Mercator (TM).** Imagine wrapping a cylinder around the Earth so it touches along a **meridian** (the central meridian, −123° for zone 10) instead of the equator, then projecting onto it.
- Along the touching line, scale is exact.
- Scale error grows with the **east–west distance** from that meridian, roughly k ≈ k₀ · (1 + ½(Δx/R)²), where R is the Earth's radius and Δx the east–west distance.
- TM is **conformal**: it preserves angles and local shapes, so a small square stays square.

**UTM** is TM made into a world system:
- 60 zones of 6° longitude each. Zone *n* has central meridian −183° + 6*n*°, so zone 10 → −123°.
- Scale factor at the central meridian k₀ = **0.9996**. This deliberately shrinks the centre so the scale error is spread: too small (−0.04%) at the centre, exact about 180 km either side, too large (+0.03%) at the zone edges.
- False easting 500,000 m, so eastings are never negative inside a zone.
- Northern hemisphere: false northing 0, so northing ≈ metres from the equator (5,041 km ≈ 45.5°).

PROJ can report the actual distortion at any point (`Proj.get_factors`) **(measured)**:

| Location (lat 45.5°N) | Scale factor *k* | Area scale |
|---|---|---|
| Central meridian, −123° | 0.999600 | 0.999200 |
| Downtown Portland, −122.65° | 0.999609 | 0.999219 |
| East Portland, −122.47° | 0.999621 | 0.999242 |
| Zone edge, −126° or −120° | 1.000275 | 1.000551 |
| **Web Mercator at Portland** | **1.4267** | **2.0355** |
| Web Mercator at the equator | 1.0000 | — |
| Conus Albers (EPSG:5070) at Portland | ~1.0 (lengths vary by direction) | 1.000000 (equal-area by design) |

How to read this:
- Every distance we measure in UTM 10N is **~0.039% short**: 800 m reads as 799.69 m.
- Every area is **~0.078% small**.
- Both are far below any decision threshold. If we ever needed better, we could divide by *k*, but we don't.

**Why Web Mercator is so bad here.** Normal-aspect Mercator touches the *equator* and stretches by k = 1/cos φ. At 45.5° that's 1.43 linearly and 2.04 in area. It's conformal, so shapes look right, which is exactly why the error is so easy to miss. It was designed for navigation (straight lines are constant compass bearings) and is used by web maps because it tiles the world into neat squares. It wasn't designed for measurement.

### 3.4 Coordinate system: axes, order, units

The final layer answers "which number comes first, and in what unit?"

- EPSG:26910 axes: easting (east), northing (north), metres.
- EPSG:4326 axes per the EPSG registry: **latitude (north), then longitude (east)** **(measured from `axis_info`)**.

That second fact causes the most common CRS bug in the world (§9.1).

---

## 4. Important algorithms and data structures

### 4.1 The PROJ database (`proj.db`)

PROJ ships a **SQLite database** containing the EPSG registry (and ESRI, IGNF and others): every CRS, datum, ellipsoid, projection method, and every known transformation between datums, each with its area of use and accuracy.

- `CRS.from_epsg(26910)` is essentially a SQL lookup plus object construction.
- Inside our PostGIS container, `postgis_full_version()` reports `DATABASE_PATH=/usr/share/proj/proj.db` **(measured)**.

### 4.2 CRS serialisation formats

| Format | Example | Notes |
|---|---|---|
| EPSG code | `EPSG:26910` | A key, not a definition: the meaning lives in the registry |
| **WKT2** (ISO 19162:2019) | the tree in §3 | Full, lossless, the modern standard |
| WKT1 | older `.prj` files in Shapefiles | Ambiguous dialects (OGC vs ESRI), weak on datums |
| **PROJJSON** | JSON version of WKT2 | What GeoParquet stores in its metadata |
| PROJ string | `+proj=utm +zone=10 +ellps=GRS80` | Compact, but **lossy**: can drop datum and axis information. Avoid as a CRS definition. |

### 4.3 Operation selection: how PROJ picks a path from A to B

When you call `Transformer.from_crs(A, B)`, PROJ:

```
1. Resolve A and B to full CRS objects (from proj.db).
2. Decompose each: projected → its base geographic CRS → its datum.
3. Find candidate datum transformations between A's datum and B's datum.
   (Direct ones; or via a hub such as WGS 84; or a null/ballpark fallback.)
4. Filter candidates whose area of use doesn't intersect the area of interest.
5. Drop candidates needing unavailable resources (grid files not installed or downloadable).
6. Sort the rest, mainly by accuracy, then by area-of-use fit.
7. Assemble the best one into a *pipeline* of elementary steps.
```

The pipeline for our main transform **(measured, `Transformer.definition`)**:

```
proj=pipeline
  step proj=unitconvert xy_in=deg xy_out=rad    ← degrees → radians
  step proj=utm zone=10 ellps=GRS80             ← forward Transverse Mercator
```

There's no datum step, because the chosen WGS 84 → NAD83 operation is the null transformation (§3.2).

You can inspect every candidate with `TransformerGroup(A, B)`. For NAD83 → WGS 84 it lists 4 usable operations (two at 4 m, one at 8 m, one "ballpark") and 49 unavailable grid-based ones **(measured)**.

### 4.4 The Transverse Mercator computation

PROJ's default `tmerc`/`utm` implementation (since PROJ 6) is the **Poder/Engsager** algorithm. It's based on **Krüger series** expansions, accurate to millimetres even well outside the zone. The older Evenden/Snyder series (`+approx`) is faster but degrades away from the central meridian. Conceptually the forward step is:

```
input (λ, φ) in radians, ellipsoid (a, f), central meridian λ0, k0
1. Convert geodetic latitude φ to conformal latitude χ   (removes ellipsoidal angle distortion)
2. Compute the spherical TM of (λ - λ0, χ) on a "rectifying" sphere  (Gauss-Schreiber step)
3. Apply a short trigonometric series (Krüger coefficients from f) to reach the ellipsoid
4. Scale by k0·A (A = rectifying radius); add false easting/northing
output (E, N) in metres
```

You don't implement this. The point is to know that a projection is **deterministic maths with known error**, whereas a datum transformation is **empirical with a stated accuracy**. They fail in different ways.

### 4.5 Geodesics: ground truth for distance

The shortest path on an ellipsoid is a **geodesic**. Computing its length (the "inverse geodesic problem") has no closed form.
- PROJ, and therefore `pyproj.Geod`, uses **Karney's algorithm** (2013, from GeographicLib), accurate to about 15 nanometres.
- We used it above as the reference distance (19,441.45 m).
- PostGIS's `geography` type does comparable spheroidal computations.

When would you measure geodesically instead of projecting?
- Data spans a large area (continent-wide), where no single projection is good everywhere.
- You need sub-0.04% accuracy.
- You're doing a few point-to-point distances.

TouchGrass does none of these at scale. Our heavy work is graph edge lengths and polygon areas in one city, where a local projection is simpler, faster, and accurate enough.

### 4.6 Area of use

Every EPSG CRS has a published bounding box where it's valid. For EPSG:26910 it's lon −126.0 to −119.99, lat 30.54 to 81.8 **(measured)**. TouchGrass uses it as a cheap sanity check (§7).

---

## 5. Architecture and component interactions

PROJ is a shared dependency underneath almost every geospatial tool in our stack:

```
                          ┌───────────────────────────────┐
                          │  PROJ (C/C++ library) + proj.db│
                          └──────────┬────────────┬───────┘
              ┌──────────────────────┘            └─────────────────────┐
              ▼                                                          ▼
   pyproj (Python binding)                                  PostGIS (C extension in Postgres)
     ├── touchgrass.core.crs  (our rules)                     ├── spatial_ref_sys table (srid → WKT/PROJ)
     ├── GeoPandas .crs / .to_crs()   [Stage 2+]              └── ST_Transform, ST_SetSRID, geography
     ├── OSMnx project_graph()        [Stage 5]
     └── Shapely (no CRS!)  ← geometry only, coordinates are plain numbers
   MapLibre GL JS (browser)  ← does NOT use PROJ; hard-codes Web Mercator for display
```

Key facts about each component:

- **Shapely has no idea what a CRS is.** It does planar geometry on raw numbers. `Point(-122.6, 45.5).buffer(800)` happily makes a circle with an 800-*degree* radius. The CRS travels **alongside** geometry, in GeoPandas (`GeoSeries.crs`), in PostGIS (the SRID on each geometry), and in GeoParquet (PROJJSON metadata). Never inside it.
- **GeoPandas** stores a `pyproj.CRS` on each GeoSeries. `to_crs()` builds a pyproj `Transformer` and applies it to all coordinates in bulk. Calling `.area`, `.length` or `.buffer` in a geographic CRS triggers a `UserWarning` saying results are likely incorrect. **Treat that warning as an error.**
- **PostGIS** stores an integer **SRID** with every geometry, defined in its `spatial_ref_sys` table. A typed column like `geometry(MultiPolygon, 4326)` rejects other SRIDs on insert, and functions refuse to mix SRIDs (*"Operation on mixed SRID geometries"*). `ST_Transform` calls the container's own PROJ.
- **Our own two PROJ installations agree.** Host pyproj and container PostGIS both report PROJ 9.8.1, and gave identical results for the same point: 525263.6807035374, 5041113.742607573 **(measured in Stage 1)**.
- **MapLibre** works in Web Mercator internally and expects GeoJSON in WGS 84 lon/lat. It never needs our UTM coordinates, so we always transform back to EPSG:4326 for the API.

TouchGrass's CRS data flow (planned for Stages 2–9; only the config part exists today):

```
TIGER (EPSG:4269) ─┐
OSM   (EPSG:4326) ─┼─▶ to_crs(region.analysis_crs = 26910) ──▶ measure: area, buffer, snap, network lengths
City  (various)  ──┘                     │
                                         ├──▶ store results + geometry in EPSG:4326 (PostGIS, GeoJSON)
                                         └──▶ GeoParquet intermediates (CRS recorded in PROJJSON metadata)
API (4326 GeoJSON) ──▶ MapLibre (renders in 3857)
```

---

## 6. How we use it in TouchGrass (D-008)

| Role | CRS | Where it appears |
|---|---|---|
| Storage & exchange | EPSG:4326 | `STORAGE_EPSG` constant in `core/crs.py`; PostGIS columns and GeoJSON (later stages) |
| Measurement | `RegionConfig.analysis_crs`, EPSG:26910 for Portland | `config/regions/portland-or.yaml`, validated on load |
| Display | EPSG:3857 | MapLibre only (Stage 9); never used for measurement |

The rule is **"store in degrees, measure in metres, display in Mercator."** The CRS is a per-region **configuration value**, not a constant. Chicago would use EPSG:26916 with no code change, which `tests/test_config.py::TestSchema::test_second_region_needs_no_code` proves.

---

## 7. Code walkthrough

### 7.1 `load_crs`: [crs.py:55](../../pipeline/src/touchgrass/core/crs.py)

```python
def load_crs(epsg: int) -> CRS:
    try:
        return CRS.from_epsg(epsg)
    except CRSError as exc:
        raise AnalysisCRSError(f"EPSG:{epsg} is not a known CRS ({exc})") from exc
```

- `CRS.from_epsg` queries `proj.db`.
- pyproj's `CRSError` inherits from `RuntimeError`, which Pydantic does **not** treat as a validation failure. It would escape as a crash instead of a clean `analysis_crs: …` message.
- Re-raising as `AnalysisCRSError(ValueError)` makes unknown codes report like every other config error.
- `from exc` keeps the original traceback for debugging.

### 7.2 `assert_analysis_crs`: [crs.py:77](../../pipeline/src/touchgrass/core/crs.py)

The four checks, and the layer of the CRS each one inspects:

```python
crs = load_crs(epsg)                                   # registry: does it exist?
if not crs.is_projected: ...                           # §3 top layer: PROJCRS vs GEOGCRS
units = {axis.unit_name for axis in crs.axis_info}     # §3.4: coordinate system units
if not units <= _METER_UNIT_NAMES: ...                 #   rejects Oregon North (ft), EPSG:2269
if _is_normal_mercator(crs): ...                       # §3.3: the CONVERSION method
```

Why this order: each check assumes the earlier ones passed. You can't ask a geographic CRS for its projection method.

**Why `is_projected` alone isn't enough.** EPSG:3857 *is* projected and *is* in metres **(measured)**. Its flaw is in the projection method, not the units, which is why the fourth check exists.

### 7.3 `_is_normal_mercator`: [crs.py:63](../../pipeline/src/touchgrass/core/crs.py)

```python
op = crs.coordinate_operation          # the CONVERSION node of the WKT tree
method = op.method_name.lower()        # e.g. "popular visualisation pseudo mercator"
return "mercator" in method and "transverse" not in method
```

This encodes the geometry from §3.3:
- **Normal** Mercator touches the equator, so scale explodes as 1/cos φ toward the poles. Rejected.
- **Transverse** Mercator touches a meridian, so it's accurate within a narrow zone. Allowed; it's what UTM uses.

Matching on the method rather than a code list (`{3857, 3395, 900913, …}`) means unfamiliar codes that use the same flawed method are caught too.

**Limitation to know.** The rule doesn't judge *how far* a valid projection is stretched over your region. A State Plane zone in metres for the wrong state would pass these four checks. That's what the next function catches.

### 7.4 `LonLatBounds.contains` and `assert_point_in_area_of_use`: [crs.py:35](../../pipeline/src/touchgrass/core/crs.py), [crs.py:117](../../pipeline/src/touchgrass/core/crs.py)

```python
if self.west <= self.east:
    return self.west <= lon <= self.east
return lon >= self.west or lon <= self.east   # box wraps across ±180°
```

Longitude is circular: a box from 170°E to 170°W (Fiji) has `west > east`. A naive `west <= lon <= east` would claim nothing is inside it. Latitude isn't circular, so it uses a plain range test.

Two real mistakes this caught in Stage 1:
1. A **wrong zone**, e.g. UTM 18N for Portland.
2. A **swapped center**, `[41.88, −87.63]` for Chicago. Both numbers pass range checks, but the point is outside UTM 16N.

**Exercise 2 traced** (`analysis_crs: 32611`, WGS 84 / UTM 11N):
1. `load_crs` ✓
2. projected ✓
3. metres ✓
4. Transverse Mercator, not normal Mercator ✓
5. Then the model validator: is (−122.65, 45.54) inside zone 11's area of use (−120° to −114°)? **No.** It's rejected with "outside the area of use of WGS 84 / UTM zone 11N".

Confirmed by running the CLI on a temporary copy of the config: `<root>: (-122.65, 45.54) is outside the area of use of WGS 84 / UTM zone 11N (lon -120.0..-114.0, lat 0.0..84.0)` **(measured)**.

Had it passed, Portland would sit ~5.6° west of zone 11's central meridian (−117°), with k = 1.001993, a **+0.20%** scale error **(measured)**. That's five times the in-zone error: still small for walking (1.6 m on 800 m), but a sign of misconfiguration that grows the further you go.

### 7.5 The model validator ordering: [models.py:87–105](../../pipeline/src/touchgrass/config/models.py)

```python
@field_validator("analysis_crs")          # runs per field, during parsing
def _metric_projected_crs(cls, epsg): assert_analysis_crs(epsg); return epsg

@model_validator(mode="after")            # runs only if ALL fields are valid
def _center_within_crs_area(self):
    lon, lat = self.center_lonlat
    assert_point_in_area_of_use(load_crs(self.analysis_crs), lon, lat)
```

The area-of-use check needs **two** fields, so it can't be a field validator. `mode="after"` guarantees `analysis_crs` already passed its own checks, so `load_crs` here can't fail on a bad code.

Note that we store the **integer**, not the `CRS` object:
- It keeps the model serialisable (`regions show` emits JSON).
- `cfg.crs()` reconstructs the object on demand.

### 7.6 `make_transformer`: [crs.py:134](../../pipeline/src/touchgrass/core/crs.py)

```python
return Transformer.from_crs(source, target, always_xy=True)
```

This is one line, but it's the most important line in the module. `always_xy=True` tells PROJ to ignore the registry's axis order and always use (x = lon/easting, y = lat/northing), the convention of GeoJSON, Shapely, GeoPandas, PostGIS (`ST_MakePoint(x, y)`) and MapLibre.

What happens without it **(measured)**:
- `Transformer.from_crs(4326, 26910).transform(-122.6765, 45.5231)` reads −122.68 as a latitude, which is impossible, and returns `(inf, inf)`.
- With other inputs it doesn't fail at all, it just returns a wrong point. For example, for a city like Nairobi, near the equator and the prime meridian, a swapped (lat, lon) is still a valid coordinate. **That's the dangerous case.**

`tests/test_crs.py::TestTransformer::test_axis_order_trap_without_always_xy` documents the trap, and the factory makes it impossible to fall into from our code.

---

## 8. Performance and scalability

**(measured)** on this machine:

| Operation | Cost |
|---|---|
| `Transformer.from_crs(4326, 26910)` (database lookup + operation selection) | **~2.0 ms** |
| Bulk transform of 200,000 points in one call | **43 ms** (~4.6 M points/s) |
| Transforming one point at a time in a Python loop (reused transformer) | ~0.5 µs/point |
| Creating a new transformer for each point | **~1.5 ms/point** (~3,000× slower than bulk) |

Lessons for later stages:
1. **Create transformers once and reuse them.** Our `make_transformer` returns a fresh object on each call. That's correct, but in a loop it would be the bottleneck. In Stage 2+, build one per (source, target) pair per pipeline step. GeoPandas `to_crs()` already does this for a whole GeoSeries.
2. **Transform in bulk**, not per feature. The C loop inside PROJ is fast; Python call overhead isn't.
3. **Reproject once per dataset, early**, then do all measurement in the analysis CRS. Don't reproject back and forth.
4. **In PostGIS, never put `ST_Transform` on the column side of a filter**, e.g. `WHERE ST_DWithin(ST_Transform(geom, 26910), …)`. The GiST index is built on `geom` in 4326, so a transformed expression can't use it: Postgres must transform every row, then compare. Options:
   - store a second, pre-projected column with its own index (ARCHITECTURE §4 plans this)
   - transform the *constant* side instead
   - use `geography` with `ST_DWithin` in metres

   We'll measure this with `EXPLAIN ANALYZE` in Stage 4.
5. **Scaling to more regions** costs nothing CRS-wise: each region is reprojected into its own analysis CRS independently. Cross-region comparisons use *results* (percentages, metres), never raw coordinates from different CRSs.

---

## 9. Common mistakes and debugging techniques

### 9.1 Axis order swapped (lat/lon vs lon/lat)
- **Symptoms:** `inf` coordinates; points in Antarctica, the Indian Ocean, or mirrored across the diagonal; geometries "valid" but empty after a clip.
- **Debug:** print the first coordinate. Is |x| ≤ 180 and does it look like a longitude for your region? For the US, longitudes are negative and latitudes positive.
- **Prevent:** always use `always_xy=True` (our factory), and remember that GeoJSON is lon, lat.

### 9.2 Measuring in a geographic CRS
- **Symptoms:** areas like `3.2e-05`; buffers that cover the whole state; GeoPandas `UserWarning: Geometry is in a geographic CRS…`.
- **Debug:** check `gdf.crs.is_geographic`.
- **Prevent:** `assert_analysis_crs` at config load; reproject before any `.area`, `.length`, `.buffer` or `sjoin_nearest(max_distance=…)`.

### 9.3 Measuring in Web Mercator
- **Symptoms:** numbers *plausible but too large*, by 1.43× lengths and 2.04× areas at Portland. This is the hardest bug to spot because nothing looks broken.
- **Debug:** compare one feature against a known value. Forest Park is about 5,200 acres; if you get roughly double (2.04× ≈ 10,600), suspect 3857.
- **Prevent:** the normal-Mercator check.

### 9.4 Assigning a CRS instead of transforming one
- **The mistake:** `gdf.set_crs(26910, allow_override=True)` on data that's actually in 4326. It **relabels** without moving any coordinates. `to_crs()` is the one that **converts**. PostGIS has the same pair: `ST_SetSRID` relabels, `ST_Transform` converts.
- **Symptoms:** data collapses to a tiny cluster near the new CRS's origin. Portland's degrees read as UTM 10N metres become E = −122.7 m, N = 45.5 m. Converted back to lon/lat, that is **(−127.49°, 0.0004°)**: the Pacific Ocean, on the equator, about 500 km west of zone 10's central meridian **(measured)**.
- **Rule:** only use `set_crs`/`ST_SetSRID` when the file has **lost** its CRS metadata and you know the true one.

### 9.5 Missing or wrong `.prj` / CRS metadata
- **Symptoms:** `gdf.crs is None`, or an ESRI WKT1 string that pyproj resolves to something unexpected.
- **Debug:**
  - `gdf.crs.to_epsg()`, which returns `None` if no exact EPSG match
  - `gdf.crs.to_wkt(pretty=True)`
  - `gdf.total_bounds`: are the numbers degrees-sized (±180) or metres-sized (hundreds of thousands)?
- **Prevent:** Stage 2's adapters should assert the expected source CRS for each dataset, not trust it blindly.

### 9.6 Mixing CRSs in one operation
- GeoPandas `sjoin` raises a warning or error on CRS mismatch. PostGIS raises "mixed SRID".
- **Never** silence these. Reproject one side.

### 9.7 Wrong zone or wrong hemisphere
- UTM 10**S** (EPSG:32710) uses a 10,000 km false northing, so northern-hemisphere data gets northings ~15,000 km.
- The area-of-use check catches both a wrong zone and a wrong hemisphere.

### A debugging checklist

```python
print(gdf.crs)                      # what does the data claim?
print(gdf.crs.is_geographic, gdf.crs.axis_info[0].unit_name)
print(gdf.total_bounds)             # do magnitudes match the claim?
print(gdf.geometry.iloc[0].wkt[:80])# eyeball one coordinate
Transformer.from_crs(a, b, always_xy=True).definition   # what pipeline did PROJ choose?
TransformerGroup(a, b).transformers[0].accuracy          # how accurate is it?
```

---

## 10. Alternatives and engineering trade-offs

| Approach | Pros | Cons | Verdict for TouchGrass |
|---|---|---|---|
| **Local UTM zone (chosen)** | Metres; exists everywhere; ≤ 0.04% error within a city; uniform rule for every region | A city straddling a zone edge needs a choice; distortion grows outside the zone | ✅ default |
| State Plane | Even lower distortion locally (designed for < 1:10,000) | Often in **feet**; many zones per state; mixed units across regions | ❌ consistency matters more |
| National equal-area (Conus Albers, EPSG:5070) | Exact areas (areal 1.000000 at Portland, measured); one CRS for all of CONUS | Distances vary by direction; not conformal | Possible for cross-city area statistics; not needed now |
| Custom local projection (e.g. TM on the city centre) | Minimal distortion everywhere in the region | No EPSG code; must ship a WKT/PROJ definition; harder to review | Overkill for city scale |
| Geodesic / `geography` everywhere | No projection error at all | Slower; not all operations exist on the spheroid (e.g. many overlays, graph tools); harder to reason about | Use selectively (e.g. PostGIS `ST_DWithin` on geography) |
| Web Mercator | What the map uses; no reprojection for tiles | 2× area error at Portland | ❌ display only |

Datum-transformation trade-off:
- **Null transform (current):** ~4 m absolute shift, zero setup, deterministic.
- **Grid-based (NADCON5 etc.):** centimetre accuracy, but needs grid files (`PROJ_NETWORK=ON` or `projsync`), which makes results depend on what's installed, a reproducibility risk.

Because our errors are relative rather than absolute, the null transform is the right choice now. If we ever adopted grids, we'd pin and record them in the dataset manifest.

---

## 11. Real-world use cases

- **Urban planning and accessibility** (this project; ParkServe's 10-minute-walk areas): local projected CRS for network lengths and service areas.
- **Surveying and cadastre** (property lines): State Plane plus precise datum realisations like NAD83(2011) with epochs and grid transforms. Centimetres matter in court.
- **Aviation and shipping**: geodesic computations on the ellipsoid; rhumb lines (Mercator straight lines) vs great circles.
- **Web mapping** (Google, OSM, MapLibre): Web Mercator tiles for display, with measurement done server-side in proper CRSs.
- **Climate and remote sensing**: rasters in equal-area or UTM grids. Reprojecting rasters involves *resampling*, which loses information, so pipelines keep native CRSs as long as possible.
- **GPS and autonomous vehicles**: WGS 84 / ITRF with time-dependent transformations, because plates move ~2–5 cm/year and HD maps must stay aligned.
- **The upcoming US datum change**: the National Geodetic Survey is replacing NAD83 with new terrestrial reference frames (NATRF2022), which will shift US coordinates by up to a few metres. Pipelines that record CRS metadata properly will absorb this; pipelines with hard-coded assumptions won't.

---

## Check your understanding

1. Our measured UTM distance was 0.04% *shorter* than the geodesic. Explain why from the UTM parameters, and say where in zone 10 the error would be zero.
2. EPSG:32610 and EPSG:26910 gave identical coordinates for Portland. Why, and in what kind of project would you *not* be allowed to treat them as identical?
3. Shapely buffered a point by 800 and produced a circle covering most of the planet. Which component was missing information, and which component should have had it?
4. Why does `WHERE ST_DWithin(ST_Transform(geom, 26910), :pt, 800)` get slow on a large table, and name two fixes.
5. Explain the difference between `set_crs` and `to_crs` in one sentence each, and describe the symptom of using the wrong one.

**Practical exercise.**
1. Using only pyproj, compute the scale factor `k` at the far east edge of Portland's buffer (about −122.4°, 45.5°) and at the far west (about −122.9°).
2. How much would an 800 m walk measured in each place differ from true ground distance?
3. Is that difference meaningful for our thresholds? Explain your reasoning before computing.

## References

- PROJ documentation: operations, pipelines, `tmerc` algorithm notes. https://proj.org/
- pyproj: `CRS`, `Transformer`, `TransformerGroup`, `Geod`, `Proj.get_factors`. https://pyproj4.github.io/pyproj/stable/
- ISO 19162:2019, WKT representation of CRSs (WKT2).
- EPSG Geodetic Parameter Dataset. https://epsg.org/
- Karney, C. F. F. (2013). *Algorithms for geodesics.* Journal of Geodesy 87, 43–55.
- Snyder, J. P. (1987). *Map Projections: A Working Manual.* USGS Professional Paper 1395.
- PostGIS reference: `ST_Transform`, `ST_SetSRID`, `spatial_ref_sys`, geography type. https://postgis.net/docs/
- NOAA NGS: Modernized National Spatial Reference System. https://geodesy.noaa.gov/datums/newdatums/
