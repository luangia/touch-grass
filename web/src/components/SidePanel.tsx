/** The selected area's numbers, the clicked block, and where the data comes from. */

import type { AreaMetrics, Methodology, RegionDetail } from "../api/client";
import type { AreaProperties } from "../api/geojson";
import { displayName, int, meters, minutes, pct } from "../format";
import type { BlockProperties } from "../map/MapView";
import { type BlockClass, blockClassOf, SHARE_COLORS, shareClassIndex } from "../map/style";
import type { Mode } from "../state/url";

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
      <h2>{displayName(area.name, area.area_type)}</h2>
      {current ? (
        <p className="headline">
          <strong>{pct(current.pct_within)}</strong> of residents live within a {threshold}-minute
          walk of {PARKS}.
        </p>
      ) : null}
      <p className="muted">{int(area.pop_total)} residents (2020 Census)</p>
      <ul className="shares" aria-label="Share of residents by walk time">
        {area.thresholds.map((t) => (
          <li key={t.threshold_min} className={t.threshold_min === threshold ? "current" : ""}>
            <span className="label">Within a {t.threshold_min}-min walk</span>
            <span className="bar" aria-hidden="true">
              <span
                style={{
                  width: `${t.pct_within * 100}%`,
                  background: SHARE_COLORS[shareClassIndex(t.pct_within)],
                }}
              />
            </span>
            <span className="value">{pct(t.pct_within)}</span>
          </li>
        ))}
      </ul>
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

function BlockSection(props: Props & { block: BlockProperties }) {
  const { block, region } = props;
  const u = region.underserved_minutes;
  const line = region.thresholds.find((t) => t.minutes === u)?.meters ?? Infinity;
  const distance = block.reachable ? (block.network_m ?? null) : null;
  const cls = blockClassOf(props.blockClasses, distance);
  const parkCount = block.parks_reachable ?? 0;
  return (
    <section aria-label="Selected census block" className="block-section">
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
          <p className="headline">
            <span
              className="swatch"
              style={{ background: cls?.color ?? "transparent" }}
              aria-hidden="true"
            />
            <strong>{meters(distance)}</strong> to{" "}
            {block.nearest_park_name ? (
              <strong>{block.nearest_park_name}</strong>
            ) : (
              "the nearest park"
            )}
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
        </>
      ) : (
        <p className="headline">No walking route to a park on the mapped paths.</p>
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
