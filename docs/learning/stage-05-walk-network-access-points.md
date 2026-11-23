# Stage 5: Pedestrian Network & Park Access Points

> Status: **implemented 2026-11-23, revised after your field check (§16), awaiting your review.** §13 is what was built, §14 the first results on Portland, §15 answers to §12, §16 the field check and the fixes it led to. Where implementation changed a number or a method from the probes, it is marked **(updated)**.
> Facts marked **(measured 2026-11-23)** come from throwaway probe scripts run on the real dated extract (`oregon-261101.osm.pbf`) in a separate scratch environment. No project code was changed.

## 1. Objective and expected outcome

Turn the dated OSM extract into an **analysis-ready walking graph**, then attach both ends of every future trip to it:
- **Origins:** each populated census block's representative point, snapped to the network.
- **Destinations:** **park access points**, the places where the network meets a park.

At the end of the stage:
- `touchgrass network portland-or` builds the graph offline from the cached extract. It writes nodes and edges, block origins, park access points, and a diagnostics report.
- Every block and every park has a recorded outcome: snapped, flagged, or "no walk access", each with a reason.
- Snapping quality and graph connectivity are measured and reported, not assumed.

No distances to parks are computed yet. That's Stage 6, which runs one multi-source Dijkstra over this graph (D-005).

## 2. Why this stage exists

A walking distance is only as good as the graph it's measured on and the two points it connects. Three things can silently break Stage 6:
1. **Missing walkable links:** a trail left out of the network makes its neighborhood look farther from parks than it is.
2. **Disconnected pieces:** an island of network with no link to the rest gives "unreachable", or worse, a snap to the wrong piece.
3. **Bad endpoints:** a park's centroid is wrong for a 5,000-acre park. A house snapped to a corner 100 m away adds error.

The probes below found real examples of **all three** in Portland's data.

## 3. Dependencies

- **Stages 2–4 (complete):** `analysis_buffer` (city + 2 km), populated blocks with representative points, 473 park units, PostGIS schema.
- **Data:** no new download. The graph comes from the **same dated extract** Stage 3 used (D-017), so parks and network describe the same moment of OSM.
- **New Python dependencies** (resolve under the D-014 cutoff, checked 2026-11-23): `osmnx` 2.1.1, `networkx` 3.7, `scipy` 1.18.1 (OSMnx uses its KD-tree for nearest-node search).
- **Decisions:** D-005 (multi-source Dijkstra, undirected walk graph, 500 m snap flag), D-006 (access points), D-008 (analysis CRS), D-017 (dated extracts), plus the new questions in §9.

## 4. What the probes found (measured 2026-11-23)

| Question | Answer |
|---|---|
| Can OSMnx read our extract? | Not a `.pbf`. `ox.graph_from_xml()` reads `.osm` **XML** (`graph_from_polygon` would download from Overpass, which D-017 rejected). Route: pyosmium filters the PBF, writes `.osm` XML, then `graph_from_xml`. |
| Cost | Scanning the 200 MB PBF takes 28 s, the XML is about 90 MB, `graph_from_xml` takes 40 s. About 2 min end to end (native Python; only the database runs under emulation). |
| Raw → simplified | 123,690 walkable ways and 617,988 nodes in the buffer's bounding box → 157,068 graph nodes after OSMnx simplification → **107,113 nodes / 8,375 km** after clipping to the buffer polygon |
| Ways the OSMnx `walk` filter drops in the box | `access=private` 12,782 · `sidewalk=separate` 3,930 · `highway=cycleway` 1,241 · motorway / motorway_link 1,598 · `foot=no` 658 · `area=yes` 193 · proposed / construction 296 |
| **Foot-legal paths dropped** | **119 km of `highway=cycleway` with `foot=designated` or `yes`**, including the **I-205 Multi-Use Path (13.1 km), Springwater Corridor, Marine Drive Trail**: major East Portland walking routes |
| Connectivity, OSMnx filter as is | 564 pieces; the largest holds 97.4% of nodes. **Hayden Island (623 nodes) is cut off.** 6,891 residents' nearest street is on a cut-off piece |
| Connectivity, foot-legal cycleways allowed back in | 490 pieces; the largest holds **98.4%**. Hayden Island reconnects (26 of 27 blocks). 4,244 residents still off the largest piece (38 blocks, to explain in implementation) |
| Snap to nearest **node** | median 51 m, p95 105 m; **93.1% of populated blocks within 100 m** (below the plan's tentative 95%) |
| Snap to nearest **edge** | median 33 m, p95 49 m; **99.45% of residents within 100 m** when snapping to any piece. **(updated)** Snapped to the main component only, as built: **99.35%**. Snapping to nodes overstates by a median of 18 m |
| Parks with a street or path within 15 m of their edge | **463 of 473**. The 10 without are mostly **river islands** (Ross Island 13.7 ha, Tri-Club Island, Moore and Wright Islands) plus a few small ravine natural areas |
| OSM entrance / gate nodes within 15 m of a park edge | 208, at 87 parks. 141 are gates; **19 are `access=private` or `no`** |

Interpretations:
- **The cycleway exclusion is an equity bias, not a technicality.** It removes long trails in exactly the part of the city (East Portland) that Stage 7 will examine most closely. PROJECT_PLAN already warned that OSM "walk" includes roads without sidewalks. This is the opposite error: it excludes paths that are explicitly for walking.
- **"Keep the largest component" is only safe after fixing the filter**, and its leftovers must be reported with their population, not dropped silently.
- **D-006's fallback (c) would be wrong here.** Snapping sampled edge points of Ross Island to the nearest street would "connect" it across the Willamette. An island with no path is genuinely not walkable.

## 5. Concepts

### 5.1 Graphs for networks
A **graph** is a set of **nodes** (intersections, dead ends) and **edges** (street or path segments between them), each edge with a **weight** (here, length in metres). NetworkX stores it as an **adjacency list**: a dict from each node to its neighbors and the edge data.
- OSMnx returns a `MultiDiGraph`: *directed* (one-way streets have one edge) and *multi* (two different segments can join the same two nodes, e.g. a street and a parallel path).
- **For walking, direction doesn't matter.** We build with `bidirectional=True` (every way walkable both ways) and convert to an undirected `MultiGraph`. Keeping *multi* matters: two parallel edges of different lengths must both survive, so the shortest one is found.

### 5.2 From OSM ways to a routable graph: simplification
An OSM way is a list of node IDs. A curvy street has dozens of shape nodes that aren't intersections. **Topology simplification** removes nodes that only shape a line (degree 2, no change of attributes) and merges the segments into one edge whose `geometry` keeps the curve. Here 617,988 OSM nodes became 157,068 graph nodes: about 4× fewer nodes, the same lengths. **Edge lengths come from the full geometry**, not from straight lines between kept nodes.

Consequence for this stage: after simplification, **most points where a path crosses a park boundary aren't nodes**. They're somewhere along an edge. That drives the design in §5.6.

### 5.3 What "walkable" means in OSM, and the OSMnx `walk` filter
OSMnx's `walk` filter (verified from OSMnx 2.1.1) keeps any `highway=*` way except:

```
area=yes · access=private · foot=no · service=private
highway ~ abandoned|bus_guideway|construction|cycleway|motor|no|planned|platform|proposed|
          raceway|razed|rest_area|services
sidewalk / sidewalk:both / :left / :right = separate
```

Read each rule as a modelling assumption:
- **`motor`** drops motorways (correct: no pedestrians).
- **`cycleway` drops all cycle paths**, assuming they're bike-only. In Portland many are shared-use (`foot=designated`). That's the 119 km above.
- **`sidewalk=separate`** drops a *street* whose sidewalks are drawn as separate `footway=sidewalk` ways, to avoid counting the same corridor twice. This is right **only if** those sidewalks connect at crossings. A gap in the separately mapped sidewalks then becomes a gap in our network.
- **Residential streets with no sidewalks are kept.** You can walk on them, but the experience differs. That's a known limitation (PROJECT_PLAN), not something this stage fixes.

**The filter is region configuration**, like the park rules: different cities tag differently.

### 5.4 Connected components
A **connected component** is a maximal set of nodes that can all reach each other. Dijkstra from parks can never reach a node in a different component, so a block snapped onto a stray fragment would come out "unreachable" (or get a nonsense distance if snapped across a gap).
- **Why fragments exist:** real separation (islands, gated complexes), clipping at the buffer edge, mapping gaps (a missing crossing), and our own filter choices (dropping the only link).
- **Policy (recommended, §9 Q3):** keep the largest component, and for every other component report its size, location and the population that would snap to it. Snap every block to the **largest** component and record how far that is.

### 5.5 Nearest-neighbor search: KD-trees and R-trees
Snapping 10,372 origins by checking all 107k nodes would be 1.1 billion distance computations.
- A **KD-tree** splits *points* alternately by x and y into a balanced tree. A nearest-point query descends to the closest leaf, then backtracks only into cells that could hold something closer. That's O(log n) per query. OSMnx's `nearest_nodes` uses SciPy's `cKDTree` for projected graphs: **0.4 s for all blocks** (measured).
- **Edges are lines, not points**, so `nearest_edges` uses an **R-tree** (Shapely STRtree, the same structure as Stage 3 and as PostGIS GiST in Stage 4): filter by bounding boxes, refine with exact point-to-line distance. **2.6 s** (measured).
- **Both need a projected CRS** (EPSG:26910). A KD-tree on lon/lat would treat a degree of longitude as equal to a degree of latitude, but at 45.5° N a degree of longitude is only about 70% as long.

### 5.6 Snapping origins *onto edges*, and the virtual source
**Snap to a node:** distance = connector to the nearest intersection + network distance from there. The connector can be 100 m when the house is mid-block.

**Snap to an edge (recommended):** find the nearest point *P* on the nearest edge (u, v), then record:

```
connector_m  = |origin − P|                   (straight walk to the street)
offset_u_m   = distance along the edge from P to u
offset_v_m   = edge length − offset_u_m
d(origin)    = connector_m + min(offset_u_m + D(u), offset_v_m + D(v))      ← Stage 6
```

The same idea works for **park access points**. A path crossing a park boundary mid-edge becomes a point *A* on edge (u, v), with offsets to u and v.

**How Stage 6 seeds Dijkstra with points that aren't nodes:** add one **virtual source** node *S*, and connect *S* to u and v of every access edge with weights `offset_u` and `offset_v`. One ordinary Dijkstra from *S* then gives every node its distance to the *nearest* access point. That's exactly multi-source Dijkstra with non-zero starting distances. Stage 5 only has to store the offsets.

### 5.7 Park access points (D-006, refined by the probes)
For each park unit, the candidates are:

| Method | Rule | Note |
|---|---|---|
| `entrance` | OSM `entrance=*` or `barrier=gate` node within 15 m of the park edge, **not** `access=private` / `no` | 208 near parks, 19 are private |
| `edge_contact` | Every network edge that comes within 15 m of the park edge. The access point is the edge's closest point to the park | Covers paths that enter the park and perimeter sidewalks |
| ~~`sampled`~~ | ~~Boundary points every 50 m snapped to the network~~ | **Proposed for removal** (Q5): it bridges water. Parks with no contact are reported as **no walk access** |

Known limitations, documented rather than solved: fences (a perimeter street next to a fenced park counts as access), and private gates not tagged as such.

### 5.8 Edge-of-buffer effects
The graph is clipped to the 2 km buffer so that a resident near the city limit can reach a park just outside it (D-001). `truncate_by_edge=True` keeps edges that cross the buffer line, so there's no artificial gap at the edge. Paths that leave the buffer and come back are cut, a small error at the very edge that's documented.

## 6. How the concepts relate

```
dated PBF ──pyosmium + walk filter (config)──▶ .osm XML ──OSMnx──▶ simplified MultiDiGraph
      │                                                                │ clip to buffer, project 26910,
      │                                                                │ undirected, components report
      │                                                                ▼
      │                                                     largest component (nodes, edges)
      │                                     KD-tree / R-tree │                        │ R-tree
      ▼                                                      ▼                        ▼
 entrance/gate nodes ─────────────────────▶ park access points        block origins on edges
                                            (point on edge + offsets)  (connector + offsets)
                                                       │                        │
                                                       └───── Stage 6: virtual-source Dijkstra ─────┘
```

## 7. Fit in the architecture

```
config/regions/<region>.yaml     + network: walk filter (exclusions, foot-legal overrides),
                                   access tolerance_m, entrance tags
pipeline/src/touchgrass/
  sources/osm.py                 + extract_walk_ways(): PBF → filtered .osm XML (+ stats)
  core/network.py      (NEW)     build graph (OSMnx), clip, project, undirect, components
  core/snap.py         (NEW)     snap points to edges: connector + offsets (STRtree)
  core/access.py       (NEW)     park access points (entrance, edge_contact) + reasons
  pipeline/network.py  (NEW)     orchestrates; writes outputs + diagnostics; validation checks
  db/migrations/0005_network.sql (NEW) block_origins, park_access_points
  cli.py                         + `touchgrass network <region>`; `load` also loads the new tables
data/processed/<region>/
  network_nodes.parquet, network_edges.parquet     (EPSG:4326 storage, D-008)
  block_origins.parquet, park_access_points.parquet, network_report.json
```

## 8. Implementation approach

### 8.1 Pipeline
1. **Filter** (pyosmium, one pass): keep `highway=*` ways passing the configured walk filter and touching the buffer's bounding box. Count every exclusion by rule. Collect `entrance` / `gate` nodes.
2. **Write `.osm` XML** to a temporary file next to the outputs (deleted afterwards). **Build** with `ox.graph_from_xml(bidirectional=True, simplify=True, retain_all=True)`.
3. **Clip** to the buffer polygon (`truncate_by_edge=True`). **Components report:** size, centroid, and, after snapping, population per component. Keep the largest.
4. **Project** to the analysis CRS (asserted with `assert_analysis_crs`). **Undirect.** Check: every edge length > 0, lengths ≈ geometry length (OSMnx lengths are geodesic; UTM lengths should agree within 0.1%).
5. **Origins:** populated blocks → nearest edge on the main component → connector + offsets. Flag `connector_m > max_snap_m` (500).
6. **Access points:** entrance and edge-contact rules per park. Parks with none are recorded with reason `no_network_contact`.
7. **Write outputs** (nodes and edges with stable IDs, sorted) and `network_report.json`, then run hard and soft **validation checks**.

### 8.2 Validation checks (planned)
| Check | Kind | Rule |
|---|---|---|
| graph.main_component_share | hard | ≥ 95% of nodes (measured 98.4%) |
| origins.population_within_100m | hard | ≥ 95% of residents (measured 99.45%), threshold now confirmed with data |
| origins.all_populated_blocks_snapped | hard | every block with pop > 0 has an origin row |
| access.every_park_accounted | hard | every park has ≥ 1 access point **or** a `no_network_contact` record |
| access.sentinels_reachable | hard | sentinel parks (Forest, Washington, Mount Tabor, …) have access points |
| graph.stranded_population | soft | population whose nearest edge is off the main component, listed by component |
| graph.excluded_ways_by_rule | soft | counts and km per filter rule |
| origins.snap_distribution | soft | median, p95, max; flagged blocks |

### 8.3 Spot-check output
`network_review.geojson`: flagged blocks, parks without access, access points of the sentinel parks, and stranded components. Open it in any GeoJSON viewer to check 5 parks visually against OSM (the PROJECT_PLAN requirement). There's no map library dependency; the web map comes in Stage 9.

### 8.4 Testing strategy
- **Synthetic OSM fixtures** (the Stage 3 `OsmFixture`, extended with open ways): a small street grid, a park with an internal path, an "island" park, a shared-use cycleway, a street with `sidewalk=separate`, a private gate.
- **Filter rules:** each OSMnx exclusion, the foot-legal cycleway override, and exclusion counts.
- **Graph:** simplification keeps lengths; clipping; undirected multi-edges survive; components and the stranded-population report (the island case).
- **Snapping:** a hand-computed point beside a known edge gives the exact connector and offsets; the KD-tree/R-tree choice gives the same answer as brute force on a small grid.
- **Access points:** a path entering the park gives `edge_contact`; a private gate is ignored; the island park gives `no_network_contact`.
- **End to end** on the synthetic region; **real-data** checks (`-m data`) for the validation thresholds and sentinel parks.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | Network source | **pyosmium filter → `.osm` XML → `ox.graph_from_xml`** (offline, same snapshot as parks) · `graph_from_polygon` via Overpass (not reproducible; rejected in D-017) · build the graph ourselves in NetworkX (reimplements simplification) | **XML route** |
| Q2 | Walk filter | **OSMnx `walk` rules + re-admit `highway=cycleway` when `foot` ∈ {designated, yes, permissive}, all in region config** · OSMnx `walk` as is · a custom filter written from scratch | **OSMnx + foot-legal cycleways**: fixes 119 km of East Portland trails and reconnects Hayden Island; everything else stays OSMnx-compatible and explainable |
| Q3 | Disconnected pieces | **Keep the largest component; report every other piece with its population; snap all blocks to the main component** · keep all components (stray fragments give "unreachable") · drop small pieces silently | **Largest + report** |
| Q4 | Origin snapping | **Nearest edge with connector + offsets** · nearest node (simpler; +18 m median; 93% within 100 m) | **Nearest edge** |
| Q5 | Access-point fallback (D-006 c) | **Remove it; parks with no network within 15 m are recorded as `no_network_contact`** · keep sampled fallback (bridges rivers: Ross Island) · fallback only within a short distance | **Remove** (amend D-006) |
| Q6 | Storage | **GeoParquet nodes + edges + origins + access points; load origins and access points into PostGIS (migration 0005)** · GraphML (OSMnx native; large XML, slow) · also load the full network into PostGIS | **GeoParquet + two new tables.** The network goes into PostGIS later only if the map needs it |
| Q7 | Command | **New `touchgrass network <region>`** (≈2 min; separate from `process`) · fold into `process` | **Separate command**, so park-rule iterations stay fast |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)

- [ ] `touchgrass network portland-or` runs offline from the cached extract, and reruns give byte-identical outputs.
- [ ] Connectivity report: component count, main-component share (≥ 95% of nodes), and stranded population listed by component, with Hayden Island connected.
- [ ] Exclusion report: ways and km per walk-filter rule, including the foot-legal cycleway override.
- [ ] ≥ 95% of residents' origins within 100 m of the network. Every block over 500 m is listed.
- [ ] Every park has access points or a `no_network_contact` record. Sentinel parks have access points. Private gates are never used.
- [ ] `network_review.geojson` written. Five parks spot-checked against OSM and noted in the lesson.
- [ ] ruff, mypy --strict and pytest pass; the hardcoding guard passes; migration 0005 applies, and `load` loads the new tables.
- [ ] Docs: D-020 (Q1–Q7), D-006 amendment, ARCHITECTURE §4–5, lesson "what was built".

## 11. Proposed implementation checklist

- [ ] Add `osmnx`, `networkx`, `scipy` under the date cutoff
- [ ] Config: `network` section (walk filter, foot-legal overrides, `access_tolerance_m`, entrance tags) + model validation
- [ ] `sources/osm.py`: `extract_walk_ways` (filter + XML + stats; entrance nodes)
- [ ] `core/network.py`: build, clip, project, undirect, components; lengths check
- [ ] `core/snap.py`: point-to-edge snapping with offsets
- [ ] `core/access.py`: entrance + edge-contact access points; `no_network_contact`
- [ ] `pipeline/network.py` + validation checks + `network_review.geojson`; CLI `touchgrass network`
- [ ] Migration `0005_network.sql` (block_origins, park_access_points) + loader update
- [ ] Tests (synthetic fixtures, rules, snapping, access, end to end, real data)
- [ ] Run on Portland; spot-check 5 parks; explain stranded components; record results in this lesson
- [ ] Docs: D-020, D-006 amendment, ARCHITECTURE, log, status

## 12. Questions to think about before we start
1. Why does a walking graph need to be a *multi*-graph even after making it undirected? Give a Portland-style example.
2. If Hayden Island's only connection were dropped, Dijkstra would report its parks and residents as unreachable from each other's side. Which is worse for an access study: a false "unreachable", or a false connection across water? Why?
3. Snapping to the nearest node overstated distances by a median of 18 m. Why would this error be larger in neighborhoods with long blocks (e.g. East Portland's superblocks) than downtown's 200-ft blocks?
4. How does a virtual source node turn "distance to the nearest of 10,000 access points" into a single Dijkstra run?

## References
- Boeing, G. (2017). *OSMnx: New methods for acquiring, constructing, analyzing, and visualizing complex street networks.* Computers, Environment and Urban Systems 65.
- OSMnx 2.x docs: `graph_from_xml`, `simplify_graph`, `truncate_graph_polygon`, `nearest_nodes` / `nearest_edges`. https://osmnx.readthedocs.io/
- NetworkX docs: `MultiGraph`, `connected_components`, `multi_source_dijkstra`. https://networkx.org/documentation/stable/
- OSM wiki: `Key:highway`, `Key:sidewalk` (`separate`), `Tag:highway=cycleway` (shared-use paths), `Key:entrance`. https://wiki.openstreetmap.org/
- Bentley, J. L. (1975). *Multidimensional binary search trees used for associative searching* (KD-trees). CACM 18(9).

## 13. What was built (2026-11-23)

| Piece | Where | What it does |
|---|---|---|
| Config | `config/models.py`: `WalkFilter`, `NetworkConfig`; Portland's `network:` section | OSMnx's walk rules restated as defaults; `readmit` for shared-use paths; tolerance, entrance tags, thresholds |
| Walk-way extraction | `sources/osm.py`: `walk_exclusion`, `extract_walk_ways` | One pass over the PBF: walkable ways with complete geometry → sorted `.osm` XML; exclusions counted by rule (ways, km); entrance/gate nodes |
| Graph | `core/network.py`: `build_walk_graph` | `graph_from_xml` → clip → project → undirected → components (largest first, stable labels); canonical edges (`u ≤ v`, geometry from u, stable `edge_id`) |
| Snapping | `core/snap.py`: `snap_to_edges` | STRtree nearest edge (lowest index wins ties) → connector + offsets |
| Access | `core/access.py`: `park_access_points` | Edge-contact intervals + public entrances; `no_network_contact` per park |
| Pipeline | `pipeline/network.py`, CLI `touchgrass network` | Outputs, report, 6 hard + 6 info checks, `network_review.geojson`, freshness guard for `load` |
| Database | migration `0005_network.sql`; `db/load.py` | `block_origins`, `park_access_points` (FKs to blocks and parks), loaded in the same transaction |
| Tests | `tests/test_network.py` (26), `tests/network_fixture.py`, +2 in `test_db.py`, +1 real-data | Filter rules, the Hayden effect on a synthetic grid, canonical edges, determinism, hand-computed snapping, access intervals, private gate, island park, end to end, stale-network refusal |

**Changes from the plan:**
- **Edge weights are UTM lengths, not OSMnx's `length`.** A test failed by 0.24% on east–west streets, and that turned out to be OSMnx's *spherical* haversine: on WGS 84, a parallel at 45° N curves with radius 6,389 km, not the sphere's 6,371 km. On 3,000 real edges, OSMnx is −0.28% to +0.05% depending on direction, while UTM is −0.039% everywhere (D-020).
- **Freshness guard:** `load` refuses network outputs built from a different `parks.parquet` or `blocks.parquet` (SHA-256 in the report). Otherwise a later `process` could leave access points pointing at parks that no longer exist.

## 14. Results on Portland (2026-11-23)

| | |
|---|---|
| Run time | **106 s** (extract scan about 30 s, OSMnx build about 40 s); reruns are byte-identical |
| Walk filter | 123,690 ways kept, including **712 re-admitted shared-use paths**. Excluded: `access=private` 12,797 ways (1,383 km); `sidewalk:*=separate` 3,870 (322 km); motorways and links 1,434 (320 km); `foot=no` 1,003 (113 km); cycleways without a foot tag 438 (21 km); others |
| Graph | **105,395 nodes, 8,370 km** in the main component = **98.4%** of nodes; 490 pieces in all |
| Origins | 10,372 populated blocks; connector median **33.3 m**, p95 48.9 m, max 507 m; **99.35% of residents and 99.41% of blocks within 100 m** |
| Stranded | 4,244 residents in 38 blocks whose nearest street is a fragment (all ≤ 25 nodes: apartment-complex paths, a footway cut off by a private service road). Snapping them to the main network adds < 35 m for 30 of the 38 pieces, 36–82 m for four more, and 104–275 m for the last four (386 people) |
| Over 500 m | 1 block: the 27th Hayden Island block (41 people, 507 m) |
| Access points | **11,878**: 11,713 edge contacts + 165 entrances. 19 private gates and 18 entrances off the network excluded. **463 parks reachable, 10 without network contact** (Ross Island, Tri-Club Island, Moore and Wright Islands, Rivervilla Park, 3 ravine natural areas, 3 unnamed) |
| Database | 86,063 rows in 11 tables, one transaction, 3.1 s |

**Spot check of the sentinel parks** (contacts / entrances, and the named streets and trails that reach them):

| Park | Contacts | Entrances | Reached from (examples) |
|---|---|---|---|
| Forest Park | 209 | 9 | many trails and streets on all sides |
| Washington Park | 242 | 5 | |
| Powell Butte Nature Park | 51 | 4 | SE 162nd Ave, SE Powell Blvd, **Springwater Corridor** (present only because of `readmit`) |
| Mount Tabor Park | 136 | 8 | SE Yamhill St, SE 60th Ave, SE Lincoln St, SE Division St |
| Laurelhurst Park | 86 | 0 | SE César E. Chávez Blvd, SE Oak St, SE Stark St, SE 33rd Ave: the streets that actually border it |

**Not done by me: a visual check against OSM.** I can't look at a map. `data/processed/portland-or/network_review.geojson` holds the flagged blocks, parks without access, sentinel access points and stranded pieces, and opens in any GeoJSON viewer (e.g. QGIS or geojson.io). Please look at Hayden Island and two or three sentinel parks before Stage 6.

**For Stage 6, the same-edge case:** if a block's origin lies on an edge that also has a park contact interval, the direct walk along that edge (`|position − interval|`, or 0 inside it) can be shorter than going via u or v. The virtual-source Dijkstra handles everything else, but this case needs its own check.

## 15. Answers to §12

1. **Multi-graph:** two different ways can join the same two intersections. In Portland, a street and a parallel park path often both run between the same corners, or a block has a mid-block alley. A simple `Graph` keeps one edge per node pair and could keep the *longer* one, so `MultiGraph` keeps both and Dijkstra takes the shorter.
2. **A false connection is worse** for an access study. A false "unreachable" is loud: it shows up as missing values and gets investigated (that's how Hayden Island was found). A false connection across water is silent and makes residents look *better* served than they are, in exactly the cases (islands, rivers, freeways) an equity analysis exists to reveal.
3. **Node-snapping error grows with block length.** The extra walk is roughly the distance from the house to the nearest corner, up to half a block. Downtown's 200 ft (61 m) blocks cap it at about 30 m; East Portland superblocks 400–600 m long allow 200–300 m. The bias is therefore largest in the neighborhoods Stage 7 cares most about, which is another reason to snap to edges.
4. **Virtual source:** add one node S joined to each access edge's ends with weights `offset_u` and `offset_v`. Any shortest path from S to a node starts by "entering" at the cheapest access point, so one Dijkstra from S gives every node its distance to the *nearest* of all 11,878 access points at once. It's the same as multi-source Dijkstra, but with non-zero starting distances.

## 16. Your field check, and what it changed (2026-11-23)

You checked `network_review.geojson` against the map and against what's on the ground:

| Check | Your finding | Cause | Fix |
|---|---|---|---|
| Hayden Island | Pass: walkable via I-5 | (re-admitted shared-use path) | none |
| Laurelhurst, Mount Tabor | Pass | | none |
| Rivervilla Park | Walkable from SE Courtney Ave | The park's neighborhood (a 193-node pocket of Oak Grove) connects to the city only outside the 2 km buffer, so clipping stranded it | Kept the 2 km extent (your choice A); the pocket is marked `touches_clip`, and the park's reason is `network_piece_cut_by_buffer_edge` |
| Cottonwood Creek, Stephens Creek, Ravine Woods | Walkable | No mapped network within 15 m (28 m, 37 m, 91 m) | **Dry gap**: connect within 100 m if the gap crosses no water |
| 3 unnamed "parks" | A private lot, a building, Cascade Athletic Club | `landuse=recreation_ground` / `village_green` | Both tags dropped (D-018 amendment 3) |
| Hardtack Island block | An island | Snapped 275 m across the Willamette | **Dry snapping**: now `isolated` on its own piece |
| Rose City Golf Course, wooded edge | Mainland | Nearest path a cart path or isolated track | Snapping them to the street network was right; unchanged |
| Ross Island | Closed to the public | OSM has no `access` tag | Listed in `parks.closed_to_public` (D-018 amendment 4): excluded with that reason, guarded by a hard check that the id and name still match |

**Why water and not a distance threshold:** Moore and Wright Islands is 26 m from the network, closer than Cottonwood Creek at 28 m. Only *what lies in between* tells them apart. The same rule separates floating homes (the point itself is on water, so a dock is assumed: 10 blocks, 254 people) from a genuine island (Hardtack: dry land, but open water to the mainland).

**A trap found on the way:** rivers were missing from the first water extract. The park rule keeps an area if its *representative point* is inside the buffer, and the Willamette's lies far upstream. `extract_areas(keep="intersects")` fixes that for water. The park rule stays as it was, which is correct for parks.

**Results after the fixes:**

| | Before | After |
|---|---|---|
| Park units | 473 | **448** (24 weak-tag units, plus Ross Island closed) |
| Parks reachable directly / via dry gap / not at all | 463 / – / 10 | **443 / 3 / 2** (Moore and Wright Islands: water; Rivervilla: clipped) |
| Origins `ok` / `moorage` / `isolated` | n/a (all to main) | 10,361 / 10 (254 people) / 1 (19 people) |
| Residents within 100 m | 99.35% | 99.36% |
| Access points | 11,878 | 11,771 (11,605 contacts, 163 entrances, 3 gaps) |
| OSM vs ORCA precision / recall | 0.8961 / 0.9309 | 0.8972 / 0.9284 (Ross Island is in ORCA but closed, a definitional disagreement) |
| Run time | 106 s | 145 s (+ the water pass) |
