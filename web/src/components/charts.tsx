/**
 * Small hand-written SVG charts (D-025 Q2). Rules: bars start at zero, shares share one
 * 0-100% axis, values are written on the bars, colors come from the map's palette.
 */

import { pct } from "../format";
import { SHARE_COLORS, shareClassIndex } from "../map/style";

/** Width (in %) of a share's bar on a 0-100% scale, clamped. Exported for tests. */
export function barWidth(share: number | null | undefined): number {
  if (share === null || share === undefined || Number.isNaN(share)) return 0;
  return Math.max(0, Math.min(100, share * 100));
}

export type ShareRow = {
  label: string;
  walking: number;
  straight: number | null;
  current?: boolean;
};

/**
 * Walking-route share as a solid bar, straight-line share as a faint bar behind it: the gap
 * between their ends is what barriers (freeways, rivers, hills, dead ends) cost (D-025 Q3).
 */
export function WalkVsStraightBars(props: { rows: ShareRow[]; caption: string }) {
  return (
    <figure className="walk-bars">
      <ul aria-label={props.caption}>
        {props.rows.map((r) => (
          <li key={r.label} className={r.current ? "current" : ""}>
            <span className="label">{r.label}</span>
            <span className="track" aria-hidden="true">
              {r.straight !== null ? (
                <span className="straight" style={{ width: `${barWidth(r.straight)}%` }} />
              ) : null}
              <span
                className="walking"
                style={{
                  width: `${barWidth(r.walking)}%`,
                  background: SHARE_COLORS[shareClassIndex(r.walking)],
                }}
              />
            </span>
            <span className="value">
              {pct(r.walking)}
              {r.straight !== null ? <span className="muted"> · {pct(r.straight)}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      <figcaption className="note">{props.caption}</figcaption>
    </figure>
  );
}

export type GroupedSeries = { label: string; values: number[] };

/** Areas × thresholds: one group per area, one bar per threshold, on a shared 0-100% axis. */
export function GroupedShareBars(props: {
  groups: GroupedSeries[];
  keys: string[];
  title: string;
}) {
  const barH = 14;
  const gap = 4;
  const groupGap = 18;
  const labelW = 150;
  const chartW = 420;
  const groupH = props.keys.length * (barH + gap) - gap;
  const height = props.groups.length * (groupH + groupGap) + 20;
  return (
    <figure className="grouped-bars">
      <svg
        viewBox={`0 0 ${labelW + chartW + 100} ${height}`}
        role="img"
        aria-label={props.title}
        width="100%"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1={labelW + t * chartW}
              x2={labelW + t * chartW}
              y1={0}
              y2={height - 18}
              className="gridline"
            />
            <text x={labelW + t * chartW} y={height - 4} className="axis" textAnchor="middle">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {props.groups.map((g, gi) => {
          const y0 = gi * (groupH + groupGap);
          return (
            <g key={g.label}>
              <text x={0} y={y0 + groupH / 2 + 4} className="group-label">
                {g.label}
              </text>
              {g.values.map((v, i) => {
                const y = y0 + i * (barH + gap);
                const w = (barWidth(v) / 100) * chartW;
                return (
                  <g key={props.keys[i]}>
                    <rect
                      x={labelW}
                      y={y}
                      width={w}
                      height={barH}
                      rx={3}
                      fill={SHARE_COLORS[shareClassIndex(v)]}
                    />
                    <text x={labelW + w + 6} y={y + barH - 3} className="bar-label">
                      {props.keys[i]}: {pct(v)}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <figcaption className="note">{props.title}</figcaption>
    </figure>
  );
}
