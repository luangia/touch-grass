/**
 * The hand-written GeoJSON property types (src/api/geojson.ts) against the live API.
 * Skipped when the API isn't running (start it: see README).
 */

import { describe, expect, it } from "vitest";

import { AREA_PROPERTY_KEYS, type AreaCollection } from "../src/api/geojson";

const API = process.env.API_URL ?? "http://localhost:8000";

async function reachable(): Promise<boolean> {
  try {
    return (await fetch(`${API}/health`)).ok;
  } catch {
    return false;
  }
}

const live = await reachable();

describe.skipIf(!live)("live API contract", () => {
  it("area features carry exactly the typed properties", async () => {
    const [region] = (await (await fetch(`${API}/api/v1/regions`)).json()) as {
      region_id: string;
    }[];
    expect(region).toBeDefined();
    const detail = (await (await fetch(`${API}/api/v1/regions/${region!.region_id}`)).json()) as {
      area_types: { area_type: string }[];
    };
    for (const { area_type } of detail.area_types) {
      const fc = (await (
        await fetch(`${API}/api/v1/regions/${region!.region_id}/areas?type=${area_type}`)
      ).json()) as AreaCollection;
      expect(fc.type).toBe("FeatureCollection");
      for (const f of fc.features) {
        expect(Object.keys(f.properties).sort()).toEqual([...AREA_PROPERTY_KEYS].sort());
        expect(typeof f.id).toBe("string");
      }
    }
  });

  it("block tiles answer 204 outside the region", async () => {
    const [region] = (await (await fetch(`${API}/api/v1/regions`)).json()) as {
      region_id: string;
    }[];
    const res = await fetch(`${API}/api/v1/regions/${region!.region_id}/tiles/blocks/12/0/0.pbf`);
    expect(res.status).toBe(204);
  });
});
