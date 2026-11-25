/**
 * Top-level state: which region, area type, threshold and area are shown (mirrored in the
 * URL), the data for them, and the selection panel. All places and coordinates come from the
 * API (D-004, D-024).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  ApiError,
  type AreaMetrics,
  type Methodology,
  type RegionDetail,
  type RegionSummary,
} from "./api/client";
import { type AreaCollection, type AreaProperties, bboxOf } from "./api/geojson";
import { AboutPage } from "./components/AboutPage";
import { ComparePage } from "./components/ComparePage";
import { RankingsPage } from "./components/RankingsPage";
import { AreaTypeToggle, ModeToggle, SearchBox, ThresholdToggle } from "./components/Controls";
import { Legend } from "./components/Legend";
import { type Selection, SidePanel } from "./components/SidePanel";
import { typeLabel } from "./format";
import { type Bounds, type Camera, type MapClick, MapView } from "./map/MapView";
import { blockClasses, blockLegend, PARK_COLOR, shareLegend, type Theme } from "./map/style";
import {
  formatView,
  MAX_COMPARE,
  PAGES,
  type Mode,
  type Page,
  pageFromPath,
  parseView,
  pathFor,
  type ViewState,
} from "./state/url";
import { applyTheme, initialTheme } from "./theme";

const BASEMAP: Record<Theme, string> = {
  light: import.meta.env.VITE_BASEMAP_STYLE ?? "https://tiles.openfreemap.org/styles/positron",
  dark: import.meta.env.VITE_BASEMAP_STYLE_DARK ?? "https://tiles.openfreemap.org/styles/dark",
};

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
  const [page, setPage] = useState<Page>(() => pageFromPath(window.location.pathname));
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [city, setCity] = useState<AreaMetrics | null>(null);
  // The camera survives the map being rebuilt (theme switch, a visit to About).
  const camera = useRef<Camera | null>(null);
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
    const url = `${pathFor(page)}${formatView(view)}`;
    if (url === `${window.location.pathname}${window.location.search}`) return; // e.g. popstate
    if (pushNext.current) window.history.pushState(null, "", url);
    else window.history.replaceState(null, "", url);
  }, [view, page]);
  const navigate = useCallback((p: Page) => {
    pushNext.current = true;
    setPage(p);
    window.scrollTo(0, 0);
  }, []);
  useEffect(() => {
    applyTheme(theme, false);
  }, [theme]);
  const toggleTheme = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      applyTheme(next, true);
      return next;
    });
  }, []);
  useEffect(() => {
    const onPop = () => {
      setPage(pageFromPath(window.location.pathname));
      setView(parseView(window.location.search));
    };
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

  // City-wide figures: the map's start panel and the About page.
  useEffect(() => {
    if (!region || city) return;
    const ctl = new AbortController();
    api
      .metrics(region.region_id, "city", ctl.signal)
      .then((m) => setCity(m.area))
      .catch(() => undefined); // the page reads fine without the figures
    return () => ctl.abort();
  }, [region, city]);

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

  // From Rankings or Compare: open the area on the map, flying to it.
  const openOnMap = useCallback(
    (areaId: string) => {
      pushNext.current = true;
      setPage("map");
      selectArea(areaId, true);
    },
    [selectArea],
  );
  const setCompare = useCallback(
    (ids: string[]) => update({ compare: ids.slice(0, MAX_COMPARE) }),
    [update],
  );
  const toggleCompare = useCallback(
    (id: string) =>
      setCompare(
        view.compare.includes(id) ? view.compare.filter((x) => x !== id) : [...view.compare, id],
      ),
    [setCompare, view.compare],
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

  const header = (
    <header className="topbar">
      <a
        className="brand"
        href={pathFor("map")}
        onClick={(e) => {
          e.preventDefault();
          navigate("map");
        }}
      >
        <span className="brand-mark" aria-hidden="true" />
        TouchGrass
        <span className="brand-region">{region.name}</span>
      </a>
      <nav aria-label="Pages">
        {PAGES.map((p) => (
          <a
            key={p}
            href={pathFor(p)}
            aria-current={page === p ? "page" : undefined}
            onClick={(e) => {
              e.preventDefault();
              navigate(p);
            }}
          >
            {{ map: "Map", rankings: "Rankings", compare: "Compare", about: "About" }[p]}
            {p === "compare" && view.compare.length ? (
              <span className="count" aria-label={`${view.compare.length} picked`}>
                {view.compare.length}
              </span>
            ) : null}
          </a>
        ))}
      </nav>
      <button
        type="button"
        className="theme-toggle"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        title={theme === "dark" ? "Light theme" : "Dark theme"}
      >
        {theme === "dark" ? (
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <circle cx="12" cy="12" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <path
              d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path
              d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>
    </header>
  );

  if (page === "rankings") {
    return (
      <div className="app app-page">
        {header}
        <RankingsPage
          region={region}
          methodology={methodology}
          areaType={areaType}
          threshold={threshold}
          onAreaType={(t) => update({ areaType: t, area: null })}
          onThreshold={(t) => update({ threshold: t }, false)}
          onOpenArea={openOnMap}
        />
      </div>
    );
  }

  if (page === "compare") {
    return (
      <div className="app app-page">
        {header}
        <ComparePage
          region={region}
          methodology={methodology}
          areaType={areaType}
          features={areas?.features ?? []}
          selected={view.compare}
          onChange={setCompare}
          onOpenArea={openOnMap}
        />
      </div>
    );
  }

  if (page === "about") {
    return (
      <div className="app app-page">
        {header}
        <AboutPage
          region={region}
          methodology={methodology}
          city={city}
          onOpenMap={() => navigate("map")}
        />
      </div>
    );
  }

  return (
    <div className="app">
      {header}
      <div className="toolbar">
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
      <MapView
        key={`${region.region_id}-${theme}`}
        region={region}
        mode={mode}
        areas={areas}
        blockClasses={classes}
        selectedArea={view.area}
        selectedBlock={selection.block?.geoid20 ?? null}
        focus={focus}
        theme={theme}
        basemapStyle={BASEMAP[theme]}
        initialCamera={camera.current}
        onMoveEnd={(c) => {
          camera.current = c;
        }}
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
        city={city}
        compare={view.compare}
        onToggleCompare={toggleCompare}
        onSelectArea={(id) => selectArea(id, true)}
      />
    </div>
  );
}
