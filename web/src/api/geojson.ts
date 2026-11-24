/**
 * GeoJSON bodies are built in SQL, not Pydantic, so their property types are written here
 * by hand (D-024 Q3). tests/geojson.contract.test.ts checks them against the live API.
 */

export type Position = [number, number];
export type Polygon = { type: "Polygon"; coordinates: Position[][] };
export type MultiPolygon = { type: "MultiPolygon"; coordinates: Position[][][] };
export type AreaGeometry = Polygon | MultiPolygon;

/** Properties of one feature from /areas. Metric fields are null for an area with no residents. */
export type AreaProperties = {
  area_id: string;
  area_type: string;
  name: string;
  pop_total: number | null;
  pop_within: number | null;
  pct_within: number | null;
  underserved_pop: number | null;
  underserved_share: number | null;
  median_walk_m: number | null;
  priority_score: number | null;
  ranked: boolean | null;
  reachable_park_ha_median: number | null;
};

export type AreaFeature = {
  type: "Feature";
  id: string;
  geometry: AreaGeometry;
  properties: AreaProperties;
};

/** RFC 7946 FeatureCollection plus foreign members describing what it holds. */
export type AreaCollection = {
  type: "FeatureCollection";
  region_id: string;
  run_id: string;
  area_type: string;
  threshold_min: number;
  features: AreaFeature[];
};

export const AREA_PROPERTY_KEYS: (keyof AreaProperties)[] = [
  "area_id",
  "area_type",
  "name",
  "pop_total",
  "pop_within",
  "pct_within",
  "underserved_pop",
  "underserved_share",
  "median_walk_m",
  "priority_score",
  "ranked",
  "reachable_park_ha_median",
];

/** [west, south, east, north] of a polygon geometry. */
export function bboxOf(geometry: AreaGeometry): [number, number, number, number] {
  const rings = geometry.type === "Polygon" ? geometry.coordinates : geometry.coordinates.flat();
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      w = Math.min(w, x);
      s = Math.min(s, y);
      e = Math.max(e, x);
      n = Math.max(n, y);
    }
  }
  return [w, s, e, n];
}
