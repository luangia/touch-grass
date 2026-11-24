# Stage 9: Interactive Map Frontend

> Status: **prepared, not implemented.** Code snippets show the planned approach.
> Findings marked **(prototype 2026-11-23)** come from a throwaway single-file page in the scratch area (MapLibre 6.17 from a CDN, the OpenFreeMap basemap, the running Stage 8 API), viewed in the in-app browser. No project code was changed. The temporary launch entry used to serve it was reverted.

## 1. Objective and expected outcome

A web map where a resident can:
- **find their neighborhood** (search by name, or click it);
- **see its share of residents within 5 / 10 / 15 minutes** of a park, with a threshold toggle;
- **zoom in to see which blocks are beyond a 10-minute walk.**

The page shows parks, neighborhoods colored by access, census blocks colored by walking distance, a legend, a side panel with the selected area's metrics, and the required attributions. All data comes from the Stage 8 API. The page knows **no coordinates or place names** of its own: the region's bounding box and names come from the API.

`npm run dev` in `web/` serves it at `http://localhost:5173`.

## 2. Why this stage exists

Stages 1–8 produced correct numbers. This stage makes them **findable and readable** by someone who's never heard of a census block.

It's also where the most consequential *presentation* choices are made: what color means "good", where the color breaks, how small or overlapping areas look. A map persuades. The design should say what the data says and nothing more.

## 3. Dependencies

- **Stage 8 API:** `/regions` (bbox), `/areas` (GeoJSON + headline metrics, per threshold), `/areas/{id}/metrics`, `/parks`, block tiles z12–14, `/methodology` (sources, caveats). CORS already allows `localhost:5173` and `127.0.0.1:5173`.
- **Decisions:** D-009 (React + TS + Vite, MapLibre GL JS, OSM-based basemap to confirm here), D-010 (GeoJSON for areas and parks, tiles for blocks), D-011 / D-022 (underserved = beyond 10 min; context not cause; small areas not ranked), D-004 (no region constants in code).

## 4. What the prototype found (prototype 2026-11-23)

| Question | Found |
|---|---|
| Basemap | **OpenFreeMap "positron"** style: no key, no registration, no stated request limits. Its TileJSON carries the attribution, and MapLibre showed "OpenFreeMap © OpenMapTiles Data from OpenStreetMap" automatically. ToS: provided as is, **no SLA**, may be discontinued without notice; self-hosting is supported (weekly planet downloads) |
| MapLibre 6 packaging | **ES module only** (`dist/maplibre-gl.mjs`): the old `<script>` global build is gone (the CDN returns 404). Fine with Vite (`import maplibregl from "maplibre-gl"`), but a surprise for copy-paste examples |
| Load in the in-app browser (cold) | Style ready 8.3 s (fonts, sprites and tiles from the CDN; network-bound), neighborhood GeoJSON 222 ms, first idle 10.6 s |
| Threshold toggle (refetch + `setData`) | **74 ms** for 93 neighborhoods |
| Block tiles at z13.5 | 899 blocks rendered, no console errors (one stale error came from the first, non-module attempt) |
| Hover highlight | Needs `promoteId: "area_id"`: our ids are strings, and feature state needs a numeric id or a promoted property |
| Layer order | Data layers inserted **below the first symbol (label) layer** keep street and place names readable on top |
| **Color problem** | My prototype colored 800–1,200 m light green, which reads as "fine". But beyond 800 m is *underserved* (D-011). **The color scale must break at the policy threshold:** cool colors within 10 minutes, warm beyond |
| Neighborhood shares under fixed breaks (50 / 70 / 85 / 95%) | 5 min: 57 / 23 / 5 / 4 / 4 neighborhoods per class · **10 min: 3 / 14 / 14 / 22 / 40** · 15 min: 1 / 3 / 3 / 7 / 79 |
| Overlapping neighborhoods | 23.5 km² of overlap (5.8% of neighborhood area). Semi-transparent fills **stack** there and look darker: a false signal |
| What the zoomed map shows | North Tabor between I-84 and Burnside: blocks at 800–1,200 m and beyond, with Mt. Tabor Park to the south. That's your Stage 7 field check, visible at a glance |

## 5. Concepts

### 5.1 How a web map draws (WebGL, tiles, styles)
- MapLibre GL draws on a `<canvas>` with **WebGL**. Each frame, it tessellates the vector geometry of the visible tiles into triangles on the GPU. That's why panning is smooth and styling can change instantly.
- The **basemap** arrives as vector tiles (same z/x/y pyramid as our blocks, §5.5 of Stage 8). A **style** (JSON) says how to draw them: layers, colors, fonts (*glyphs*) and icons (*sprites*).
- **Display projection: Web Mercator (EPSG:3857).** It preserves shapes locally but inflates areas toward the poles. At Portland's latitude a km² looks about 2× larger than at the equator, but uniformly across the city, so neighborhoods stay comparable to each other.

### 5.2 Sources vs layers
A **source** is *data*; a **layer** is *one way of drawing* a source. One source can feed many layers:

```ts
map.addSource("areas", { type: "geojson", data: url, promoteId: "area_id" });
map.addLayer({ id: "areas-fill", type: "fill", source: "areas", maxzoom: 12, paint: {...} });
map.addLayer({ id: "areas-line", type: "line", source: "areas", paint: {...} });
map.addSource("blocks", { type: "vector", tiles: [tileUrl], minzoom: 12, maxzoom: 14 });
map.addLayer({ id: "blocks", type: "fill", source: "blocks", "source-layer": "blocks", minzoom: 12, ... });
```

- **GeoJSON source:** the whole FeatureCollection is downloaded once and cut into tiles in a web worker. That suits ~100 areas and ~450 parks.
- **Vector source:** tiles are fetched as you pan. That suits 10k blocks (D-010).
- Source `maxzoom: 14` means "the server has nothing finer": MapLibre **overzooms** the z14 tile beyond that. `minzoom: 12` means it never requests coarser tiles (the API would answer 422).

### 5.3 Data-driven styling (expressions)
Paint properties can be **expressions** evaluated per feature on the GPU side:

```ts
// Blocks: classes break at the thresholds; cool = within 10 min, warm = beyond (D-011).
"fill-color": ["case",
  ["!", ["to-boolean", ["get", "reachable"]]], NO_ROUTE,
  ["step", ["get", "network_m"], C.within5, m5, C.within10, m10, C.within15, m15, C.beyond15]]
```

`m5`, `m10` and `m15` come from the API's region thresholds, never from literals in code. The *style-building functions* are pure TypeScript (data in, expression out), so they're unit-testable without a browser.

### 5.4 Feature state for hover and selection
Changing a GeoJSON source's data to highlight one area would re-tile everything. **Feature state** is a small key-value store per feature id that expressions can read:

```ts
map.setFeatureState({ source: "areas", id: hoveredId }, { hover: true });
"line-width": ["case", ["boolean", ["feature-state", "hover"], false], 3, 0.6]
```

The ids must be numbers or a promoted property (`promoteId`), which our string `area_id`s need.

### 5.5 Classification and color (the honest-map part)
- **Fixed breaks vs quantiles.** Quantile classes put an equal number of areas in each color, so the map always "has a worst fifth", even if everyone's fine. Fixed breaks (50/70/85/95%) mean the same color is the same share at every threshold and in every region. At 5 minutes most of the map is in the low classes because most neighborhoods *are* (§4). Fixed breaks show that truthfully.
- **Break at the policy line.** Within 10 minutes and beyond 10 minutes are different *kinds* of result, so they get different hue families (a diverging scheme centered on the threshold), not neighboring shades of one ramp.
- **Colorblind safety:** about 8% of men have red-green color vision deficiency. ColorBrewer's RdYlBu and YlGnBu schemes are rated colorblind-safe. Blue against orange stays distinguishable in deuteranopia and protanopia. Never encode with color alone: the legend has text and the panel has numbers.
- **"No route"** (19 residents on Hardtack Island) is not "far": it gets its own neutral gray with a label.
- **Small areas** (< 1,000 residents, D-022 Q2) are shown with the same colors but a dashed outline, and the panel says why they aren't ranked. Hiding them would be worse.
- **Overlaps:** semi-transparent fills stack in shared areas and look darker. The fix is to draw neighborhood fills **opaque**, so the later polygon simply covers the earlier one. They go *low* in the basemap's layer stack: the positron style draws water (layer 2), then land cover, buildings, roads (9–32), then labels. Inserting our fills just above the basemap's background and below its `water` layer keeps rivers, streets and names drawn on top. If the basemap's land-cover tints wash out our colors, the fallback is 0.7 opacity and accepting the stacking on 5.8% of the area; the browser check decides. The panel lists *every* neighborhood containing the clicked point, so a shared block is never silently attributed to one.

### 5.6 React and an imperative map
React re-renders a component tree from state; MapLibre is an **imperative** object with its own state (camera, sources, layers). The bridge is a single component that:
1. creates the map once in an effect (`useRef` for the container and the instance) and removes it on unmount;
2. adds sources and layers when the style has loaded;
3. **reacts to props** (threshold, selected area) by calling `setData`, `setFeatureState` or `setPaintProperty`, never by re-creating the map.

```tsx
useEffect(() => { /* create once */ return () => map.remove(); }, []);
useEffect(() => { map.getSource("areas")?.setData(areasUrl(threshold)); }, [threshold]);
```

A wrapper library (react-map-gl) hides this. Writing the ~150 lines yourself is the learning point, and it avoids one more dependency (Q2).

### 5.7 A typed API client
- **OpenAPI → TypeScript:** `openapi-typescript` turns the API's `/openapi.json` into TypeScript types. If a field is renamed in Python, `tsc` fails in `web/`.
- **GeoJSON bodies** aren't Pydantic models (they're built in SQL), so their *property* types are written by hand and checked by a test against the live API.

### 5.8 State in the URL
`?region=portland-or&threshold=10&area=neighborhood:16`: a link reproduces the view; back and forward work. React state mirrors the URL; no state library is needed for one map and one panel.

### 5.9 Accessibility
A canvas map is invisible to screen readers and awkward by keyboard. The **search box (a combobox of area names) and the side panel are the accessible path** to every number on the map. Choosing a result there also moves the map. The legend uses text, not only swatches.

### 5.10 Supply chain (npm)
npm installs the *latest* matching version at install time. Like `exclude-newer` for uv (D-014), npm's **`before`** setting (`.npmrc`: `before=2026-11-08`) ignores anything published after a date. `package-lock.json` pins exact versions with integrity hashes. Versions available before the cutoff (checked 2026-11-23): MapLibre 6.17.0, React 19.1.10, Vite 8.3.6, TypeScript 7.0.2, Vitest 5.1.0, Testing Library React 16.3.3.

## 6. How the concepts relate

```
URL (?region&threshold&area) ⇄ React state ──► <MapView> (imperative bridge) ──► MapLibre (WebGL canvas)
        │                          │                 sources: basemap (OpenFreeMap), parks + areas (GeoJSON),
        │                          │                          blocks (vector tiles z12–14)
        │                          │                 layers:  styled by pure functions (thresholds → expressions)
        │                          ├──► <SearchBox> (names from /areas)       ┐ accessible path
        │                          ├──► <SidePanel> (/areas/{id}/metrics)     ┘ to every number
        │                          └──► <Legend> (same class definitions as the style)
        ▼
typed API client (types generated from /openapi.json; GeoJSON property types by hand)
```

## 7. Fit in the architecture

```
web/                                  (NEW)
  package.json, package-lock.json     exact versions; .npmrc before=2026-11-08; .nvmrc
  vite.config.ts, tsconfig.json       strict; Vitest (jsdom)
  index.html
  src/
    api/client.ts                     fetch wrappers; base URL from VITE_API_URL
    api/schema.d.ts                   generated from /openapi.json (npm run gen:api)
    api/geojson.ts                    hand-written property types for /areas and /parks
    map/style.ts                      thresholds → class breaks → MapLibre expressions + legend items
    map/MapView.tsx                   the imperative bridge (§5.6)
    components/SearchBox.tsx, SidePanel.tsx, Legend.tsx, ThresholdToggle.tsx, Attribution.tsx
    state/url.ts                      URL ⇄ state
    App.tsx, main.tsx
  tests/                              style mapping, URL state, components with a mocked map
```

The frontend **computes no metrics** (ARCHITECTURE §2): it only chooses colors for values the API provides.

## 8. Implementation approach

1. Scaffold Vite + React + TS (strict), Prettier, `.npmrc` with `before`, `.nvmrc`, and the lockfile (Q1).
2. API client and generated types; the base URL comes from `VITE_API_URL` (default `http://localhost:8000`).
3. `style.ts`: class definitions from region thresholds and fixed share breaks; expression builders; legend items. Unit tests first.
4. `MapView`: create the map with the region bbox; add parks, areas (fill below z12, outline always) and blocks (z12+) below labels; hover by feature state; click → select.
5. Panel: selected area's shares at 5/10/15, underserved residents, median walk, reachable park area, the "not ranked" note, and all areas containing the point. ACS context and the methodology page come in Stage 10.
6. Search box over area names; threshold toggle; URL sync; legend; attributions (basemap automatic + our sources from `/methodology`, required by the RLIS licence).
7. Loading and error states (API down → a clear message, not a blank map).
8. Tests and a browser check in the in-app browser (pan, zoom, toggle, click, search; no console errors).

### 8.1 Testing strategy
- **TypeScript strict;** `tsc --noEmit` in the test script.
- **Unit (Vitest):** style mapping (every bin boundary, no-route, thresholds from data); URL round trip; legend matches style classes.
- **Components (Testing Library, jsdom):** SearchBox filters and selects, SidePanel renders metrics and the unranked note, ThresholdToggle, and the error state. MapLibre is mocked: jsdom has no WebGL.
- **Contract:** generated types compile against the live API (`gen:api` diff is empty).
- **Guard:** no region ids, place names or coordinates in `web/src` (the same tokens as the Python guards, plus a check for coordinate-like literals).
- **Browser check (manual + scripted in the in-app browser):** initial view fits the city; toggle updates colors; z12+ shows blocks; click → panel; search → fly-to; no console errors; attributions visible.

## 9. Decisions for you before implementation

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | Tooling | **Vite + React + TS strict, npm with `.npmrc before=2026-11-08`, lockfile, Prettier; Node 24 LTS in `.nvmrc`** (your machine has Node 25, which also works) · pnpm/yarn · ESLint too | **npm + before + lockfile, Prettier, no ESLint** (strict TS catches most of it; fewer deps) |
| Q2 | Map binding | **Plain `maplibre-gl` + a small React bridge** · react-map-gl | **Plain**: one dependency fewer; you see the lifecycle |
| Q3 | API types | **Generated from `/openapi.json` (openapi-typescript, committed) + hand-written GeoJSON props** · all hand-written | **Generated**: Python changes break `tsc`, not users |
| Q4 | Layers by zoom | **Neighborhood fill below z12 (opaque, under the basemap's water, roads and labels; 0.7 opacity fallback), outlines always; blocks z12+; parks always** · blocks only | **As described** |
| Q5 | Block colors | **Fixed classes at the region's thresholds: 2 cool (≤5, ≤10 min), 2 warm (≤15, >15), gray "no route"; not affected by the toggle** · binary within/beyond the toggled threshold | **Fixed 4 + no route**: one stable picture of distance |
| Q6 | Neighborhood colors | **Share within the toggled threshold, fixed breaks 50/70/85/95%, 5 classes; small areas dashed outline** · quantile breaks per threshold | **Fixed breaks**: same color = same share everywhere |
| Q7 | Palettes | **ColorBrewer-derived, colorblind-safe: RdYlBu split at 10 min for blocks; YlGnBu (low = light) for shares** · custom | **ColorBrewer**, with text in legend and panel |
| Q8 | State | **React state mirrored to the URL (`region`, `threshold`, `area`)**; no state library · in-memory only | **URL**: shareable, back/forward work |
| Q9 | Finding a neighborhood | **Name search (combobox) + click**; address search out of scope (needs an external geocoder; privacy) · geocoder now | **Name search + click** |
| Q10 | Basemap | **OpenFreeMap positron, style URL from `VITE_BASEMAP_STYLE`** so it can be swapped or self-hosted · another provider | **OpenFreeMap via config**; no SLA noted as a Stage 11 risk |
| Q11 | Tests | **Vitest + Testing Library (jsdom, map mocked) + a scripted check in the in-app browser** · add Playwright now | **No Playwright yet** (Stage 11 hardening) |

## 10. Acceptance criteria (from PROJECT_PLAN, made concrete)
- [ ] A user can find their neighborhood (search or click), see its share within 10 minutes (and 5 / 15 via the toggle), and zoom in to see which blocks are beyond 10 minutes.
- [ ] Initial view comes from the API's bbox; no hardcoded coordinates, region ids or place names in `web/src` (guard test).
- [ ] Layers: basemap, parks, neighborhoods, blocks; legend matching the style; attributions for OpenFreeMap / OpenMapTiles / OSM and for our data sources.
- [ ] Hover highlights, click selects (panel with metrics), threshold toggle, URL state.
- [ ] `tsc` strict clean; unit + component tests pass; browser check: no console errors.
- [ ] Docs: D-024 (Q1–Q11), ARCHITECTURE §2, README quickstart, lesson "what was built".

## 11. Proposed implementation checklist
- [ ] Scaffold `web/` (Vite, React, TS strict, Vitest, Prettier), `.npmrc` before, `.nvmrc`, lockfile
- [ ] API client + generated types + GeoJSON property types; env config (`VITE_API_URL`, `VITE_BASEMAP_STYLE`)
- [ ] `style.ts` + tests (bins from thresholds, share classes, legend)
- [ ] `MapView` bridge: sources, layers below labels, hover state, click select, fit to bbox
- [ ] SidePanel, SearchBox, ThresholdToggle, Legend, Attribution; loading/error states
- [ ] URL state + tests
- [ ] Guard test; `tsc`; component tests
- [ ] Browser check in the in-app browser; launch config for `web`
- [ ] Docs: D-024, ARCHITECTURE, README, log, status, lesson §13+

## 12. Questions to think about before we start
1. Why does the prototype's light green for 800–1,200 m mislead, even though the legend is technically correct?
2. At 5 minutes, 57 of 93 neighborhoods fall in the lowest class under fixed breaks. Quantile breaks would spread them evenly. Which is more honest, and what would each make a reader believe?
3. Two semi-transparent neighborhoods overlap. What does the overlap look like, and why is that a false signal?
4. The map component re-renders whenever the threshold changes. What goes wrong if each render calls `new maplibregl.Map(...)`?

## References
- MapLibre GL JS documentation: sources, layers, expressions, feature state.
- Brewer, C. A. *Designing Better Maps*, and ColorBrewer 2.0 (colorblind-safe schemes).
- Monmonier, M. (2018). *How to Lie with Maps*, 3rd ed.: classification and color.
- OpenFreeMap terms of service and FAQ (read 2026-11-23).
- openapi-typescript documentation.
