/**
 * Census context, framed as context (D-011, D-025 Q4-Q5): estimates carry their margin of
 * error and reliability; low-reliability values are greyed; nothing here is ranked or sorted
 * by value; and differences no larger than their margin of error are called "not clear".
 */

import type { ContextValue, Methodology, ResidentComparison } from "../api/client";
import { int } from "../format";

type Indicator = { id: string; label: string };

/** Indicator labels from the metrics run's parameters (they're config, not code). */
export function indicatorLabels(m: Methodology | null): Record<string, string> {
  const params = m?.metrics as { metrics?: { indicators?: Indicator[] } } | null;
  return Object.fromEntries((params?.metrics?.indicators ?? []).map((i) => [i.id, i.label]));
}

const pp = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

/** "38% ± 6%" for a share; "2,208 ± 386" for a count; "no estimate" when suppressed. */
export function estimateText(v: ContextValue): string {
  if (v.estimate === null || v.estimate === undefined) return "no estimate";
  if (v.kind === "share") return `${pp(v.estimate)} ± ${pp(v.moe ?? 0)}`;
  return `${int(v.estimate)} ± ${int(v.moe ?? 0)}`;
}

const RELIABILITY_NOTE = {
  high: "reliable",
  medium: "use with care",
  low: "unreliable: shown, not compared",
} as const;

export function ContextList(props: { values: ContextValue[]; labels: Record<string, string> }) {
  if (!props.values.length) return null;
  return (
    <section aria-label="Census context" className="context">
      <h3>Who lives here</h3>
      <p className="note">
        American Community Survey estimates, ± 90% margin of error. Context, not cause.
      </p>
      <dl>
        {props.values.map((v) => (
          <div key={v.indicator} className={`ctx ctx-${v.reliability}`}>
            <dt>{props.labels[v.indicator] ?? v.indicator}</dt>
            <dd>
              {estimateText(v)}{" "}
              <span className={`badge badge-${v.reliability}`}>
                {RELIABILITY_NOTE[v.reliability]}
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

type Item = ResidentComparison["indicators"][number];

/**
 * One plain sentence per indicator. Neutral about the unit (a share of residents or of
 * households, per the indicator's label); differences in percentage points; "no clear
 * difference" when the gap is within its margin of error.
 */
export function comparisonSentence(item: Item, minutes: number): string {
  const b = `${pp(item.beyond.estimate, 1)} (± ${pp(item.beyond.moe, 1)})`;
  const w = `${pp(item.within.estimate, 1)} (± ${pp(item.within.moe, 1)})`;
  const base = `${item.label}: ${b} where residents live farther than a ${minutes}-minute walk, ${w} where they live within it`;
  if (!item.clear) return `${base}. No clear difference: the gap is within the margin of error.`;
  const points = (Math.abs(item.difference) * 100).toFixed(1);
  const moe = (item.difference_moe * 100).toFixed(1);
  const dir = item.difference > 0 ? "higher" : "lower";
  return `${base}. A clear difference: ${points} points (± ${moe}) ${dir} farther away.`;
}

export function ResidentComparisonBlock(props: { comparison: ResidentComparison }) {
  const { comparison } = props;
  if (!comparison.indicators.length) return null;
  const first = comparison.indicators[0]!;
  return (
    <section aria-label="Who lives farther from parks" className="resident-comparison">
      <p>
        Of {int(first.within.residents + first.beyond.residents)} residents,{" "}
        {int(first.beyond.residents)} live farther than a {comparison.threshold_min}-minute walk
        from a park. Census estimates for the two groups:
      </p>
      <ul>
        {comparison.indicators.map((i) => (
          <li key={i.indicator}>{comparisonSentence(i, comparison.threshold_min)}</li>
        ))}
      </ul>
      <p className="note">
        {comparison.assumption} This describes where people live; it doesn't show why, and it says
        nothing about any individual household.
      </p>
    </section>
  );
}
