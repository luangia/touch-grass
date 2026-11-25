/** The selected area's numbers, the clicked block, and where the data comes from. */

import type { AreaMetrics, Methodology, RegionDetail } from "../api/client";
import type { AreaProperties } from "../api/geojson";
import { displayName, int, meters, minutes, pct, typeLabel } from "../format";
import type { BlockProperties } from "../map/MapView";
import { type BlockClass, blockClassOf } from "../map/style";
import { MAX_COMPARE, type Mode } from "../state/url";
import { WalkVsStraightBars } from "./charts";
import { ContextList, indicatorLabels } from "./context";

export type Selection = {
  areaId: string | null;
  metrics: AreaMetrics | null;
  loading: boolean;
  error: string | null;
  alsoIn: AreaProperties[];
  block: BlockProperties | null;
};

type Props = {
  region: RegionDetail;
  mode: Mode;
  threshold: number;
  blockClasses: BlockClass[];
  selection: Selection;
  hovered: AreaProperties | null;
  methodology: Methodology | null;
  /** City-wide figures for the start panel (null until loaded). */
  city: AreaMetrics | null;
  compare: string[];
  onToggleCompare: (areaId: string) => void;
  onSelectArea: (areaId: string) => void;
};

const PARKS = "a park or natural area";
const metersPerMinute = (r: RegionDetail) => (r.walk_speed_kmh * 1000) / 60;

function rankingFloor(m: Methodology | null): number | null {
  const metrics = m?.metrics as { metrics?: { min_residents_for_ranking?: number } } | null;
  return metrics?.metrics?.min_residents_for_ranking ?? null;
}

function AreaLink(props: { area: AreaProperties; onSelect: (id: string) => void }) {
  return (
    <button type="button" className="link" onClick={() => props.onSelect(props.area.area_id)}>
      {displayName(props.area.name, props.area.area_type)}
    </button>
  );
}

function AreaSection(props: Props & { area: AreaMetrics }) {
  const { area, region, threshold } = props;
  const floor = rankingFloor(props.methodology);
  const u = region.underserved_minutes;
  const current = area.thresholds.find((t) => t.threshold_min === threshold);
  return (
    <section aria-label="Selected area">
      <p className="overline">
        {area.area_type === "city" ? "Whole city" : typeLabel(area.area_type)}
      </p>
      <h2>{displayName(area.name, area.area_type)}</h2>
      {current ? (
        <p className="hero">
          <span className="hero-number">{pct(current.pct_within)}</span>{" "}
          <span className="hero-text">
            of residents live within a {threshold}-minute walk of {PARKS}.
          </span>
        </p>
      ) : null}
      <p className="muted">{int(area.pop_total)} residents (2020 Census)</p>
      <WalkVsStraightBars
        caption="Solid: by walking route. Faint: as the crow flies. The gap is what barriers cost."
        rows={area.thresholds.map((t) => ({
          label: `Within a ${t.threshold_min}-min walk`,
          walking: t.pct_within,
          straight: t.pct_within_straight ?? null,
          current: t.threshold_min === threshold,
        }))}
      />
      <dl>
        <dt>Farther than a {u}-minute walk</dt>
        <dd>
          {int(area.underserved_pop)} residents ({pct(area.underserved_share)})
        </dd>
        <dt>Typical resident's walk</dt>
        <dd>
          {meters(area.median_walk_m)} ({minutes(area.median_walk_m, metersPerMinute(region))}),
          population-weighted median
        </dd>
        <dt>Park area within a {u}-minute walk</dt>
        <dd>{area.reachable_park_ha_median.toFixed(1)} ha for the typical resident</dd>
      </dl>
      <ContextList values={area.context} labels={indicatorLabels(props.methodology)} />
      <CompareButton {...props} areaId={area.area_id} />
      {!area.ranked && area.area_type !== "city" ? (
        <p className="note">
          Fewer than {floor !== null ? int(floor) : "the minimum"} residents: shown on the map but
          left out of rankings, because a few people swing the share.
        </p>
      ) : null}
      {props.selection.alsoIn.length > 1 ? (
        <p className="note">
          This spot is in {props.selection.alsoIn.length} overlapping areas:{" "}
          {props.selection.alsoIn.map((a, i) => (
            <span key={a.area_id}>
              {i > 0 ? ", " : ""}
              {a.area_id === area.area_id ? (
                <strong>{displayName(a.name, a.area_type)}</strong>
              ) : (
                <AreaLink area={a} onSelect={props.onSelectArea} />
              )}
            </span>
          ))}
          . Shared blocks count in each.
        </p>
      ) : null}
    </section>
  );
}

function CompareButton(props: Props & { areaId: string }) {
  const picked = props.compare.includes(props.areaId);
  const full = !picked && props.compare.length >= MAX_COMPARE;
  return (
    <p>
      <button
        type="button"
        className="chip"
        aria-pressed={picked}
        disabled={full}
        onClick={() => props.onToggleCompare(props.areaId)}
      >
        {picked ? "✓ In compare" : "+ Add to compare"}
      </button>
      {full ? <span className="note"> Compare holds {MAX_COMPARE} areas.</span> : null}
    </p>
  );
}

/** "1.6× longer than a straight line (745 m)", or nothing when there's no usable ratio. */
export function detourText(walk: number, straight: number | undefined): string | null {
  if (straight === undefined || straight <= 0) return null;
  const ratio = walk / straight;
  return `${meters(straight)} in a straight line: the walk is ${ratio.toFixed(1)}× as long`;
}

function BlockSection(props: Props & { block: BlockProperties }) {
  const { block, region } = props;
  const u = region.underserved_minutes;
  const line = region.thresholds.find((t) => t.minutes === u)?.meters ?? Infinity;
  const distance = block.reachable ? (block.network_m ?? null) : null;
  const cls = blockClassOf(props.blockClasses, distance);
  const parkCount = block.parks_reachable ?? 0;
  return (
    <section aria-label="Selected census block" className="block-section">
      <p className="overline">Selected block</p>
      <h2>Census block</h2>
      {props.selection.alsoIn.length > 0 ? (
        <p className="muted">
          In{" "}
          {props.selection.alsoIn.map((a, i) => (
            <span key={a.area_id}>
              {i > 0 ? " and " : ""}
              <AreaLink area={a} onSelect={props.onSelectArea} />
            </span>
          ))}
        </p>
      ) : null}
      {distance !== null ? (
        <>
          <p className="hero">
            <span className="hero-number">
              <span
                className="swatch"
                style={{ background: cls?.color ?? "transparent" }}
                aria-hidden="true"
              />
              {meters(distance)}
            </span>{" "}
            <span className="hero-text">
              to{" "}
              {block.nearest_park_name ? (
                <strong>{block.nearest_park_name}</strong>
              ) : (
                "the nearest park"
              )}
            </span>
          </p>
          <p>
            About a {minutes(distance, metersPerMinute(region))} walk,{" "}
            {distance > line ? (
              <strong>farther than the {u}-minute line</strong>
            ) : (
              <>within the {u}-minute line</>
            )}
            .
          </p>
          {detourText(distance, block.straight_m) ? (
            <p className="muted">{detourText(distance, block.straight_m)}.</p>
          ) : null}
        </>
      ) : (
        <p className="hero-text">No walking route to a park on the mapped paths.</p>
      )}
      <p className="muted">
        {int(block.pop20)} residents ·{" "}
        {parkCount > 0
          ? `${parkCount} park${parkCount === 1 ? "" : "s"} within a ${u}-minute walk (${(block.reachable_park_ha ?? 0).toFixed(1)} ha)`
          : `no park within a ${u}-minute walk`}
      </p>
      <p className="note">
        Walks start from one point inside the block (Census block {block.geoid20}).
      </p>
    </section>
  );
}

export function SidePanel(props: Props) {
  const { selection, hovered, mode } = props;
  const area = selection.metrics ? <AreaSection {...props} area={selection.metrics} /> : null;
  const block = selection.block ? <BlockSection {...props} block={selection.block} /> : null;
  return (
    <aside className="panel" aria-live="polite">
      {selection.loading ? <p className="muted">Loading…</p> : null}
      {selection.error ? <p className="error">{selection.error}</p> : null}
      {/* Lead with what the current view is about. */}
      {mode === "blocks" ? (
        <>
          {block}
          {area}
        </>
      ) : (
        <>
          {area}
          {block}
        </>
      )}
      {!selection.metrics && !selection.loading && !selection.block ? (
        <section>
          <h2>Can residents walk to a park?</h2>
          {props.city
            ? (() => {
                const t = props.city.thresholds.find((x) => x.threshold_min === props.threshold);
                return t ? (
                  <p className="hero">
                    <span className="hero-number">{pct(t.pct_within)}</span>{" "}
                    <span className="hero-text">
                      of {props.region.name} residents live within a {props.threshold}-minute walk
                      of {PARKS}
                      {t.pct_within_straight !== null
                        ? `, against ${pct(t.pct_within_straight)} as the crow flies`
                        : ""}
                      .
                    </span>
                  </p>
                ) : null;
              })()
            : null}
          <p>
            {mode === "residents"
              ? `Colors show the share of residents who live within the selected walk of ${PARKS}. Search by name or click an area.`
              : `Colors show each census block's walk to the nearest park or natural area. Click a block to see its walk.`}
          </p>
          {hovered && mode === "residents" ? (
            <p className="muted">
              {displayName(hovered.name, hovered.area_type)}: {pct(hovered.pct_within)} of residents
              within a {props.threshold}-minute walk
            </p>
          ) : null}
        </section>
      ) : null}
      {props.methodology ? (
        <details className="about">
          <summary>About these numbers</summary>
          <ul>
            {props.methodology.caveats.map((c) => (
              <li key={c.id}>
                <strong>{c.title}</strong> {c.text}
              </li>
            ))}
          </ul>
          <h4>Data sources</h4>
          <ul className="sources">
            {[...new Set(props.methodology.sources.map((s) => s.attribution))].map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </aside>
  );
}
