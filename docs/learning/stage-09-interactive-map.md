# Stage 9: Interactive Map Frontend

> Status: **complete (2026-11-23, commits `2635677`, `8442cc7`, `6000586`).** §13 is what was built, §14 the browser check, §15 the answers to §12, and §16 where the build departed from the plan.
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

## 13. What was built (2026-11-23)

| Piece | Where | What it does |
|---|---|---|
| Project | `web/package.json`, `package-lock.json`, `.npmrc` (`before=2026-11-08`, `save-exact`), `.nvmrc` (24), `.prettierrc.json`, `tsconfig.json` (strict + `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`) | MapLibre 6.17.0, React 19.1.10; dev: Vite 8.3.6, TypeScript 5.9.3, Vitest 5.1.0, jsdom 29, Testing Library, openapi-typescript 7.13, Prettier 3.9.10 (the `before` cutoff picked it over 3.10) |
| API client | `src/api/client.ts`, `schema.d.ts` (generated, `npm run gen:api`), `geojson.ts` | Typed fetches with abort signals and readable errors; base URL from `VITE_API_URL` |
| Style | `src/map/style.ts` | Block classes from the region's thresholds (cool ≤ the underserved threshold, warm beyond, gray for no route); share classes at fixed 50/70/85/95%; legend items from the same lists |
| Map bridge | `src/map/MapView.tsx` | Created once per region. Data layers slotted into the basemap: area fills under water/roads/labels (opaque, so overlaps don't stack; transparent from z12 but still clickable); blocks and parks under roads; outlines (solid, dashed for unranked) and hover/selection highlight under labels. Feature state for hover and selection; a click reports all overlapping areas and the block |
| UI | `App.tsx`, `components/Controls.tsx` (search combobox, threshold and area-type toggles), `SidePanel.tsx`, `Legend.tsx`, `state/url.ts`, `format.ts`, `styles.css` | URL state with back/forward; the finest area type and the underserved threshold by default; the panel shows shares at every threshold, underserved residents, the typical walk, reachable park area, the not-ranked note, overlapping areas, the clicked block, and caveats + source attributions from `/methodology`; a collapsible legend; a phone layout |
| Tests | `tests/` (25) | Style mapping evaluated by **MapLibre's own expression evaluator** at every class boundary; URL round trip; formatting; components with a mocked map; App start-up and API-down state; region/coordinate guard; live-API contract (skipped when the API is down) |
| Dev server | `.claude/launch.json` → `web` | `npm --prefix web run dev` on :5173 |

**Bugs and surprises, in the order found:**
1. **MapLibre 6 has no default export** (and no UMD build): `import * as maplibregl`.
2. **"Worker failed to load":** MapLibre finds its web worker relative to its own module, and Vite's dependency pre-bundling moves the module. Fix: import the worker file's URL (`?url`) and call `setWorkerUrl`. Works in dev and in `vite build` (the worker is emitted as its own asset).
3. **Dark header and panel** in the in-app browser: it emulates `prefers-color-scheme: dark`, and the page had no background of its own. Fix: `color-scheme: light` and an explicit white background. My first attempt edited a line Prettier had already reformatted, so it silently did nothing. Lesson: edits that assert their target.
4. **Back button did nothing:** I wrote `history.pushState` *inside* a React state updater. StrictMode runs updaters twice in development, so every click pushed two identical entries. Fix: updaters stay pure; an effect writes the URL.
5. **Test asked for a "searchbox":** a search input with a suggestion list *is* a combobox to assistive technology. The test now finds it by role and label, which also checks the label.
6. **Prettier reformatted the generated types,** so "regenerated types match" could never be true. `.prettierignore` now excludes `schema.d.ts`.
7. **Legend covered the map on phones:** it's now collapsible, closed below 761 px, and pinned top-right there.

## 14. Browser check (in-app browser, 2026-11-23)

| Check | Result |
|---|---|
| Initial view | Fits the city from the API's bbox; 93 neighborhoods at 10 min; legend; attribution "OpenFreeMap © OpenMapTiles Data from OpenStreetMap" |
| Load | Data layers ready **0.6 s** after reload (warm). Cold first load of the basemap was ~8 s in the prototype, all of it OpenFreeMap's CDN |
| Search "North Tabor" | Selects it, URL `?threshold=10&area=neighborhood%3A16`, flies to z14. Panel: 5,663 residents; 6% / **54%** / 99% within 5 / 10 / 15 min; 2,602 beyond 10 min; typical walk 777 m (10 min); 1.1 ha |
| Blocks at z14 | 425 blocks drawn. North Tabor's south half is orange and red (beyond 10 and 15 min); the blocks along Halsey near the parks are blue |
| Threshold toggle | 5 min recolors the map (most neighborhoods under 50%), legend title follows, URL replaced (no history entry) |
| Click | Selects the area under the cursor (Sabin: 65% / 100% / 100%); no console errors |
| Back / forward | One entry per selection; back clears it, forward restores it |
| Area type | Council districts: 4 areas, labels and search say "council district" |
| Phone (375 px) | No horizontal scroll; header stacks; legend collapsed top-right; panel below the map |
| Console | No errors in the checked flows |
| `vite build` | Builds; main chunk 1.27 MB (353 KB gzipped), mostly MapLibre; worker emitted separately |

### Review for you (acceptance)
1. Open the map: it's running in the browser pane (**http://localhost:5173**; the API must be running too). Find your own neighborhood with the search box, check its 10-minute share, then zoom in and see which blocks are orange or red.
2. Do the colors read the way they should? Blue means within 10 minutes and orange/red beyond. Parks are dark green. Neighborhood shares go from pale yellow to dark blue.
3. Anything confusing in the panel wording?

## 15. Answers to §12
1. **Light green for 800–1,200 m:** green reads as "good" and sits next to the blues on the same ramp, so the eye groups it with the served blocks. But beyond 800 m *is* underserved by our definition. A legend can be correct and the picture still wrong; the hue change has to sit on the policy line.
2. **Fixed vs quantile at 5 min:** fixed breaks show that 57 of 93 neighborhoods have under half their residents within 5 minutes, which is the truth. Quantiles would paint a fifth of the city dark blue, implying "these are well served at 5 minutes" when the share there may be only 45%. Quantiles answer "how does this area rank?"; fixed breaks answer "how well served is it?". The map's question is the second.
3. **Overlapping transparent fills:** where two 60%-opaque polygons overlap, the colors multiply into a darker, more saturated patch. It looks like a third class ("even better served") that exists nowhere in the data. Opaque fills (the later one covers the earlier) plus "this spot is in 2 areas" in the panel avoid it.
4. **`new maplibregl.Map` on each render:** every threshold change would tear down the WebGL context, refetch the style, fonts and tiles (seconds), lose the camera position, and leak contexts (browsers cap them at about 16). The bridge creates the map once and applies changes with `setData` and feature state.

## 16. Departures from the plan
- **TypeScript 5.9.3, not 7.0** (openapi-typescript requires `^5`), and **jsdom 29** (30+ excludes Node 25). Both recorded in D-024.
- **`@types/node`** added for the tests' file checks, and **`@maplibre/maplibre-gl-style-spec`** (already a MapLibre dependency) made explicit so tests can run MapLibre's own expression evaluator.
- **An area-type toggle** (neighborhoods / council districts) came almost free from the API and the D-022 MAUP point, so it's in.
- **Dev-only `window.__map`** for the scripted browser check and console debugging; it isn't present in production builds.
- **The panel's "About these numbers"** shows the caveats and source attributions now (RLIS requires the attribution); the full methodology page remains Stage 10.

## 17. Your review and the changes it led to (2026-11-23)

You asked for four changes. Here is the reasoning on each, then what was built.

| Your point | Assessment | Built |
|---|---|---|
| 1. Two kinds of information: residents' share and the walk from a block. Split them into two views | Agreed, and it was the main source of confusion. The zoom level silently changed what a color meant, both legends were always shown, and the walk-time toggle did nothing to the blocks | A **Show** switch: *Residents near a park* / *Walk from each block*, in the URL. The walk-time choice and the matching legend appear only where they apply. Block tiles start at z12, so switching zooms in, and a "zoom in" note replaces an empty map |
| 2. "Residents within 15 min, by neighborhood" is confusing | Agreed. "Nature" would overclaim, because the park rules count mapped parks and natural areas but exclude golf courses, cemeteries and the zoo | Legend: "Residents who live within a 15-minute walk of a park or natural area"; items "50%–70% of residents". Panel headline: "**54%** of residents live within a 10-minute walk of a park or natural area." |
| 3. Limit panning to around the neighboring states, and show the coast | Agreed, but derived from the region's bbox (+600 km) rather than listing states: there's no state geometry in the data, and naming states in code would break D-004 | `maxBounds` from `expandBounds(bbox, 600 km)` (unit-tested, Mercator-clamped). For Portland: all of Oregon and Washington, the coast, and into Idaho, Nevada and California. My first try, 400 km, measured in the browser, cut off eastern Oregon |
| 4. Highlight the clicked block; clearer block info, including its neighborhood | Agreed, plus the **nearest park's name**, which turns a number into an answer | Black outline on the selected block. The panel section, headed **Census block**, shows: "In Mt. Tabor" (linked); a swatch and "**1,222 m** to **Frazer Park**"; "about a 15 min walk, **farther than the 10-minute line**"; residents and parks within reach; the block id in small print |

**Found along the way:**
- **MapLibre's CSS beats yours if it loads later.** `.maplibregl-map { position: relative }` overrode `.map { position: absolute }` after the map moved into a wrapper, leaving a 0-px-high map. Fixed with a more specific selector.
- **ETag = run id isn't enough.** I added the park name to the tiles without a new analysis run, so the browser's cached tiles were still "current" by ETag. The ETag now includes a response-format version, and the API test pins it. The general lesson: **a cache key must cover everything that changes the bytes**: data *and* code.
- **"62%" appeared twice** once the headline was added, so the test now reads the share list by its accessible name, which is also a screen-reader improvement.
- **`PARK_COLOR`** moved from the map module to the style module. Colors belong with the style, and the tests mock the map module.

**Tests:** web 25 → 31 (pan limit, block class lookup, block-first panel with park name and area link, unnamed park and no route, plain-words headline, blocks view hides the walk-time choice). API 35 (+ park name and ETag version assertions).

## 18. Second review: About, overlay, palette, design, dark mode (2026-11-23)

| Your point | Assessment | Built |
|---|---|---|
| An About page | Yes. It's also the right home for Stage 10's planned methodology page, so one page, not two | `/about`: framing, city figures (652,503 residents; 85% within 10 min; 97,753 farther), the four measurement steps with the real parameters, the caveats, data sources, and credits linking your name to luangia.github.io. All facts come from the API |
| A more noticeable zoom-out warning | Yes, but not a blocking modal, which would stop scroll-to-zoom | The map dims, and a centered card ("Blocks appear when you zoom in") has a Zoom in button; the overlay ignores the pointer |
| The yellow is too unnoticeable; maybe red/orange/green/blue | The real problem was worse than faintness: the **worst** class was the palest. Agreed on warm-for-bad, but no green (it's the parks' color and the red-green color-blind pair) | The residents view uses the block view's palette: red, orange, three blues. Both views now read "warm = worse access" |
| Modern, minimal, scientific, white, some green but not overwhelming | Precision and visible sources make it feel scientific, more than decoration does. Green stays an accent | Inter; near-black text on white; hairlines; one forest-green accent; pill toggles; hero numbers; softer parks (the old dark green was what felt heavy) |
| Dark mode (yours) | | System default + remembered toggle, the dark basemap, faded buildings, MapLibre controls themed |

**Found while checking in the browser:**
- On the dark basemap the **buildings sit above the area fills in near-black**, which made the residents view noisy. Fix: fade every basemap `building` fill layer in the residents view and restore it in the block view. This works for any OpenMapTiles-schema basemap, not just these two styles.
- **Screen-reader spacing:** the hero number and its sentence are separate elements, and without a space between them the text read "62%of residents". The test caught it.
- You were using the pane while I tested. My click on the theme toggle flipped your light choice to dark; I put light back.

**Tests:** web 36 (+ routing, About page from API data and the credit link, theme toggle and memory, navigation, palette consistency). API unchanged (35).

