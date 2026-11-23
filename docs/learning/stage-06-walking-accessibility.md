# Stage 6: Walking Accessibility Analysis

> Status: **prepared, not implemented.** Code snippets show the planned approach.
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
