/**
 * No region-specific values in the frontend (D-004, Stage 9 acceptance): region ids, city
 * names, area names and coordinates all come from the API. Tokens are read from every
 * region config, like the Python guards.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = join(import.meta.dirname, "..", "..");
const SRC = join(import.meta.dirname, "..", "src");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

function regionTokens(): string[] {
  const dir = join(ROOT, "config", "regions");
  const tokens: string[] = [];
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".yaml"))) {
    const text = readFileSync(join(dir, f), "utf-8");
    const get = (key: string) => text.match(new RegExp(`^${key}:\\s*(.+)$`, "m"))?.[1]?.trim();
    const id = get("region_id");
    const name = get("name")?.split(",")[0]?.trim();
    const crs = get("analysis_crs")?.split(/\s/)[0];
    for (const t of [id, name, crs]) if (t) tokens.push(t.toLowerCase());
  }
  return tokens;
}

describe("no hardcoded regions in web/src", () => {
  // The generated API types quote example values from the API's docs, not app logic.
  const sources = files(SRC).filter((f) => !f.endsWith("schema.d.ts"));

  it("finds the region tokens to check", () => {
    expect(regionTokens().length).toBeGreaterThanOrEqual(3);
  });

  it("names no configured region, city or CRS", () => {
    const tokens = regionTokens();
    const offenders = sources.flatMap((f) => {
      const text = readFileSync(f, "utf-8").toLowerCase();
      return tokens.filter((t) => text.includes(t)).map((t) => `${f}: ${t}`);
    });
    expect(offenders).toEqual([]);
  });

  it("contains no coordinate-like literals (lon, lat pairs)", () => {
    const pair = /-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,2}\.\d{3,}/;
    const offenders = sources.filter((f) => pair.test(readFileSync(f, "utf-8")));
    expect(offenders).toEqual([]);
  });
});
