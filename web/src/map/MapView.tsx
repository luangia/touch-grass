/**
 * The bridge between React (declarative) and MapLibre (imperative), lesson §5.6.
 *
 * The map is created ONCE per region (the parent keys this component by region id).
 * Prop changes are applied with setData / setFeatureState / fitBounds, never by
 * re-creating the map.
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
import { type BlockClass, blockColorExpression, shareColorExpression } from "./style";

export type BlockProperties = {
  geoid20: string;
  pop20: number;
  reachable: boolean;
  network_m: number | null;
  walk_min: number | null;
  parks_reachable: number | null;
  reachable_park_ha: number | null;
};

export type MapClick = { areas: AreaProperties[]; block: BlockProperties | null };
export type Bounds = [number, number, number, number];

type Props = {
  region: RegionDetail;
  areas: AreaCollection | null;
  blockClasses: BlockClass[];
  selectedArea: string | null;
  focus: Bounds | null;
  basemapStyle: string;
  onHoverArea: (props: AreaProperties | null) => void;
  onClick: (hit: MapClick) => void;
  onError: (message: string) => void;
};

maplibregl.setWorkerUrl(workerUrl);

const AREAS = "areas";
const PARKS = "parks";
const BLOCKS = "blocks";
const BLOCK_MIN_ZOOM = 12; // the API serves block tiles from z12 (D-023 Q6)
const BLOCK_MAX_ZOOM = 14; // ... to z14; MapLibre overzooms beyond
const PARK_COLOR = "#1b7837";
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

function addDataLayers(map: MLMap, region: RegionDetail, classes: BlockClass[]): void {
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
  });

  const layers: [LayerSpecification, string | undefined][] = [
    [
      {
        id: "areas-fill",
        type: "fill",
        source: AREAS,
        paint: {
          "fill-color": shareColorExpression(),
          // Opaque, so overlapping areas don't stack into a darker false signal (lesson §5.5).
          // Transparent (still clickable) where blocks take over.
          "fill-opacity": ["step", ["zoom"], 1, BLOCK_MIN_ZOOM, 0],
        },
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
        paint: { "fill-color": blockColorExpression(classes), "fill-opacity": 0.85 },
      },
      belowRoads,
    ],
    [
      {
        id: "parks-fill",
        type: "fill",
        source: PARKS,
        paint: { "fill-color": PARK_COLOR, "fill-opacity": 0.85 },
      },
      belowRoads,
    ],
    [
      {
        id: "areas-line",
        type: "line",
        source: AREAS,
        filter: ["!=", ["get", "ranked"], false],
        paint: { "line-color": "#424242", "line-width": 0.8 },
      },
      firstSymbol,
    ],
    [
      {
        id: "areas-line-unranked",
        type: "line",
        source: AREAS,
        filter: ["==", ["get", "ranked"], false],
        paint: { "line-color": "#424242", "line-width": 1.2, "line-dasharray": [2, 2] },
      },
      firstSymbol,
    ],
    [
      {
        id: "areas-highlight",
        type: "line",
        source: AREAS,
        paint: {
          "line-color": "#000000",
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
  const handlers = useRef(props); // latest callbacks without re-binding map events
  handlers.current = props;
  const [loaded, setLoaded] = useState(false);

  // Create once (per region: the parent keys this component by region id).
  useEffect(() => {
    if (!container.current) return;
    const { bbox } = props.region;
    const map = new maplibregl.Map({
      container: container.current,
      style: props.basemapStyle,
      bounds: [
        [bbox.west, bbox.south],
        [bbox.east, bbox.north],
      ],
      fitBoundsOptions: { padding: 24 },
    });
    mapRef.current = map;
    // Dev only: lets the browser check (and you, in the console) inspect the live map.
    if (import.meta.env.DEV) (window as unknown as { __map?: MLMap }).__map = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("load", () => {
      addDataLayers(map, props.region, props.blockClasses);
      setLoaded(true);
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
      const blocks = map.getLayer("blocks-fill")
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

  // New area data (threshold or area type changed): swap it in place.
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    (map.getSource(AREAS) as GeoJSONSource).setData((props.areas ?? EMPTY) as never);
  }, [loaded, props.areas]);

  // Selection outline (feature state survives setData because ids are promoted).
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    if (selected.current !== null)
      map.setFeatureState({ source: AREAS, id: selected.current }, { selected: false });
    if (props.selectedArea !== null)
      map.setFeatureState({ source: AREAS, id: props.selectedArea }, { selected: true });
    selected.current = props.selectedArea;
  }, [loaded, props.selectedArea, props.areas]);

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

  return <div ref={container} className="map" data-testid="map" />;
}
