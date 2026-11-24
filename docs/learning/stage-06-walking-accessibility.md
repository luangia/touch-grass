# Stage 6: Walking Accessibility Analysis

> Status: **implemented 2026-11-23, awaiting your review** (including the route check in §14). §13 is what was built, §14 the results, §15 answers to §12.
> Numbers marked **(measured 2026-11-23)** come from a throwaway prototype run on the real Stage 5 outputs (scratch script outside the repo). No project code was changed.

## 1. Objective and expected outcome

For every populated census block, compute **how far its residents walk to the nearest park** on the real pedestrian network, and compare that with the straight-line distance a simple buffer analysis would use.

At the end of the stage:
- `touchgrass analyze portland-or` runs one shortest-path computation over the Stage 5 network and writes `block_access` with these fields for each populated block:
  - network distance (m) and walking time (min)
  - the nearest park and the access point used
  - straight-line distance to the nearest park
  - detour ratio
  - a reachability flag
- Every run is recorded in `analysis_runs`: parameters, input checksums, code version and runtime. A rerun with the same inputs gives the same run id and identical rows.
- Invariants hold for 100% of blocks, or every violation is explained. Three sample blocks are checked against an independent router.

No neighborhood aggregation happens yet; that's Stage 7.

## 2. Why this stage exists

This is the number the whole project is about. Distance to the nearest park is what "access" means here (D-005, D-011), and how it's measured decides the answer:

| Measured how | Residents within 800 m (10 min) of a park, Portland **(measured 2026-11-23)** |
|---|---|
| Straight line (a buffer analysis) | **97.2%** |
| Walking network | **85.0%** |

The 12-point gap is about 80,000 people that a buffer map would call "served". Rivers, freeways, cul-de-sacs, ravines and missing crossings are why. Showing that gap honestly is the core of TouchGrass.

## 3. Dependencies

- **Stage 5 (complete, `f03f451`):** `network_edges` (149,895 edges, all pieces), `block_origins` (10,372, with offsets and `snap_status`), `park_access_points` (11,771, with offsets that already include gaps).
- **Stage 3:** `parks` (448 units) for the straight-line baseline.
- **Libraries:** nothing new. SciPy (already installed for OSMnx) provides `scipy.sparse.csgraph.dijkstra`, and NetworkX stays as a test oracle.
- **Decisions:** D-005 (multi-source shortest paths; straight line only as a comparison), D-011 (underserved = beyond 800 m), D-008 (metres in the analysis CRS), D-020 (snapping, offsets, statuses).

## 4. What the prototype found (measured 2026-11-23)

| Question | Answer |
|---|---|
| Speed, NetworkX (virtual source, Python heap) | 0.5 s to build + **0.30 s** to search |
| Speed, SciPy `csgraph.dijkstra` (compiled) | 0.10 s to build + **0.02 s** to search, and it also returns *which* access point is nearest |
| Do they agree? | Identical on all 104,785 reached nodes (max difference 0.0 m) |
| Does the 2,400 m cutoff matter? | Not for speed (0.32 s without it in NetworkX). It only hides 2 real distances > 2,400 m |
| Same-edge cases | 645 origins share an edge with a park contact; for **614**, walking straight along that edge is shorter than going via an end node (saving up to 624 m) |
| Blocks whose census point lies inside a park polygon | 175 blocks, 19,356 people: blocks containing both homes and park land (Pier Park, Mount Tabor, Forest Heights Open Space). Median distance 40 m |
| Invariant: network ≥ straight line − 15 m | **4 blocks violate it**, by up to 44.6 m. Cause: a Stage 5 bug (§5.6) |
| Forest Park interior | One block deep in Forest Park gets **2,628 m**. Interior trails aren't access edges, so the walk goes out to the boundary. A second Stage 5 bug (§5.6) |
| Unreachable | Hardtack Island (isolated, 19 people) + 2 blocks beyond the 2,400 m cutoff (29 people) |
| Within 400 / 800 / 1,200 m | Network **43.5% / 85.0% / 97.5%**; straight line 67.6% / 97.2% / 100.0% |
| Detour ratio (network ÷ straight, straight > 50 m) | median **1.35**, population-weighted 1.38, p90 2.05, p99 4.25 |
| Median network distance | 435 m (population-weighted 449 m) |

## 5. Concepts

### 5.1 Dijkstra's algorithm
Shortest paths from a source in a graph with **non-negative** edge weights:

```
dist[source] = 0; every other node = ∞; push (0, source) on a min-heap
while heap not empty:
    (d, x) = pop smallest
    if d > dist[x]: continue            # stale entry, already settled cheaper
    for each neighbour y of x with edge weight w:
        if d + w < dist[y]: dist[y] = d + w; push (dist[y], y)
```

- **Why it's correct:** when a node is popped it's the closest unsettled node, and since weights are non-negative, no later path can be shorter. That's the "greedy settles in order of distance" invariant.
- **Cost:** each edge relaxation may push onto the heap, so it's **O((V + E) log V)**. For us that's 105k nodes and 150k edges: about 4 million heap operations, milliseconds in compiled code.
- **Priority queue:** a binary heap. The `if d > dist[x]` line is the standard "lazy deletion" trick, which skips the decrease-key operation that `heapq` lacks.

### 5.2 Many sources at once: the virtual source
We want each node's distance to the **nearest of 11,771 access points**, not to one point. Add a virtual node S, connect it to every access point's edge ends with weights `offset_u_m` and `offset_v_m`, and run **one** Dijkstra from S. Any shortest path from S "enters" through the cheapest access point, so `dist[x]` is exactly the distance to the nearest one. Running 11,771 separate searches would be about 11,000× the work for the same answer.

SciPy's version: give each access point its own node and pass them all as sources with `min_only=True`. It returns the distances *and* `sources[x]`, the access point each node is nearest to. That gives the **nearest park id** for free.

### 5.3 From node distances to block distances

```
d(block) = connector_m + min( offset_u_m + D(u),  offset_v_m + D(v),  direct )
direct   = |position − contact interval|   if an access point lies on the block's own edge
```

The `direct` term is the same-edge case from Stage 5 §14. Without it, 614 blocks would be routed to an intersection and back along the same street, overstating their distance by up to 624 m. Walking time is `d / (80 m/min)` (D-005: 4.8 km/h).

### 5.4 Cutoffs
A cutoff stops the search at a radius, which is a big speed-up on continental graphs. Here it buys nothing (0.02 s either way) and costs information: 2 blocks beyond 2,400 m would get "∞" instead of their real distance. **Recommendation: no cutoff**, so every reachable block gets an exact distance. "Unreachable" then means genuinely no path, not "far".

### 5.5 Network vs straight-line distance, and the detour index
- **Straight line:** the Euclidean distance (analysis CRS) from the block's point to the nearest **park polygon**. This is what a buffer analysis measures.
- **Detour ratio** = network ÷ straight line. It's ≥ 1 apart from the access tolerances (§5.6), and it measures how indirect the network is around a place. A ratio near 1 means a direct grid; above 2 means rivers, freeways or cul-de-sacs. Portland's median is 1.35, and 10% of blocks have to walk more than twice the straight-line distance.
- **Unstable when the straight line is tiny:** 40 m network over 5 m straight is a ratio of 8 that means nothing. Ratios are reported only where the straight line is > 50 m.
- **Buffers vs service areas:** a buffer is a circle; a network service area is the set of streets within d. The difference in the §2 table is the difference between those two shapes.

### 5.6 Invariants, and the two Stage 5 bugs they caught
Property-style checks that must hold for every block are the strongest tests an analysis can have:

| Invariant | Why it must hold |
|---|---|
| `network_m ≥ straight_m − tolerance` | You can't walk to a park in less than the straight-line distance. The slack is the 15 m contact tolerance, plus up to 15 m more when an entrance node is snapped onto an edge, so **30 m** in total |
| `walk_min = network_m / speed` | Units |
| `pct(400) ≤ pct(800) ≤ pct(1200)` | Monotone thresholds (checked in Stage 7) |
| Same run inputs → same rows | Reproducibility |

The prototype found **4 blocks violating the first invariant**, by up to 44.6 m. All 4 lie on an edge whose stored contact span runs from 0 to the end of the edge, while the block itself is 55–80 m from the park. In Stage 5 an edge that comes near a park at **two separate places** got one span from the first touch to the last, so the middle of the street was treated as "at the park". **Fix (Stage 5 bug): one access row per contact stretch.**

The Forest Park block at 2,628 m exposed the second bug. Only edges near the park's *edge* count as access, so a resident snapped to a trail *inside* Forest Park "walks" out to the boundary. **Fix: any edge inside the grown park polygon counts as contact.** Being on a trail in the park means being in the park. Both fixes are small and belong to this stage's implementation (Q1).

### 5.7 Reproducible analysis runs
A result is only useful if you can say exactly what produced it. Each run records:
- **parameters:** speed, thresholds, tolerances, whether the same-edge rule is on;
- **inputs:** SHA-256 of `network_edges`, `block_origins`, `park_access_points`, `parks`;
- **code version:** git commit, plus a "dirty" flag if there are uncommitted changes;
- **timing**, and the result counts.

The **run id is a hash of parameters + inputs + code version**, so rerunning unchanged work reproduces the same id and the same rows, while changing anything gives a new id. Old runs stay in the database, so Stage 7 can compare, for example, the run before and after a park-rule change.

## 6. How the concepts relate

```
Stage 5:  edges ── access points (offsets) ── block origins (connector + offsets)
                         │
                 one virtual-source Dijkstra (SciPy, min_only → nearest access point)
                         │
          D(node), nearest park per node
                         │
   per block: connector + min(via u, via v, along own edge)  ──►  network_m, walk_min, park_id
                         │                                             │
   straight line to nearest park polygon (STRtree) ──► straight_m ──► detour ratio, invariants
                         │
            analysis_runs (params, input hashes, code version) + block_access rows
```

## 7. Fit in the architecture

```
pipeline/src/touchgrass/
  core/access.py          FIX   one row per contact stretch; interior edges count as contact
  core/routing.py         (NEW) build the sparse graph; multi-source Dijkstra; per-block distances
  pipeline/analyze.py     (NEW) run record, invariants, outputs, validation
  db/migrations/0007_analysis.sql (NEW) analysis_runs, block_access
  cli.py                  + `touchgrass analyze <region>`; `load` also loads runs
data/processed/<region>/
  block_access.parquet, analysis_run.json, analysis_validation.json
```

`block_access` (planned): `run_id, geoid20, reachable, network_m, walk_min, straight_m, detour_ratio, nearest_park_id, nearest_access_id, route_kind [via_node|same_edge], snap_status`. Primary key `(run_id, geoid20)`; foreign keys to `analysis_runs` and `census_blocks`.

## 8. Implementation approach

1. **Fix the Stage 5 contact intervals** (§5.6): one row per stretch (`access_id` gets a `#k` suffix), and interior edges count. Rerun `network`, compare counts, update the Stage 5 lesson.
2. **Routing** (`core/routing.py`): edges → symmetric sparse matrix, keeping the **minimum** of parallel edges (SciPy sums duplicates in COO→CSR unless we reduce first, which is a real trap). Add one node per access point, then `dijkstra(..., indices=access_nodes, min_only=True, return_predecessors=False)`. Map `sources` back to access ids and park ids.
3. **Per-block distance:** via u, via v, and same-edge direct (vectorised merge on `edge_id`). Unreachable → `reachable = false`, `network_m = NULL`.
4. **Straight line:** STRtree nearest park polygon from the block point (not the snapped point).
5. **Run record + outputs + checks**, then `load`.

### 8.1 Validation checks (planned)
| Check | Kind | Rule |
|---|---|---|
| analysis.every_origin_has_a_row | hard | rows = origins |
| analysis.network_not_shorter_than_straight | hard | `network_m ≥ straight_m − 30 m` for all reachable blocks (0 violations after the fix) |
| analysis.unreachable_explained | hard | every unreachable block is `isolated` (or listed with a reason) |
| analysis.time_matches_distance | hard | `walk_min × speed = network_m` |
| analysis.oracle_sample | hard | 50 random blocks: SciPy distance = NetworkX `multi_source_dijkstra` distance |
| analysis.summary | info | shares within 400/800/1,200 m (network vs straight), detour distribution, runtime |

### 8.2 Manual verification (acceptance)
Three sample blocks (one downtown, one East Portland, one by the river) get their route reconstructed from predecessors and exported as GeoJSON. You compare each with a web router (e.g. openstreetmap.org directions, "Foot"). I'll pick the three, and the comparison is yours to make, like the Stage 5 map check.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | Stage 5 fixes | **Fix both in this stage (per-stretch contacts, interior edges)** · fix only the span bug · leave and document | **Fix both**: each is small, and the invariant can then be a hard check |
| Q2 | Engine | **SciPy csgraph (+ NetworkX as test oracle)** · NetworkX only · pandana / igraph (new dependency) | **SciPy**: 15× faster, already installed, gives the nearest source directly |
| Q3 | Cutoff | **None** · 2 × max threshold (2,400 m) as in the plan | **None**: no speed cost, no hidden distances |
| Q4 | Same-edge rule | **On** · off (via nodes only) | **On**: 614 blocks would otherwise be overstated by up to 624 m |
| Q5 | Straight-line target | **Nearest park polygon** · nearest access point | **Polygon**: that's the buffer method we're contrasting with |
| Q6 | Unreachable blocks | **`reachable = false`, distance NULL; Stage 7 counts them as beyond every threshold and lists them** · drop them | **Keep and flag** (Hardtack's 19 residents are real) |
| Q7 | Run identity | **Deterministic hash of params + inputs + code version; runs accumulate** · random id per run · keep only the latest run | **Deterministic + accumulate** |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)
- [ ] `touchgrass analyze portland-or` writes `block_access` + run record; a rerun reproduces the run id and identical rows.
- [ ] All hard invariants hold for 100% of blocks (after the Stage 5 fixes), or each violation is explained.
- [ ] SciPy and NetworkX agree on a random sample; hand-computed fixture graphs give the exact answers.
- [ ] Runtime recorded in the run record.
- [ ] Network vs straight-line comparison reported (shares within thresholds, detour distribution).
- [ ] 3 sample routes exported, and checked by you against a web router.
- [ ] Migration 0007 applied; `load` loads runs; ruff, mypy and pytest pass; hardcoding guard passes.
- [ ] Docs: D-021 (Q1–Q7), D-020 amendment (contact fixes), ARCHITECTURE §4–5, lesson "what was built".

## 11. Proposed implementation checklist
- [ ] Stage 5 fixes: per-stretch contact rows; interior edges as contact; tests; rerun `network`
- [ ] `core/routing.py`: sparse graph (min of parallels), virtual-source Dijkstra, nearest access/park
- [ ] Per-block distance incl. same-edge; straight line; detour; reachability
- [ ] `pipeline/analyze.py`: run id, record, invariants, outputs; CLI `touchgrass analyze`
- [ ] Migration `0007_analysis.sql` + loader
- [ ] Tests: hand-computed graphs (incl. parallel edges, same-edge, unreachable), SciPy-vs-NetworkX oracle, invariants, determinism
- [ ] Run on Portland; export 3 sample routes; record results in this lesson
- [ ] Docs: D-021, D-020 amendment, ARCHITECTURE, log, status

## 12. Questions to think about before we start
1. Why does Dijkstra fail with negative edge weights? Could a negative weight appear in our graph? (Look at how offsets are computed.)
2. The virtual source has edges *to* access points. Why don't we also need edges *from* blocks to a virtual sink?
3. Straight-line access is 97.2% and network access is 85.0%. Which kinds of places would you expect to have the largest gap between the two? How would you test that guess with this stage's outputs?
4. A run id is a hash of parameters, inputs and code version. Why include the code version? What would go wrong without it?

## References
- Dijkstra, E. W. (1959). *A note on two problems in connexion with graphs.* Numerische Mathematik 1.
- Cormen, Leiserson, Rivest, Stein. *Introduction to Algorithms*, ch. 24 (single-source shortest paths).
- SciPy docs: `scipy.sparse.csgraph.dijkstra` (`indices`, `min_only`, `limit`). https://docs.scipy.org/doc/scipy/reference/sparse.csgraph.html
- NetworkX docs: `multi_source_dijkstra`. https://networkx.org/documentation/stable/
- Levinson, D. & El-Geneidy, A. (2009). *The minimum circuity frontier and the journey to work.* Regional Science and Urban Economics 39(6). (Circuity, i.e. the detour ratio, in urban networks.)

## 13. What was built (2026-11-23)

| Piece | Where | What it does |
|---|---|---|
| Stage 5 fixes | `core/access.py` | One access row per contact stretch (`#k`); interior edges count. Portland: 11,771 → 15,676 access points |
| Routing | `core/routing.py` | Sparse graph (minimum of parallel edges, explicit zeros kept), one node per access point, `dijkstra(min_only=True)`; per-block distance via u / via v / same-edge |
| Analysis | `pipeline/analyze.py`, CLI `touchgrass analyze` | Run id from (params, inputs, code); straight-line baseline; detour ratio; 5 hard checks (incl. the NetworkX oracle); summary; 4 sample routes; runs kept in `runs/<run_id>/` |
| Database | migration `0007_analysis.sql`; `db/load.py` | `analysis_runs`, `block_access`; every run on disk reloaded; a stale latest run refused |
| Tests | `test_routing.py` (8), `test_analyze.py` (5), +2 in `test_network.py`, +1 in `test_db.py` | Hand-computed graph (via u/v, same edge, unreachable, two parks), parallel-edge sum trap, explicit zero, negative weight, SciPy = NetworkX on a random graph; run identity, rerun bytes, new run on a changed parameter, staleness |

**Bugs hit:** pandas turned `None` into `NaN` in a string Series (fixed with `dtype=object`); the oracle sample was larger than the tiny synthetic graph; a divide-by-zero warning in the detour ratio (`np.divide(where=)`); and I first wrote a muddled route-reconstruction helper, replaced it with a temporary destination node.

## 14. Results on Portland (2026-11-23, run `b586d98bacd40d3f`)

| | |
|---|---|
| Run time | **1.0 s** total; the shortest-path search **24 ms** |
| Invariants | all 5 hard checks pass: **0** blocks shorter than the straight line allows (4 before the Stage 5 fix); SciPy = NetworkX on the sample |
| Unreachable | 1 block: Hardtack Island (19 people, `isolated`) |
| Route kinds | 9,706 via a node, **665** straight along their own edge |
| Within 5 / 10 / 15 min | network **43.7% / 85.0% / 97.5%**; straight line 67.6% / 97.2% / 100.0% |
| Walking distance | median 434 m per block, **447 m population-weighted**; p90 842 m; max 7,709 m |
| Detour ratio (straight > 50 m) | median **1.35**, p90 2.05, p99 4.25 (9,780 blocks) |

**The headline:** about 80,000 Portlanders (12 points) look served by a straight-line buffer but aren't within a 10-minute walk on the network.

### Route check for you (acceptance)
`data/processed/portland-or/runs/b586d98bacd40d3f/analysis_routes.geojson` holds each route's path, start point and destination access point. Compare each with a web router, for example openstreetmap.org → Directions → "Foot (OSRM)" or "Foot (GraphHopper)", from the **from** point to the **to** point:

| Sample | From (lat, lon) | To (lat, lon) | Park | Our walk | Straight |
|---|---|---|---|---|---|
| Near the region centre | 45.53975, −122.64995 | 45.53899, −122.65239 | (unnamed park) | 344 m, 4.3 min | 193 m |
| East, near 10 min | 45.48083, −122.53846 | 45.48572, −122.54193 | Raymond Park | 800 m, 10.0 min | 603 m |
| Largest detour | 45.48986, −122.69043 | 45.48547, −122.68616 | SW Terwilliger Blvd Parkway | 1,013 m, 12.7 min | 66 m |
| **Longest walk** | 45.59419, −122.62037 | 45.57593, −122.56660 | (unnamed park) | **7,709 m**, 96 min | 648 m |

Our distance includes the straight connector from the block point to the street (the router does that part its own way), so expect agreement within roughly 50–100 m on the first three. **The longest walk is suspicious:** it's in the airport/Columbia corridor, its nearest street is a service road 192 m away, and it walks 7.7 km for a park 648 m away. Either the area is genuinely cut off, or something like a private airport road is in the network. Please look at it specifically.

**Run identity note:** this run was made from uncommitted code (`dirty: true` with a diff hash in its record). After you commit, rerun `touchgrass analyze` to get a run tied to the clean commit. The rows will be identical; only the run id changes.

## 15. Answers to §12

1. **Negative weights:** Dijkstra settles a node when it's popped, assuming no later path can be cheaper. A negative edge breaks that, because a cheaper path could arrive after settling. Our weights are lengths and offsets. Offsets are `start` and `length − end`, and float noise could make `length − end` = −1e-12, which is why Stage 5 clips it at 0 and `build_routing_graph` rejects negatives outright.
2. **No virtual sink needed:** the search runs *from* the parks and labels **every** node with its distance to the nearest one. Each block then just reads the two numbers at its edge's ends, a constant-time lookup. A sink would be needed only to ask "from this one block to anywhere", one search per block, which is exactly the 10,000-search approach the virtual source avoids.
3. **The largest gaps** should be where straight lines cross barriers the network can't: the Willamette and Columbia, I-5 / I-84 / I-205, rail yards, ravines (SW hills), and large industrial or airport land. How to test: map `detour_ratio`, or `network_m − straight_m`, by block and look for clusters along those features, or compare the mean detour of blocks within 300 m of a freeway with the rest. Stage 7 can make this per neighborhood.
4. **Code version in the run id:** the same parameters and inputs can give different answers if the code changes. For example, the Stage 5 contact fix changed distances for hundreds of blocks with identical inputs. Without the code version, two different results would share one id, and "the same run" would no longer mean the same numbers.

## 16. Your route check (2026-11-23)

You routed the four samples on openstreetmap.org. I then asked the same OSRM foot router for each route's geometry, sending only the two public coordinates. Routers measure from where they snap your point onto a road, so the fair comparison is our distance minus our connector (the straight walk from the block point to the street):

| Sample | You (osm.org) | OSRM foot | Ours minus connector | Verdict |
|---|---|---|---|---|
| Near the region centre | 289 m | 314.2 m | 314 m | **exact** |
| East, near 10 min | 913 m | 784.9 m | 784 m | **exact** (your 913 m was likely another engine or slightly different clicks) |
| Largest detour | 921 m | 939.3 m | 939 m | **exact** |
| Longest walk | 7.0 km | 6,893 m from a start snapped **585 m** away | 7,518 m from a service road **192 m** away | **real difference, rule gap** |

**The longest walk is real:** the router also needs about 7 km, because the area by the airport is cut off from the park 648 m away. But the router refused our starting street. That service road (OSM way 484824281) is tagged **`access=no`**, and our walk filter, copied from OSMnx, only excludes `access=private`.

**The rule gap, measured on Portland:**
- **kept although closed:** 537 ways, 76.4 km, with `access=no` and no foot permission (mostly service roads); 19 of them are also `foot=private`, which nothing excludes;
- **dropped although open:** 152 ways, 15.5 km, with `access=private` but `foot=designated` or `yes`. In OSM a `foot=*` tag overrides `access` for pedestrians.

**Fixed (D-020 amendment 3, "Apply the fix"):** `access=no` and `foot=private` are closures, and `foot=yes|designated|permissive` overrides an `access` closure.

| | Before | After |
|---|---|---|
| Walkable ways | 123,690 | 123,245 |
| Main network | 8,370 km, 98.40% of nodes | 8,323 km, 98.47% |
| Pieces | 490 | 476 |
| Blocks whose distance changed > 1 m | | 35 (20 shorter, 15 longer) |
| Longest-walk block (410510073001017) | 7,709 m from an `access=no` road | **806 m**: it starts on the Marine Drive Trail 584 m away, like OSRM, and reaches Broughton Beach; OSRM gives 288 m for the street part vs our 222 m |
| Within 5 / 10 / 15 min | 43.7 / 85.0 / 97.5% | 43.7 / 85.0 / 97.5% |

That block is now **flagged**: its 584 m connector exceeds the 500 m limit. It's 6 people on airport industrial land with no public street nearby, which is the honest answer.

The new latest run is `8205a92013b15324`. The previous run stays as history.

**Lesson:** an independent router found what our own invariants couldn't. The invariants check that the computation is *consistent*; only an external reference checks that the *inputs* (which ways are walkable) are right.
