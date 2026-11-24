import { expression, latest } from "@maplibre/maplibre-gl-style-spec";
import { describe, expect, it } from "vitest";

import {
  blockClasses,
  blockClassOf,
  blockColorExpression,
  blockLegend,
  NO_DATA_COLOR,
  NO_ROUTE_COLOR,
  SHARE_COLORS,
  shareClassIndex,
  shareColorExpression,
  shareLegend,
} from "../src/map/style";

const THRESHOLDS = [
  { minutes: 15, meters: 1200 },
  { minutes: 5, meters: 400 },
  { minutes: 10, meters: 800 },
];

/** Evaluate an expression with MapLibre's own evaluator, as the map would. */
function evaluate(expr: unknown, properties: Record<string, unknown>): string {
  const spec = (latest as unknown as { paint_fill: Record<string, unknown> }).paint_fill[
    "fill-color"
  ];
  const parsed = expression.createExpression(expr, "layers[0].paint.fill-color", spec as never);
  if (parsed.result !== "success") throw new Error(JSON.stringify(parsed.value));
  const color = parsed.value.evaluate({ zoom: 10 }, { type: "Polygon", properties } as never) as {
    toString(): string;
  };
  return color.toString();
}

const rgba = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},1)`;
};

describe("block classes", () => {
  const classes = blockClasses(THRESHOLDS, 10);

  it("has one class per threshold plus beyond, split at the underserved line", () => {
    expect(classes.map((c) => c.maxMeters)).toEqual([400, 800, 1200, null]);
    expect(classes.map((c) => c.label)).toEqual([
      "≤ 5 min (400 m)",
      "≤ 10 min (800 m)",
      "≤ 15 min (1200 m)",
      "> 15 min",
    ]);
    // Within 10 min: blues; beyond: oranges/reds.
    expect(classes[1]!.color).not.toEqual(classes[2]!.color);
    expect(
      classes.slice(0, 2).every((c) => c.color.startsWith("#2c") || c.color === "#abd9e9"),
    ).toBe(true);
  });

  it("colors every boundary inclusively, as MapLibre evaluates it", () => {
    const expr = blockColorExpression(classes);
    const at = (m: number | null, reachable = true) => evaluate(expr, { network_m: m, reachable });
    expect(at(0)).toBe(rgba(classes[0]!.color));
    expect(at(400)).toBe(rgba(classes[0]!.color)); // ≤ is inclusive
    expect(at(400.01)).toBe(rgba(classes[1]!.color));
    expect(at(800)).toBe(rgba(classes[1]!.color));
    expect(at(800.5)).toBe(rgba(classes[2]!.color)); // underserved: warm
    expect(at(1200)).toBe(rgba(classes[2]!.color));
    expect(at(5000)).toBe(rgba(classes[3]!.color));
    expect(at(null, false)).toBe(rgba(NO_ROUTE_COLOR));
  });

  it("adapts to other threshold sets", () => {
    const two = blockClasses(
      [
        { minutes: 10, meters: 800 },
        { minutes: 20, meters: 1600 },
      ],
      10,
    );
    expect(two.map((c) => c.maxMeters)).toEqual([800, 1600, null]);
    expect(() => blockClasses(THRESHOLDS, 12)).toThrow(/not a threshold/);
  });

  it("finds the class of a distance for the panel, matching the map", () => {
    expect(blockClassOf(classes, 400)).toBe(classes[0]);
    expect(blockClassOf(classes, 801)).toBe(classes[2]);
    expect(blockClassOf(classes, 9999)).toBe(classes[3]);
    expect(blockClassOf(classes, null)).toBeNull();
  });

  it("legend lists the same colors plus no route", () => {
    const legend = blockLegend(classes);
    expect(legend.map((l) => l.color)).toEqual([...classes.map((c) => c.color), NO_ROUTE_COLOR]);
  });
});

describe("share classes", () => {
  const expr = shareColorExpression();

  it("uses fixed breaks at 50/70/85/95%", () => {
    const at = (p: number | null) => evaluate(expr, { pct_within: p });
    expect(at(0)).toBe(rgba(SHARE_COLORS[0]!));
    expect(at(0.4999)).toBe(rgba(SHARE_COLORS[0]!));
    expect(at(0.5)).toBe(rgba(SHARE_COLORS[1]!));
    expect(at(0.84)).toBe(rgba(SHARE_COLORS[2]!));
    expect(at(0.95)).toBe(rgba(SHARE_COLORS[4]!));
    expect(at(1)).toBe(rgba(SHARE_COLORS[4]!));
    expect(at(null)).toBe(rgba(NO_DATA_COLOR));
  });

  it("panel class index agrees with the map", () => {
    expect([0, 0.5, 0.7, 0.849, 0.85, 0.95, 1].map(shareClassIndex)).toEqual([0, 1, 2, 2, 3, 4, 4]);
  });

  it("legend labels", () => {
    expect(shareLegend().map((l) => l.label)).toEqual([
      "under 50% of residents",
      "50%–70% of residents",
      "70%–85% of residents",
      "85%–95% of residents",
      "95% or more of residents",
    ]);
  });
});
