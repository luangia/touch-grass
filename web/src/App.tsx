/**
 * Top-level state: which region, area type, threshold and area are shown (mirrored in the
 * URL), the data for them, and the selection panel. All places and coordinates come from the
 * API (D-004, D-024).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  ApiError,
  type Methodology,
  type RegionDetail,
  type RegionSummary,
} from "./api/client";
import { type AreaCollection, type AreaProperties, bboxOf } from "./api/geojson";
import { AreaTypeToggle, ModeToggle, SearchBox, ThresholdToggle } from "./components/Controls";
import { Legend } from "./components/Legend";
import { type Selection, SidePanel } from "./components/SidePanel";
import { typeLabel } from "./format";
import { type Bounds, type MapClick, MapView } from "./map/MapView";
import { blockClasses, blockLegend, PARK_COLOR, shareLegend } from "./map/style";
import { formatView, type Mode, parseView, type ViewState } from "./state/url";

const BASEMAP: string =
  import.meta.env.VITE_BASEMAP_STYLE ?? "https://tiles.openfreemap.org/styles/positron";

const WIDE = typeof window !== "undefined" && window.matchMedia?.("(min-width: 761px)").matches;

const NO_SELECTION: Selection = {
  areaId: null,
  metrics: null,
  loading: false,
  error: null,
  alsoIn: [],
  block: null,
};

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);

/** The area type with the most areas: the finest grain (e.g. neighborhoods over districts). */
function defaultAreaType(region: RegionDetail): string | null {
  const sorted = [...region.area_types].sort(
    (a, b) => b.areas - a.areas || a.area_type.localeCompare(b.area_type),
  );
  return sorted[0]?.area_type ?? null;
}

export function App() {
  const [view, setView] = useState<ViewState>(() => parseView(window.location.search));
  const [regions, setRegions] = useState<RegionSummary[] | null>(null);
  const [region, setRegion] = useState<RegionDetail | null>(null);
  const [methodology, setMethodology] = useState<Methodology | null>(null);
  const [areas, setAreas] = useState<AreaCollection | null>(null);
  const [selection, setSelection] = useState<Selection>(NO_SELECTION);
  const [hovered, setHovered] = useState<AreaProperties | null>(null);
  const [focus, setFocus] = useState<Bounds | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);

  // URL ⇄ state. The history write happens in an effect, not in the state updater: updaters
  // must be pure (StrictMode runs them twice, which pushed every entry twice).
  const pushNext = useRef(false);
  const update = useCallback((patch: Partial<ViewState>, push = true) => {
    pushNext.current = push;
    setView((v) => ({ ...v, ...patch }));
  }, []);
  useEffect(() => {
    const url = `${window.location.pathname}${formatView(view)}`;
    if (url === `${window.location.pathname}${window.location.search}`) return; // e.g. popstate
    if (pushNext.current) window.history.pushState(null, "", url);
    else window.history.replaceState(null, "", url);
  }, [view]);
  useEffect(() => {
    const onPop = () => setView(parseView(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Regions → the chosen (or first) region's detail and methodology.
  useEffect(() => {
    const ctl = new AbortController();
    api
      .regions(ctl.signal)
      .then(setRegions)
      .catch((e: unknown) => ctl.signal.aborted || setFatal(message(e)));
    return () => ctl.abort();
  }, []);
  const regionId = view.region ?? regions?.[0]?.region_id ?? null;
  useEffect(() => {
    if (!regionId || !regions) return;
    if (!regions.some((r) => r.region_id === regionId)) {
      setFatal(`Unknown region "${regionId}".`);
      return;
    }
    const ctl = new AbortController();
    Promise.all([api.region(regionId, ctl.signal), api.methodology(regionId, ctl.signal)])
      .then(([r, m]) => {
        setRegion(r);
        setMethodology(m);
      })
      .catch((e: unknown) => ctl.signal.aborted || setFatal(message(e)));
    return () => ctl.abort();
  }, [regionId, regions]);

  const areaType = region
    ? view.areaType && region.area_types.some((t) => t.area_type === view.areaType)
      ? view.areaType
      : defaultAreaType(region)
    : null;
  const mode: Mode = view.mode ?? "residents";
  const threshold = region
    ? view.threshold !== null && region.thresholds.some((t) => t.minutes === view.threshold)
      ? view.threshold
      : region.underserved_minutes
    : null;

  // Areas for (type, threshold).
  useEffect(() => {
    if (!region || !areaType || threshold === null) return;
    const ctl = new AbortController();
    api
      .areas(region.region_id, areaType, threshold, ctl.signal)
      .then(setAreas)
      .catch((e: unknown) => ctl.signal.aborted || setMapError(message(e)));
    return () => ctl.abort();
  }, [region, areaType, threshold]);

  // Selected area → metrics.
  useEffect(() => {
    if (!region || !view.area) {
      setSelection((s) => ({ ...s, areaId: null, metrics: null, loading: false, error: null }));
      return;
    }
    const ctl = new AbortController();
    setSelection((s) => ({ ...s, areaId: view.area, loading: true, error: null }));
    api
      .metrics(region.region_id, view.area, ctl.signal)
      .then((m) => setSelection((s) => ({ ...s, metrics: m.area, loading: false })))
      .catch(
        (e: unknown) =>
          ctl.signal.aborted ||
          setSelection((s) => ({ ...s, metrics: null, loading: false, error: message(e) })),
      );
    return () => ctl.abort();
  }, [region, view.area]);

  const selectArea = useCallback(
    (areaId: string, fly: boolean) => {
      update({ area: areaId });
      if (fly) {
        const f = areas?.features.find((x) => x.id === areaId);
        if (f) setFocus(bboxOf(f.geometry));
      }
    },
    [areas, update],
  );

  // Fly to an area named in the URL, once, when its geometry first arrives.
  const flewToUrlArea = useRef(false);
  useEffect(() => {
    if (flewToUrlArea.current || !view.area || !areas) return;
    const f = areas.features.find((x) => x.id === view.area);
    if (f) setFocus(bboxOf(f.geometry));
    flewToUrlArea.current = true;
  }, [areas, view.area]);

  // Residents view: a click selects the area. Blocks view: it selects the block; the area
  // stays as it was and is linked from the block's section (D-024 amendment).
  const onMapClick = useCallback(
    (hit: MapClick) => {
      if (mode === "blocks") {
        setSelection((s) => ({ ...s, alsoIn: hit.areas, block: hit.block }));
        return;
      }
      setSelection((s) => ({ ...s, alsoIn: hit.areas, block: null }));
      const first = hit.areas[0];
      if (first) update({ area: first.area_id });
    },
    [mode, update],
  );
  const changeMode = useCallback(
    (m: Mode) => {
      if (m === "residents") setSelection((s) => ({ ...s, block: null }));
      update({ mode: m });
    },
    [update],
  );

  const classes = useMemo(
    () => (region ? blockClasses(region.thresholds, region.underserved_minutes) : []),
    [region],
  );

  if (fatal) {
    return (
      <main className="fatal">
        <h1>TouchGrass</h1>
        <p className="error">The map can't load: {fatal}</p>
        <p>Is the API running? Start it with the command in the README.</p>
      </main>
    );
  }
  if (!region || !areaType || threshold === null) {
    return <main className="loading">Loading…</main>;
  }

  return (
    <div className="app">
      <header>
        <h1>
          TouchGrass <span className="muted">· {region.name}</span>
        </h1>
        <div className="controls">
          <SearchBox
            features={areas?.features ?? []}
            areaType={areaType}
            onSelect={(id) => selectArea(id, true)}
          />
          <ModeToggle value={mode} onChange={changeMode} />
          {/* Block colors already show every threshold, so the walk-time choice is for areas. */}
          {mode === "residents" ? (
            <ThresholdToggle
              thresholds={region.thresholds}
              value={threshold}
              onChange={(t) => update({ threshold: t }, false)}
            />
          ) : null}
          <AreaTypeToggle
            types={region.area_types}
            value={areaType}
            onChange={(t) => update({ areaType: t, area: null })}
          />
        </div>
      </header>
      <MapView
        key={region.region_id}
        region={region}
        mode={mode}
        areas={areas}
        blockClasses={classes}
        selectedArea={view.area}
        selectedBlock={selection.block?.geoid20 ?? null}
        focus={focus}
        basemapStyle={BASEMAP}
        onHoverArea={setHovered}
        onClick={onMapClick}
        onError={setMapError}
      />
      {/* Collapsible: open on wide screens, closed on phones where it would cover the map. */}
      <details className="legends" open={WIDE}>
        <summary>Legend</summary>
        {mode === "residents" ? (
          <Legend
            title={`Residents who live within a ${threshold}-minute walk of a park or natural area, by ${typeLabel(areaType).toLowerCase()}`}
            items={shareLegend()}
            note="Dashed outline: too few residents to rank."
          />
        ) : (
          <Legend
            title="Walk from each census block to the nearest park or natural area"
            items={blockLegend(classes)}
          />
        )}
        <Legend title="" items={[{ color: PARK_COLOR, label: "Park or natural area" }]} />
      </details>
      {mapError ? (
        <p className="error map-error" role="alert">
          {mapError}{" "}
          <button type="button" className="link" onClick={() => setMapError(null)}>
            dismiss
          </button>
        </p>
      ) : null}
      <SidePanel
        region={region}
        mode={mode}
        threshold={threshold}
        blockClasses={classes}
        selection={selection}
        hovered={hovered}
        methodology={methodology}
        onSelectArea={(id) => selectArea(id, true)}
      />
    </div>
  );
}
