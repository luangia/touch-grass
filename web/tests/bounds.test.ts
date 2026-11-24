import { describe, expect, it } from "vitest";

import { expandBounds } from "../src/map/bounds";

describe("pan limit", () => {
  it("adds at least the margin on every side, wider in longitude away from the equator", () => {
    const [[w, s], [e, n]] = expandBounds([-1, 44, 1, 46], 400);
    const latDeg = 400 / 111.32;
    expect(s).toBeCloseTo(44 - latDeg, 6);
    expect(n).toBeCloseTo(46 + latDeg, 6);
    // At 46°N a degree of longitude is ~77 km, so 400 km is ~5.2°.
    expect(-1 - w).toBeCloseTo(400 / (111.32 * Math.cos((46 * Math.PI) / 180)), 6);
    expect(e - 1).toBeCloseTo(-1 - w, 9);
  });

  it("stays inside the Web Mercator world", () => {
    const [[w, s], [e, n]] = expandBounds([-179, -84, 179, 84], 400);
    expect([w, s, e, n]).toEqual([-180, -85, 180, 85]);
  });
});
