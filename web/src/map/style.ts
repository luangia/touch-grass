/**
 * Data → color, as pure functions (DECISIONS.md D-024 Q5–Q7). The map layers and the legend
 * are both built from these class lists, so they can't disagree.
 *
 * Blocks: one class per walk-time threshold, plus "beyond the last" and "no route". The
 * palette splits at the underserved threshold (D-011): cool hues within it, warm beyond.
 * Neighborhoods: share of residents within the chosen threshold, FIXED breaks, so a color
 * means the same share at every threshold and in every region.
 *
 * Colors are ColorBrewer RdYlBu (blocks) and YlGnBu (shares), both rated colorblind-safe.
 */

import type { ExpressionSpecification } from "maplibre-gl";

export type LegendItem = { color: string; label: string };
export type Threshold = { minutes: number; meters: number };

// RdYlBu: blues ordered near → far (dark → light), oranges/reds near → far (light → dark).
const COOL = ["#2c7bb6", "#74add1", "#abd9e9"];
const WARM = ["#fdae61", "#f46d43", "#d7191c"];
export const NO_ROUTE_COLOR = "#9e9e9e";
export const NO_DATA_COLOR = "#e0e0e0";

/** ColorBrewer YlGnBu, 5 classes: low share = light, high share = dark. */
export const SHARE_COLORS = ["#ffffcc", "#a1dab4", "#41b6c4", "#2c7fb8", "#253494"];
export const SHARE_BREAKS = [0.5, 0.7, 0.85, 0.95];

function spread(palette: string[], n: number): string[] {
  if (n > palette.length) throw new Error(`need ${n} colors, palette has ${palette.length}`);
  if (n === 1) return [palette[0]!];
  return Array.from(
    { length: n },
    (_, i) => palette[Math.round((i * (palette.length - 1)) / (n - 1))]!,
  );
}

export type BlockClass = { maxMeters: number | null; color: string; label: string };

/** Classes for block walking distance: ≤ each threshold, then beyond the last. */
export function blockClasses(thresholds: Threshold[], underservedMinutes: number): BlockClass[] {
  const sorted = [...thresholds].sort((a, b) => a.minutes - b.minutes);
  if (sorted.length === 0) throw new Error("no thresholds");
  if (!sorted.some((t) => t.minutes === underservedMinutes)) {
    throw new Error(`underserved threshold ${underservedMinutes} min is not a threshold`);
  }
  const within = sorted.filter((t) => t.minutes <= underservedMinutes);
  const beyond = sorted.filter((t) => t.minutes > underservedMinutes);
  const cool = spread(COOL, within.length);
  const warm = spread(WARM, beyond.length + 1);
  const last = sorted[sorted.length - 1]!;
  return [
    ...within.map((t, i) => ({
      maxMeters: t.meters,
      color: cool[i]!,
      label: `≤ ${t.minutes} min (${Math.round(t.meters)} m)`,
    })),
    ...beyond.map((t, i) => ({
      maxMeters: t.meters,
      color: warm[i]!,
      label: `≤ ${t.minutes} min (${Math.round(t.meters)} m)`,
    })),
    { maxMeters: null, color: warm[beyond.length]!, label: `> ${last.minutes} min` },
  ];
}

/** fill-color for the block tile layer: no route first, then ≤ each class bound. */
export function blockColorExpression(classes: BlockClass[]): ExpressionSpecification {
  const distance: ExpressionSpecification = ["to-number", ["get", "network_m"]];
  const steps: (ExpressionSpecification | string)[] = [];
  for (const c of classes) {
    if (c.maxMeters === null) continue;
    steps.push(["<=", distance, c.maxMeters], c.color);
  }
  const fallback = classes[classes.length - 1]!.color;
  return [
    "case",
    ["!", ["to-boolean", ["get", "reachable"]]],
    NO_ROUTE_COLOR,
    ...steps,
    fallback,
  ] as ExpressionSpecification;
}

export function blockLegend(classes: BlockClass[]): LegendItem[] {
  return [
    ...classes.map(({ color, label }) => ({ color, label })),
    { color: NO_ROUTE_COLOR, label: "No walking route" },
  ];
}

/** fill-color for areas: share within the chosen threshold, fixed breaks; null → no data. */
export function shareColorExpression(): ExpressionSpecification {
  const stops: (number | string)[] = [];
  SHARE_BREAKS.forEach((b, i) => stops.push(b, SHARE_COLORS[i + 1]!));
  return [
    "case",
    ["==", ["get", "pct_within"], null],
    NO_DATA_COLOR,
    ["step", ["to-number", ["get", "pct_within"]], SHARE_COLORS[0]!, ...stops],
  ] as ExpressionSpecification;
}

export function shareLegend(): LegendItem[] {
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const bounds = [0, ...SHARE_BREAKS, 1];
  return SHARE_COLORS.map((color, i) => ({
    color,
    label:
      i === 0
        ? `under ${pct(bounds[1]!)}`
        : i === SHARE_COLORS.length - 1
          ? `${pct(bounds[i]!)} or more`
          : `${pct(bounds[i]!)}–${pct(bounds[i + 1]!)}`,
  }));
}

/** Index of the class a share falls in (for the side panel swatch). */
export function shareClassIndex(share: number): number {
  return SHARE_BREAKS.filter((b) => share >= b).length;
}
