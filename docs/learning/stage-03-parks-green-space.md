# Stage 3: Parks & Green Space Dataset

> Status: **implemented 2026-11-23** with Q1–Q7 as recommended (D-017, D-018). §1–12 are the preparation lesson; §13 is what was built and what the real data showed; §14 explains the largest disagreements with ORCA.
> Facts marked **(verified 2026-11-23)** were measured on the real sources while preparing this lesson.

## 1. Objective and expected outcome

Produce a **defensible, documented set of publicly accessible parks and natural areas** for the study area, plus a quantitative comparison against an authoritative local inventory.

| Output (`data/processed/portland-or/`) | Contents |
|---|---|
| `green_candidates.parquet` | **Every** OSM area that *might* be green space, with its tags, the rule that fired, and `included` / `excluded` + reason |
| `parks.parquet` | The final destinations: one row per green-space unit (overlapping OSM features merged), with `park_id`, `name`, `park_class` (`park` / `natural_area`), `area_m2`, member OSM ids |
| `park_validation.json` (inside `validation.json`) | OSM vs Metro ORCA agreement (area precision/recall), largest discrepancies, inclusion-rule counts |
| `park_discrepancies.parquet` | Geometries of the biggest disagreements, for inspection in QGIS or the Stage 9 map |

The commands stay the same: `touchgrass fetch` gains two sources (an OSM snapshot and ORCA), and `touchgrass process` gains a parks step.

## 2. Why this stage exists

Parks are the **destinations** of every accessibility calculation. Errors here propagate directly to every later number:
- a **missed** park makes nearby residents look underserved;
- an **inaccessible** "park" (private, fenced, golf course, cemetery) makes them look well served.

Unlike census data, there's no official national park dataset that's open, current, and portable across cities. So we must **choose rules, write them down, and measure how well they work** against an independent source. That's the real engineering content of this stage.

## 3. Dependencies

- **Stage 2 (complete):** study boundary + 2 km buffer, the ArcGIS adapter, manifests and caching, the validation framework.
- **Decisions:** D-003 (OSM primary, ORCA for validation), D-008 (CRS rules), D-015 (Stage 2 sources); new decisions in §9.
- **New dependency:** `osmium` (pyosmium 4.x, the official Python bindings for libosmium; arm64 wheels install cleanly). OSMnx is **not** needed yet: it arrives in Stage 5 for the walking network.
- **Disk/time:** a **255 MB** OSM snapshot, downloaded once. The Geofabrik mirror ran at about 0.6 MB/s during research (**6 min 43 s**), so caching matters. Extracting one pass over Oregon took **52 s**.

## 4. What the real data showed (verified 2026-11-23)

### 4.1 Getting OSM data reproducibly
- **The public Overpass API refused us.** It returned *"502 … currently at capacity"* and asks automated users to run their own instance. A pipeline can't depend on that.
- **Geofabrik keeps dated snapshots:**
  - yearly 1 January files back to 2014 (`oregon-140101.osm.pbf` … `oregon-260101`);
  - monthly files for recent months (`oregon-260901`, `261001`, `261101`);
  - daily files for about the last 10 days.

  Each is about 243 MB with a published **MD5**. `oregon-261101.osm.pbf` downloaded and its MD5 matched (`7087d34f…`).
- pyosmium assembled every area (closed ways **and** multipolygon relations) in the buffer in **52 s**.

### 4.2 What is tagged as green space inside Portland's 2 km buffer

| OSM tag | Areas | km² | Notes |
|---|---|---|---|
| `leisure=nature_reserve` | 112 | 59.4 | **Forest Park (4,821 acres)**, Smith & Bybee Wetlands, Powell Butte, Tryon Creek |
| `boundary=protected_area` | 25 | 48.1 | 46.1 km² of it overlaps the nature reserves (the same land tagged twice). **Washington Park (481 acres) is tagged only this way** |
| `leisure=park` | 361 | 14.4 | Mount Tabor (185 acres), Delta Park West, Laurelhurst, … 10 are `access=private` |
| `landuse=forest` | 48 | 9.4 | Mostly private or undeveloped land: not a park by itself |
| `landuse=grass` | 1,743 | 8.1 | Medians, lawns, verges: not parks |
| `leisure=golf_course` | 13 | 8.1 | Exclude (fee, members, not open space for walking) |
| `landuse=cemetery` | 32 | 4.1 | Excluded by default; a debatable choice (see §5.4) |
| `leisure=pitch` / `playground` / `sports_centre` / `track` | 1,678 / 545 / 185 / 97 | 3.9 | Mostly *inside* parks; standalone ones are usually school grounds |
| `leisure=garden` | 284 | 0.37 | Community plots, private gardens, fee sites (Japanese Garden, Lan Su), plus a few public gardens |
| `leisure=dog_park` | 40 | 0.23 | Public dog parks |
| `landuse=recreation_ground` | 53 | 0.24 | Mixed |

**The central lesson:** a naive "`leisure=park` = parks" rule would **drop Forest Park and Washington Park**, Portland's two largest urban parks, together about 5,300 acres. Tag semantics vary by mapper and region, so classification rules must be **explicit, configurable, and validated**, never assumed.

Other signals:
- **Entrances:** 1,304 `entrance=*` nodes in the buffer (1,079 in the city). That's useful for Stage 5's access points.
- **Government Island State Recreation Area** (2,251 acres) is a river island with no walking access. It's included as green space, and Stage 5 must find **no** walking access point rather than invent one. A good test case.

### 4.3 Metro ORCA (the validation source)
- Two layers: **ORCA parcels** (15,093; fields `UNITTYPE`, `STATUS`, `RECREATION`, `OWNER`, `MANAGER`, …) and **ORCA Sites** (6,465 dissolved sites with `TYPE`).
- ORCA deliberately *includes* golf courses, cemeteries, HOA land, and school land, so it must be filtered like OSM:
  - Portland `UNITTYPE`: Park 1,824 · Natural Area 715 · Other 622 · HOA 350 · School Land 327 · Cemetery 78 · Golf 41.
  - `STATUS`: Open 2,718 · Unknown 1,112 · Open_Restricted 79 · Open_Fee 30 · Planned 18.
- Subset for validation, `UNITTYPE IN ('Park','Natural Area') AND STATUS <> 'Planned'`: **4,129 features** in the buffer envelope.
- Licence: the RLIS Open Database EULA (verified 2026-11-22). Attribution statement + link required.

## 5. Concepts

### 5.1 The OpenStreetMap data model

OSM has only three element types plus free-form tags:

```
node       id, lat, lon, tags                         → points (entrances, benches, trees)
way        id, ordered list of node ids, tags         → lines, or polygons if closed (first node = last)
relation   id, ordered list of members (node/way/relation + role), tags
           type=multipolygon: ways with role "outer"/"inner" form a polygon with holes
```

- **Geometry is not stored on ways or relations.** A way only knows node IDs, so its shape must be *assembled* by looking up every node's coordinates.
- **Multipolygons** (needed for big parks with holes or several pieces, like Forest Park) require collecting member ways, joining them end to end into rings, and deciding which rings are outer and inner.
- pyosmium's `with_areas()` does this assembly (libosmium's multipolygon assembler) and reports broken relations instead of guessing.

**Tags are a folksonomy.** `leisure=park` means "a park" in the mapper's judgement. The [OSM wiki](https://wiki.openstreetmap.org/wiki/Tag:leisure%3Dpark) documents conventions, but there's no schema enforcement, which is why §4.2's surprises happen.

### 5.2 PBF and how a streaming reader works

`.osm.pbf` is OSM's binary format: Protocol Buffers in compressed blocks, with coordinates delta-encoded so they compress well. Objects come **sorted by type**: all nodes, then ways, then relations.

That ordering is what makes streaming possible:

```
pass over file:
  nodes     → store each node's (lon, lat) in a location index (array keyed by node id)
  ways      → look up node locations → build way geometry
  relations → (two-pass) libosmium first collects which ways belong to multipolygons,
              then assembles areas once all member ways are known
```

**Memory:** the node-location index for Oregon (tens of millions of nodes) fits comfortably in RAM. For a whole continent you'd use a disk-backed index. **Time:** about 52 s for Oregon, dominated by decompression and area assembly.

### 5.3 Why a dated snapshot, not a live API

| Approach | Reproducible? | Notes |
|---|---|---|
| Live Overpass / OSMnx `features_from_polygon` | No: results change as mappers edit | Public server at capacity; politely discourages automation |
| Overpass with `[date:"…"]` | In principle | Same capacity problem; depends on a third party's history database |
| **Dated Geofabrik extract + checksum (recommended)** | Yes: same bytes, verifiable MD5, cached with our manifest | 255 MB per state; monthly files may be pruned eventually, but 1 January snapshots are kept for years |

This is the Stage 2 pattern again: immutable raw input, checksum, manifest, offline processing.

### 5.4 Classification: deciding what counts as a "park"

"Accessible public green space where a resident can walk in and spend time" is our working definition. Turning it into rules forces explicit choices:

```
INCLUDE  leisure=park | leisure=nature_reserve | boundary=protected_area
         | leisure=dog_park | landuse=recreation_ground | leisure=common | landuse=village_green
EXCLUDE  if access ∈ {private, no}                                  → reason "access"
         golf_course, landuse=cemetery                               → reason "not open space"
         landuse=grass | forest | meadow (unless also INCLUDE-tagged) → reason "land cover, not a park"
         pitch | playground | sports_centre | track (standalone)     → reason "amenity, usually inside a park or school"
         leisure=garden                                              → reason "fee / membership / private" (configurable)
THEN     area < min_area_m2 (400 m²)                                 → reason "too small"
```

Every rule is **config, not code** (D-004). Another city's mappers may use different tags, and another analyst may disagree with excluding cemeteries. Some cities treat cemeteries as walkable green space, and that's a legitimate choice. Every excluded candidate is **kept** in `green_candidates.parquet` with its reason, so the decision is auditable, and a reviewer can see exactly what flipping a rule would change.

### 5.5 Overlap: when one place has several features

Forest Park is one place but three OSM objects (nature reserve, protected area, and smaller parks inside). Counting them separately would:
- double-count park area (area per resident in Stage 7);
- create duplicate "nearest park" destinations (harmless for distance, confusing for reporting).

**Approach: merge candidates whose interiors overlap** into one *green-space unit*:

```
1. spatial index (STRtree) over included candidates
2. for each pair whose bounding boxes intersect: exact test, intersection area > 1 m²?   → edge
3. connected components of that graph = units
4. unit geometry = union of members; name = name of the largest member; class = natural_area
   if any member is a reserve/protected area, else park; keep all member OSM ids
```

This is **graph thinking applied to geometry**, and a preview of Stage 5. Parks that merely **touch** (share an edge) are *not* merged: two adjacent parks remain two destinations.

### 5.6 Measuring agreement between two polygon datasets

Treat ORCA as the reference, and compute areas in UTM (never degrees or Web Mercator):

```
A = union(OSM parks)     B = union(ORCA parks + natural areas)      (both clipped to the buffer)
precision = area(A ∩ B) / area(A)     "how much of what OSM calls park, ORCA agrees with"
recall    = area(A ∩ B) / area(B)     "how much of ORCA's park land OSM captured"
```

- These are the same precision/recall ideas as in classification, applied to *area* instead of counts.
- Area-weighted metrics are dominated by big parks, so we also report **feature-level** agreement: the share of ORCA sites ≥ 1 acre that are ≥ 50% covered by OSM, and the reverse.
- The **largest disagreements** (`A − B`, `B − A`, exploded into pieces, sorted by area) are the actionable output. Each is a tagging difference, a data error in either source, or a genuine definitional difference (e.g., ORCA's "Natural Area" includes land with no public access).
- **We report; we don't "fix" OSM to match ORCA.** Disagreement is information. Uploading corrections to OSM is a separate, manual community activity.

### 5.7 Measurement pitfalls this stage guards against
- **Area in the wrong CRS:** every `area_m2` uses `area_m2()` from Stage 2, which is UTM-checked. (Forest Park would read about 9,800 acres in Web Mercator.)
- **Boundary clipping:** a park straddling the buffer edge is kept *whole*, not clipped. A resident walking to it may enter from inside the buffer, and its true size matters for reporting.
- **Invalid geometry:** OSM multipolygons are sometimes broken. pyosmium reports them, and we count them in validation rather than silently skip them.

## 6. How the concepts relate

```
Geofabrik dated PBF ──(MD5 + manifest)──▶ pyosmium stream: nodes → ways → relations → areas
                                                │ tag filter (config)          │ entrance nodes: Stage 5
                                                ▼
                                   green_candidates (every candidate + decision + reason)
                                                │ include rules, min area
                                                ▼
                              overlap graph → connected components → parks (units)
                                                │
Metro ORCA (ArcGIS, filtered) ──────────────────┤ area precision / recall, feature agreement
                                                ▼
                         validation.json + park_discrepancies.parquet
```

## 7. Fit in the architecture

```
config/regions/portland-or.yaml   + osm: {geofabrik_path, snapshot}
                                  + parks: {include, exclude, min_area_m2, validation_source, sentinel_parks}
pipeline/src/touchgrass/
  sources/osm.py          (NEW)   Geofabrik URL + MD5 verification; pyosmium area extraction
  sources/arcgis.py               (reused for ORCA; no change expected)
  core/parks.py           (NEW)   classify (rules → include/exclude + reason); merge overlaps → units
  core/compare.py         (NEW)   area precision/recall, feature agreement, discrepancy pieces
  pipeline/sources.py, fetch.py, process.py, validate.py   + parks step and checks
```

- **Region-agnostic:** the OSM extract (`north-america/us/oregon`) and snapshot date are config. Rules are config with sensible defaults. ORCA is just "a validation source" of the existing `arcgis_feature_layer` type with a `where` filter.
- **Fetch stays independent of process:** ORCA is fetched with its attribute filter only (not a buffer envelope), so `fetch` never needs processed outputs. Clipping to the buffer happens in `process`.

## 8. Implementation approach

### 8.1 Config additions (illustrative)

```yaml
osm:
  geofabrik_path: north-america/us/oregon
  snapshot: "261101"          # oregon-261101.osm.pbf (1 Nov 2026); bump deliberately

parks:
  include_tags:
    leisure: [park, nature_reserve, dog_park, common]
    boundary: [protected_area]
    landuse: [recreation_ground, village_green]
  exclude_access: [private, "no"]
  min_area_m2: 400
  natural_area_tags: {leisure: [nature_reserve], boundary: [protected_area]}
  validation_source:
    adapter: arcgis_feature_layer
    url: https://services2.arcgis.com/McQ0OlIABe29rJJy/arcgis/rest/services/Outdoor_Recreation_and_Conservation_Areas_ORCA/FeatureServer/5
    where: "UNITTYPE IN ('Park','Natural Area') AND STATUS <> 'Planned'"
    attribution: <RLIS statement + licence link>
  sentinel_parks: [Forest Park, Washington Park, Mount Tabor Park, Laurelhurst Park, Powell Butte Nature Park]
```

`sentinel_parks` is a **region-specific acceptance test in config**: large, well-known parks that must survive the rules. It's how the Forest Park trap would have been caught automatically.

### 8.2 Processing steps

```
1  osm extract   stream PBF once → areas whose tags match ANY candidate key (include ∪ exclude keys)
                 and whose representative point is in the buffer → green_candidates (with all tags)
2  classify      apply rules in order → included / excluded + reason (first matching rule)
3  merge         overlap graph → units → parks.parquet (area in UTM; stable sort by area, then OSM id)
4  orca          read fetched ORCA GeoJSON → clean → clip to buffer
5  compare       precision / recall / feature agreement → discrepancy pieces (≥ 0.5 acre)
6  validate      hard + soft checks below
```

### 8.3 Validation checks

| Check | Type | Expected |
|---|---|---|
| CRS 4326, valid geometries, unique `park_id` | hard | — |
| Every park ≥ `min_area_m2` | hard | — |
| Every `sentinel_parks` name present among included units | hard | all found |
| OSM snapshot MD5 matches Geofabrik's published `.md5` | hard (in fetch) | — |
| Broken OSM multipolygons skipped | soft | count + ids |
| Candidates by rule / reason (counts, km²) | soft | reported |
| Units: count by class, total km², largest 10 | soft | reported |
| OSM vs ORCA: area precision, recall; feature agreement both ways | soft | reported (targets in §10) |
| Top discrepancies (both directions) | soft | listed with names and areas |

### 8.4 Testing strategy
- **Rule tests (pure, no files):** tag dictionaries → expected decision and reason. Covers every include/exclude path, `access=private` beating `leisure=park`, a garden excluded with its reason, and the "Forest Park pattern" (`nature_reserve` + `protected_area`, no `leisure=park`) being included.
- **Merge tests:** hand-made polygons. Overlapping → 1 unit; touching → 2 units; A overlaps B, B overlaps C → 1 unit (transitivity); class becomes `natural_area` if any member is a reserve.
- **Comparison tests:** two squares offset by a known amount → hand-computed precision and recall.
- **OSM extraction test:** a tiny `.osm` XML fixture written by hand (2 nodes … 1 multipolygon relation with a hole), read by the same pyosmium code. That proves multipolygon assembly and tag capture without a 255 MB file.
- **Synthetic end-to-end:** extend Stage 2's synthetic region with an OSM fixture and an ORCA GeoJSON. Assert parks, reasons and the comparison numbers.
- **Data tests (`-m data`):** sentinel parks present; precision and recall reported; all hard checks pass on real Portland data.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | How to get OSM data | **Dated Geofabrik PBF + pyosmium** · Overpass (date-pinned) · live OSMnx queries | **Geofabrik PBF + pyosmium.** Reproducible and checksummed; no dependency on an overloaded public API. (OSMnx stays planned for the Stage 5 network.) |
| Q2 | Snapshot | `oregon-261101` (1 Nov 2026, monthly) · `oregon-260101` (1 Jan 2026, kept long-term but ~11 months old) | **261101.** Cached with checksum in `data/raw`; if Geofabrik prunes it, the manifest still documents exactly what was used |
| Q3 | Inclusion rules | As in §5.4 / §8.1 | **As proposed**, including: cemeteries **excluded**, gardens **excluded** (fee/membership/private), standalone playgrounds/pitches **excluded** |
| Q4 | Overlapping features | **Merge interiors that overlap** (touching parks stay separate) · keep every OSM feature separate · merge everything that touches | **Merge overlaps** |
| Q5 | Minimum area | 400 m² · 0 · 1,000 m² | **400 m²** (about 20 × 20 m: keeps pocket parks, drops slivers) |
| Q6 | Sentinel parks | The five in §8.1, or your own list | **The five listed** |
| Q7 | Housekeeping: project Python | Keep the Anaconda-based interpreter (isolated, but not reproducible) · switch the venv to a **uv-managed CPython 3.12** | **Switch** (one command; nothing else changes) |

## 10. Acceptance criteria

- [ ] `touchgrass fetch` downloads the dated OSM snapshot (MD5 verified against Geofabrik) and the filtered ORCA layer, both with manifests; a second run downloads nothing.
- [ ] `touchgrass process` writes `green_candidates.parquet`, `parks.parquet`, `park_discrepancies.parquet`; all hard checks pass, including **all sentinel parks present**.
- [ ] Every excluded candidate has a reason; rule counts are reported.
- [ ] OSM vs ORCA area precision and recall are reported, and the **top 10 discrepancies in each direction are reviewed and explained** in the lesson (no target number is imposed in advance; we decide whether the agreement is acceptable after seeing why the sources differ).
- [ ] Re-running `process` produces identical outputs.
- [ ] ruff, mypy --strict, and pytest pass; the hardcoding guard still passes (rules, URLs, and park names live in config).
- [ ] Docs: D-017 (OSM acquisition) and D-018 (park rules) recorded; ARCHITECTURE §9 OSM row updated; lesson "what was built" section.

## 11. Proposed implementation checklist

- [ ] (Q7) Switch the venv to uv-managed CPython 3.12; re-lock and re-run all tests
- [ ] Add `osmium` dependency under the date cutoff
- [ ] Config schema: `osm` (path, snapshot), `parks` (include/exclude tags, access, min area, natural-area tags, validation source, sentinel parks); update `portland-or.yaml`
- [ ] `sources/osm.py`: Geofabrik URL, download + MD5 verification (manifest records both), pyosmium area extraction with tag and buffer filter, broken-multipolygon count
- [ ] `core/parks.py`: rule engine (include / exclude + first-matching reason), overlap graph → units
- [ ] `core/compare.py`: area precision/recall, feature agreement, discrepancy pieces
- [ ] Wire into `pipeline/sources.py`, `fetch.py`, `process.py`, `validate.py`
- [ ] Tests: rules, merge, compare, OSM XML fixture, synthetic end-to-end, data tests
- [ ] Run on real data; review the top discrepancies and write the explanations
- [ ] Docs: decisions, ARCHITECTURE, implementation log, status, lesson §"what was built"

## 12. Questions to think about before we start
1. Why does OSM store a way's *node IDs* rather than its coordinates, and what does that cost a reader?
2. If ORCA says a parcel is a "Natural Area" but OSM has nothing there, list three different explanations. Which would you check first, and how?
3. Should a cemetery count as accessible green space? Argue both sides; then say how our design lets either answer be tested.
4. Why could merging *touching* parks (not just overlapping ones) distort a "number of parks per neighborhood" metric?

## References
- OSM data model and tags: https://wiki.openstreetmap.org/wiki/Elements · https://wiki.openstreetmap.org/wiki/Tag:leisure%3Dpark · https://wiki.openstreetmap.org/wiki/Tag:boundary%3Dprotected_area
- OSM multipolygons: https://wiki.openstreetmap.org/wiki/Relation:multipolygon
- PBF format: https://wiki.openstreetmap.org/wiki/PBF_Format
- pyosmium documentation: https://docs.osmcode.org/pyosmium/latest/
- Geofabrik downloads (Oregon): https://download.geofabrik.de/north-america/us/oregon.html
- Overpass API usage policy: https://wiki.openstreetmap.org/wiki/Overpass_API
- Metro ORCA (RLIS Discovery): https://rlisdiscovery.oregonmetro.gov/
- OSM licence (ODbL) and attribution: https://www.openstreetmap.org/copyright

## 13. What was built (post-implementation)

### Results on real Portland data

| Measure | Result |
|---|---|
| OSM snapshot | `oregon-261101.osm.pbf`, 255,114,996 bytes, **MD5 verified** against Geofabrik |
| Candidate areas in the buffer | 5,300 (from 52,836 matching areas in all of Oregon) |
| Included | **531 candidates, 76.2 km²** |
| Excluded by reason | land cover 1,891 (19.5 km²) · amenity 2,502 (3.9 km²) · not open space 45 (12.2 km²) · garden 284 (0.37 km²) · access 26 (0.14 km²) · too small 21 |
| Park units after merging overlaps | **477** (363 parks, 114 natural areas), 73.6 km²; 41 units merged several features; 2 units had golf/zoo land cut out (1.51 km²), 1 dropped |
| Sentinel parks | **all 5 found** (Forest, Washington, Mount Tabor, Laurelhurst, Powell Butte) |
| Broken OSM relations | 37 candidate relations in the whole Oregon extract couldn't be assembled; 3 geometry errors |
| ORCA features in the buffer | 3,185 (of 7,520 fetched metro-wide) |
| **Area precision / recall** (ORCA as reference) | **0.895 / 0.931** after the subtraction rule (was 0.877 / 0.931); OSM 72.8 km², ORCA 70.0 km², overlap 65.2 km² |
| Feature coverage | 80% of OSM units ≥ 1 acre are ≥ 50% covered by ORCA; 87% of ORCA features ≥ 1 acre are ≥ 50% covered by OSM |
| Runtime / idempotency | `process` 48 s total (about 40 s is the OSM pass); second run **byte-identical** for all 8 files |

### Differences from the plan
- **The venv now runs on uv-managed CPython 3.12.12** (Q7). `[tool.uv] python-preference = "only-managed"` keeps it that way.
- **`osm` and `parks` are optional config sections.** Regions without parks still process, which keeps the Stage 2 synthetic test region valid.
- **ORCA is fetched metro-wide** with only its attribute filter (7,520 features, 47 MB) and clipped in `process`, so `fetch` never depends on processed outputs.
- **Test fixtures are real `.osm.pbf` files** written with pyosmium's `SimpleWriter`, including a multipolygon with a hole and a deliberately broken relation. Two fixture mistakes of mine were caught by the code behaving correctly: a "hole" placed outside its outer ring (libosmium assigns rings by geometry, not role labels) and a feature just outside the buffer.

### Known limitations
- ~~Excluded land inside included parks is still counted as park.~~ **Resolved** by the D-018 amendment (§15).
- **Linear trail corridors** (Springwater, Marine Drive) are areas in ORCA but lines in OSM. They'll contribute through the walking network in Stage 5, not as destinations.
- Broken relations are counted for the whole state, not located within the buffer.
- Names on units come from the largest *named* member, which may not be the name people use for the whole merged unit.

## 14. The 20 largest disagreements, explained

Method: for each piece, a live lookup of what **all** ORCA categories say at that point, plus every OSM candidate there (including excluded ones and their reasons).

### OSM says park, our ORCA subset does not

| Piece | Acres | What's there | Category |
|---|---|---|---|
| Delta Park West | 628 | ORCA: **Heron Lakes Golf Course** (Golf, Open_Fee). OSM: the park polygon encloses three golf-course polygons we excluded | **A: excluded facility inside a park polygon** |
| Smith & Bybee Wetlands | 231 | ORCA: unnamed **"Other"** parcel (Bureau of Environmental Services) | B: ORCA category differs |
| Buttes Natural Area | 119 | ORCA: nothing at this point | C: in OSM, absent from ORCA (needs a manual check) |
| Washington Park | 65 | ORCA: **Oregon Zoo** ("Other", Open_Fee). OSM: protected-area boundary includes the zoo | **A** |
| Mitchell Creek Natural Area | 58 | ORCA: nothing | C |
| Catkin Marsh Natural Area | 54 | ORCA: nothing | C |
| Lower Powell Butte Floodplain | 54 | ORCA: nothing | C |
| Grant Butte Wetlands | 34 | ORCA: same name, category **"Other"** | B |
| Southwest Community Park | 34 | ORCA: same name, status **"Planned"** (we filtered out Planned) | B (currency: built since, or mapped early in OSM) |
| Forest Heights Open Space | 33 | ORCA: **Home Owners Association** land | B; possibly OSM over-inclusion (HOA land without `access=private`) |

### ORCA says park or natural area, OSM does not (by our rules)

| Piece | Acres | What's there | Category |
|---|---|---|---|
| Glendoveer Golf Course and Nature Trail | 231 | ORCA codes the whole site as Park; OSM has the golf course, which we exclude | D: our definition (the perimeter trail is public; the course isn't) |
| Jenne Butte | 119 | ORCA Natural Area (Gresham, status Unknown); OSM nothing | E: OSM mapping gap or not publicly open |
| Springwater Trail | 78 | ORCA Park (trail corridor); OSM maps it as a path | F: trail corridors are lines in OSM |
| Zenger Farm | 46 | ORCA Natural Area (urban farm, BES); OSM no candidate | D: a farm, not a park by our definition |
| Lower Abbey Creek NA (2 pieces) | 82 | ORCA Natural Area (Metro, status Unknown); OSM nothing | E |
| Unnamed (ODOT) | 42 | ORCA Natural Area owned by the **Department of Transportation** | E: highway right-of-way vegetation, not public green space |
| Unnamed (City of Portland) | 39 | ORCA Natural Area, status Unknown | E |
| Kelly Butte Reservoir | 28 | ORCA Natural Area owned by the **Water Bureau** | E: restricted infrastructure; OSM correctly has no park |
| Marine Drive Trail | 22 | ORCA Park (Port of Portland trail corridor); OSM `landuse=grass` (excluded as land cover) | F |

### What the categories mean
- **A (actionable, ~370 acres):** parks drawn around fee-charging or restricted facilities. Our rules exclude golf courses *as features* but not *as holes in parks*. Proposed fix: subtract `not_open_space` areas (golf, cemetery; optionally `tourism=zoo`) from park units. It matters most for Stage 5, where a golf-course fence must not count as a park entrance.
- **B (~330 acres):** ORCA's own categories ("Other", "Planned", HOA) say "not a public park" where OSM says park. Mostly ORCA being stricter or more current about ownership. Forest Heights is the one where OSM may be wrong.
- **C (~285 acres):** natural areas in OSM that ORCA doesn't list at all. Plausibly newer restoration sites or ORCA gaps; can't be settled from data alone.
- **D, F:** genuine definitional or representational differences. Neither source is wrong.
- **E:** ORCA "Natural Area" includes land the public can't use (transportation, reservoirs, closed sites), which is why recall can't reach 100% under our definition.

**Conclusion:** with every large disagreement explained and the one rule gap (A) fixed (§15), the OSM-based dataset is fit for the MVP.

## 15. Follow-up: cutting excluded facilities out of parks (D-018 amendment)

**Rule.** `tourism=zoo` joins golf courses and cemeteries under `not_open_space`. `subtract_reasons: [not_open_space]` cuts those areas out of any merged unit that encloses them, then re-measures the unit and drops it if it falls below 400 m².

**Why after merging, not before:** subtraction is a set difference, (A ∪ B) − H. Doing it once on the merged unit is equivalent to doing it per member, simpler, and cheaper.

| | Before | After |
|---|---|---|
| Delta Park West | 628 acres | **319 acres** (307.8 acres of golf removed) |
| Washington Park | 489 acres | **424 acres** (65.1 acres of zoo removed) |
| Units | 478 | 477 (dropped: a 0.16-acre recreation ground inside Waverly Country Club, a private golf club) |
| Precision vs ORCA | 0.877 | **0.895** |
| Recall vs ORCA | 0.931 | 0.931 |

**Why the recall didn't move is itself evidence:** the land we removed was not in ORCA's Park/Natural Area subset either (ORCA codes it Golf Course and Other/Open_Fee). Both sources now agree it isn't public park.

**Not subtracted, on purpose:** gardens. Several sit inside parks and are free and public (International Rose Test Garden, Peninsula Park Rose Garden), so cutting them out would be wrong.

**Tests added:** cut, drop and untouched cases; an unknown reason is rejected by config validation; an end-to-end zoo-inside-reserve case that becomes a polygon hole. 117 tests pass; outputs remain byte-identical on rerun.
