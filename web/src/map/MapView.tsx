/**
 * The bridge between React (declarative) and MapLibre (imperative), lesson §5.6.
 *
 * The map is created ONCE per region (the parent keys this component by region id).
 * Prop changes are applied with setData / setFeatureState / setPaintProperty / easeTo,
 * never by re-creating the map.
 *
 * Two views (D-024 amendment): "residents" fills areas by the share of residents within the
 * chosen walk; "blocks" colors each census block by its walk to the nearest park, with areas
 * as outlines. Block tiles exist from z12, so the blocks view asks you to zoom in below that.
 */

import "maplibre-gl/dist/maplibre-gl.css";

// MapLibre 6 is ES-module only, with named exports and no default export.
import * as maplibregl from "maplibre-gl";
// MapLibre finds its web worker by a URL relative to its own module, which Vite's dependency
// pre-bundling moves. Hand it the worker file's URL explicitly (works in dev and in builds).
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type {
  GeoJSONSource,
  LayerSpecification,
  Map as MLMap,
  MapGeoJSONFeature,
} from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import type { RegionDetail } from "../api/client";
import { api } from "../api/client";
import type { AreaCollection, AreaProperties } from "../api/geojson";
import type { Mode } from "../state/url";
import { type Bounds, expandBounds, MAX_PAN_KM } from "./bounds";
import {
  type BlockClass,
  blockColorExpression,
  LINE_STYLE,
  PARK_STYLE,
  shareColorExpression,
  type Theme,
} from "./style";

export type { Bounds } from "./bounds";

/** Attributes of a block in the vector tiles (MVT drops null attributes, hence optional). */
export type BlockProperties = {
  geoid20: string;
  pop20: number;
  reachable: boolean;
  network_m?: number;
  walk_min?: number;
  straight_m?: number; // as the crow flies, to the nearest park polygon (D-025 Q3)
  detour_ratio?: number;
  nearest_park_name?: string;
  parks_reachable?: number;
  reachable_park_ha?: number;
};

export type MapClick = { areas: AreaProperties[]; block: BlockProperties | null };
export type Camera = { center: [number, number]; zoom: number };

type Props = {
  region: RegionDetail;
  mode: Mode;
  areas: AreaCollection | null;
  blockClasses: BlockClass[];
  selectedArea: string | null;
  selectedBlock: string | null;
  focus: Bounds | null;
  theme: Theme;
  basemapStyle: string;
  /** Where to start (e.g. after a theme switch rebuilt the map); else the region's bbox. */
  initialCamera: Camera | null;
  onMoveEnd: (camera: Camera) => void;
  onHoverArea: (props: AreaProperties | null) => void;
  onClick: (hit: MapClick) => void;
  onError: (message: string) => void;
};

maplibregl.setWorkerUrl(workerUrl);

const AREAS = "areas";
const PARKS = "parks";
const BLOCKS = "blocks";
export const BLOCK_MIN_ZOOM = 12; // the API serves block tiles from z12 (D-023 Q6)
const BLOCK_MAX_ZOOM = 14; // ... to z14; MapLibre overzooms beyond
const EMPTY: AreaCollection = {
  type: "FeatureCollection",
  region_id: "",
  run_id: "",
  area_type: "",
  threshold_min: 0,
  features: [],
};

/** First basemap layer drawing a given source-layer (e.g. water, transportation). */
function firstLayerOf(map: MLMap, sourceLayer: string): string | undefined {
  return map.getStyle().layers.find((l) => "source-layer" in l && l["source-layer"] === sourceLayer)
    ?.id;
}

/**
 * The basemap's buildings sit above the area fills (they're drawn after water). In the
 * residents view they're noise over the colors, nearly black on the dark basemap, so they fade.
 * In the blocks view they help you find your street, so they come back.
 */
function setBuildingEmphasis(map: MLMap, mode: Mode): void {
  for (const layer of map.getStyle().layers) {
    if (layer.type !== "fill" || !("source-layer" in layer) || layer["source-layer"] !== "building")
      continue;
    map.setPaintProperty(layer.id, "fill-opacity", mode === "residents" ? 0.12 : 1);
  }
}

/** Areas are opaque in the residents view; in the blocks view transparent but clickable. */
const areaOpacity = (mode: Mode) => (mode === "residents" ? 1 : 0);
const blockVisibility = (mode: Mode) => (mode === "blocks" ? "visible" : "none");

function addDataLayers(
  map: MLMap,
  region: RegionDetail,
  classes: BlockClass[],
  mode: Mode,
  theme: Theme,
): void {
  const park = PARK_STYLE[theme];
  const line = LINE_STYLE[theme];
  const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  // Areas go under the basemap's water, roads and labels (D-024 Q4); blocks and parks under
  // its roads; outlines over roads but under labels. Fallback: below the first label.
  const belowWater = firstLayerOf(map, "water") ?? firstSymbol;
  const belowRoads = firstLayerOf(map, "transportation") ?? firstSymbol;

  map.addSource(AREAS, { type: "geojson", data: EMPTY as never, promoteId: "area_id" });
  map.addSource(PARKS, { type: "geojson", data: api.parksUrl(region.region_id) });
  map.addSource(BLOCKS, {
    type: "vector",
    tiles: [api.blockTilesUrl(region.region_id)],
    minzoom: BLOCK_MIN_ZOOM,
    maxzoom: BLOCK_MAX_ZOOM,
    promoteId: "geoid20", // feature state (the selection outline) needs an id per block
  });

  const layers: [LayerSpecification, string | undefined][] = [
    [
      {
        id: "areas-fill",
        type: "fill",
        source: AREAS,
        // Opaque, so overlapping areas don't stack into a darker false signal (lesson §5.5).
        paint: { "fill-color": shareColorExpression(), "fill-opacity": areaOpacity(mode) },
      },
      belowWater,
    ],
    [
      {
        id: "blocks-fill",
        type: "fill",
        source: BLOCKS,
        "source-layer": "blocks",
        minzoom: BLOCK_MIN_ZOOM,
        layout: { visibility: blockVisibility(mode) },
        paint: { "fill-color": blockColorExpression(classes), "fill-opacity": 0.85 },
      },
      belowRoads,
    ],
    [
      {
        id: "parks-fill",
        type: "fill",
        source: PARKS,
        paint: { "fill-color": park.fill, "fill-opacity": park.opacity },
      },
      belowRoads,
    ],
    [
      {
        id: "parks-line",
        type: "line",
        source: PARKS,
        paint: { "line-color": park.outline, "line-width": 0.8, "line-opacity": 0.8 },
      },
      belowRoads,
    ],
    [
      {
        id: "areas-line",
        type: "line",
        source: AREAS,
        filter: ["!=", ["get", "ranked"], false],
        paint: { "line-color": line.area, "line-width": 0.8 },
      },
      firstSymbol,
    ],
    [
      {
        id: "areas-line-unranked",
        type: "line",
        source: AREAS,
        filter: ["==", ["get", "ranked"], false],
        paint: { "line-color": line.area, "line-width": 1.2, "line-dasharray": [2, 2] },
      },
      firstSymbol,
    ],
    [
      {
        id: "areas-highlight",
        type: "line",
        source: AREAS,
        paint: {
          "line-color": line.highlight,
          "line-width": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            3.5,
            ["boolean", ["feature-state", "hover"], false],
            2,
            0,
          ],
        },
      },
      firstSymbol,
    ],
    [
      {
        id: "blocks-selected",
        type: "line",
        source: BLOCKS,
        "source-layer": "blocks",
        minzoom: BLOCK_MIN_ZOOM,
        layout: { visibility: blockVisibility(mode) },
        paint: {
          "line-color": line.highlight,
          "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 0],
        },
      },
      firstSymbol,
    ],
  ];
  for (const [layer, before] of layers) map.addLayer(layer, before);
}

function toArea(f: MapGeoJSONFeature): AreaProperties {
  return f.properties as AreaProperties;
}

export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const hovered = useRef<string | null>(null);
  const selected = useRef<string | null>(null);
  const selectedBlock = useRef<string | null>(null);
  const handlers = useRef(props); // latest props without re-binding map events
  handlers.current = props;
  const [loaded, setLoaded] = useState(false);
  const [zoom, setZoom] = useState(0);

  // Create once (per region: the parent keys this component by region id).
  useEffect(() => {
    if (!container.current) return;
    const { bbox } = props.region;
    const box: Bounds = [bbox.west, bbox.south, bbox.east, bbox.north];
    const camera = props.initialCamera;
    const map = new maplibregl.Map({
      container: container.current,
      style: props.basemapStyle,
      ...(camera
        ? { center: camera.center, zoom: camera.zoom }
        : {
            bounds: [
              [bbox.west, bbox.south],
              [bbox.east, bbox.north],
            ] as [[number, number], [number, number]],
            fitBoundsOptions: { padding: 24 },
          }),
      maxBounds: expandBounds(box, MAX_PAN_KM), // the region and its surroundings, not the world
    });
    mapRef.current = map;
    // Dev only: lets the browser check (and you, in the console) inspect the live map.
    if (import.meta.env.DEV) (window as unknown as { __map?: MLMap }).__map = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("load", () => {
      addDataLayers(map, props.region, props.blockClasses, handlers.current.mode, props.theme);
      setBuildingEmphasis(map, handlers.current.mode);
      setLoaded(true);
    });
    setZoom(map.getZoom());
    map.on("zoomend", () => setZoom(map.getZoom()));
    map.on("moveend", () => {
      const c = map.getCenter();
      handlers.current.onMoveEnd({ center: [c.lng, c.lat], zoom: map.getZoom() });
    });
    map.on("error", (e) => {
      const msg = e.error?.message ?? "map error";
      handlers.current.onError(msg);
    });
    map.on("mousemove", "areas-fill", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const id = String(f.id);
      if (hovered.current !== id) {
        if (hovered.current !== null)
          map.setFeatureState({ source: AREAS, id: hovered.current }, { hover: false });
        map.setFeatureState({ source: AREAS, id }, { hover: true });
        hovered.current = id;
      }
      map.getCanvas().style.cursor = "pointer";
      handlers.current.onHoverArea(toArea(f));
    });
    map.on("mouseleave", "areas-fill", () => {
      if (hovered.current !== null)
        map.setFeatureState({ source: AREAS, id: hovered.current }, { hover: false });
      hovered.current = null;
      map.getCanvas().style.cursor = "";
      handlers.current.onHoverArea(null);
    });
    map.on("click", (e) => {
      const hits = map.queryRenderedFeatures(e.point, { layers: ["areas-fill"] });
      const blocks =
        handlers.current.mode === "blocks" && map.getLayer("blocks-fill")
          ? map.queryRenderedFeatures(e.point, { layers: ["blocks-fill"] })
          : [];
      // A point can lie in several overlapping areas: report all, once each.
      const seen = new Set<string>();
      const areas = hits.map(toArea).filter((a) => !seen.has(a.area_id) && seen.add(a.area_id));
      handlers.current.onClick({
        areas,
        block: blocks[0] ? (blocks[0].properties as BlockProperties) : null,
      });
    });
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // View mode: area fill and block layers; entering the blocks view zooms in if needed.
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    map.setPaintProperty("areas-fill", "fill-opacity", areaOpacity(props.mode));
    setBuildingEmphasis(map, props.mode);
    for (const id of ["blocks-fill", "blocks-selected"])
      map.setLayoutProperty(id, "visibility", blockVisibility(props.mode));
    if (props.mode === "blocks" && map.getZoom() < BLOCK_MIN_ZOOM)
      map.easeTo({ zoom: BLOCK_MIN_ZOOM, duration: 600 });
  }, [loaded, props.mode]);

  // New area data (threshold or area type changed): swap it in place.
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    (map.getSource(AREAS) as GeoJSONSource).setData((props.areas ?? EMPTY) as never);
  }, [loaded, props.areas]);

  // Area selection outline (feature state survives setData because ids are promoted).
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    if (selected.current !== null)
      map.setFeatureState({ source: AREAS, id: selected.current }, { selected: false });
    if (props.selectedArea !== null)
      map.setFeatureState({ source: AREAS, id: props.selectedArea }, { selected: true });
    selected.current = props.selectedArea;
  }, [loaded, props.selectedArea, props.areas]);

  // Block selection outline.
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    const at = (id: string) => ({ source: BLOCKS, sourceLayer: "blocks", id });
    if (selectedBlock.current !== null)
      map.setFeatureState(at(selectedBlock.current), { selected: false });
    if (props.selectedBlock !== null)
      map.setFeatureState(at(props.selectedBlock), { selected: true });
    selectedBlock.current = props.selectedBlock;
  }, [loaded, props.selectedBlock]);

  // Fly to a requested area (from search or URL).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !props.focus) return;
    const [w, s, e, n] = props.focus;
    map.fitBounds(
      [
        [w, s],
        [e, n],
      ],
      { padding: 48, maxZoom: 14, duration: 600 },
    );
  }, [props.focus]);

  const needZoom = props.mode === "blocks" && loaded && zoom < BLOCK_MIN_ZOOM - 0.01;
  return (
    <div className="map-wrap">
      <div ref={container} className="map" data-testid="map" />
      {needZoom ? (
        // Dims the map so "nothing drawn yet" is obvious, without blocking scroll-to-zoom: the
        // overlay ignores the pointer, only the card takes clicks.
        <div className="zoom-overlay">
          <div className="zoom-card" role="status">
            <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
              <circle
                cx="10.5"
                cy="10.5"
                r="6.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              />
              <path
                d="M10.5 7.5v6M7.5 10.5h6M15.5 15.5 20 20"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
            <p className="zoom-title">Blocks appear when you zoom in</p>
            <p className="zoom-text">
              Each census block's walk to a park is drawn from street level.
            </p>
            <button
              type="button"
              className="button-primary"
              onClick={() => mapRef.current?.easeTo({ zoom: BLOCK_MIN_ZOOM, duration: 600 })}
            >
              Zoom in
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
