/** Synthetic API responses (no real places: see tests/guard.test.ts). */

import type { AreaMetrics, Methodology, RegionDetail, RegionSummary } from "../src/api/client";
import type { AreaCollection, AreaFeature } from "../src/api/geojson";

export const SUMMARY: RegionSummary = {
  region_id: "testville-zz",
  name: "Testville",
  bbox: { west: -1, south: 1, east: 1, north: 2 },
  center: [0, 1.5],
  latest_run_id: "abc123",
};

export const REGION: RegionDetail = {
  ...SUMMARY,
  analysis_crs: 32631,
  walk_speed_kmh: 4.8,
  thresholds: [
    { minutes: 5, meters: 400 },
    { minutes: 10, meters: 800 },
    { minutes: 15, meters: 1200 },
  ],
  underserved_minutes: 10,
  area_types: [
    { area_type: "district", areas: 2 },
    { area_type: "neighborhood", areas: 3 },
  ],
  acs_vintage: "2020-2024",
  osm_snapshot: "000000",
  metrics_available: true,
};

const square = (x: number): AreaFeature["geometry"] => ({
  type: "Polygon",
  coordinates: [
    [
      [x, 1],
      [x + 0.5, 1],
      [x + 0.5, 1.5],
      [x, 1.5],
      [x, 1],
    ],
  ],
});

export function feature(id: string, name: string, pct: number, ranked = true): AreaFeature {
  return {
    type: "Feature",
    id,
    geometry: square(id.length % 3),
    properties: {
      area_id: id,
      area_type: "neighborhood",
      name,
      pop_total: ranked ? 2000 : 300,
      pop_within: Math.round((ranked ? 2000 : 300) * pct),
      pct_within: pct,
      underserved_pop: 100,
      underserved_share: 1 - pct,
      median_walk_m: 500,
      priority_score: 50,
      ranked,
      reachable_park_ha_median: 3,
    },
  };
}

export const AREAS: AreaCollection = {
  type: "FeatureCollection",
  region_id: "testville-zz",
  run_id: "abc123",
  area_type: "neighborhood",
  threshold_min: 10,
  features: [
    feature("neighborhood:1", "OAK HOLLOW", 0.62),
    feature("neighborhood:2", "Riverbend", 0.97),
    feature("neighborhood:3", "TINY CORNER", 0.1, false),
  ],
};

export function metrics(overrides: Partial<AreaMetrics> = {}): AreaMetrics {
  return {
    area_id: "neighborhood:1",
    area_type: "neighborhood",
    name: "OAK HOLLOW",
    pop_total: 2000,
    blocks: 20,
    underserved_pop: 760,
    underserved_share: 0.38,
    unreachable_pop: 0,
    median_walk_m: 640,
    priority_score: 400,
    ranked: true,
    reachable_park_ha_median: 2.5,
    reachable_park_ha_mean: 4,
    park_m2_inside: 1000,
    park_m2_inside_per_resident: 0.5,
    block_share_within: 0.6,
    land_share_within: 0.5,
    thresholds: [
      { threshold_min: 5, threshold_m: 400, pop_within: 400, pct_within: 0.2 },
      { threshold_min: 10, threshold_m: 800, pop_within: 1240, pct_within: 0.62 },
      { threshold_min: 15, threshold_m: 1200, pop_within: 1900, pct_within: 0.95 },
    ],
    context: [],
    ...overrides,
  };
}

export const METHODOLOGY: Methodology = {
  region_id: "testville-zz",
  run_id: "abc123",
  walk_speed_kmh: 4.8,
  thresholds: REGION.thresholds,
  underserved_minutes: 10,
  analysis: {},
  metrics: { metrics: { min_residents_for_ranking: 1000 } },
  sources: [
    {
      source_id: "x",
      url: "https://example.org",
      vintage: null,
      retrieved_at: "2026-01-01T00:00:00Z",
      attribution: "Example Agency",
    },
  ],
  caveats: [
    { id: "walk_network", title: "Distances are walking routes.", text: "On mapped paths." },
  ],
};
