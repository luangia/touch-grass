/** The selected area's numbers, the clicked block, and where the data comes from. */

import type { AreaMetrics, Methodology, RegionDetail } from "../api/client";
import type { AreaProperties } from "../api/geojson";
import { displayName, int, meters, minutes, pct } from "../format";
import type { BlockProperties } from "../map/MapView";
import { SHARE_COLORS, shareClassIndex } from "../map/style";

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
  threshold: number;
  selection: Selection;
  hovered: AreaProperties | null;
  methodology: Methodology | null;
  onSelectArea: (areaId: string) => void;
};

const metersPerMinute = (r: RegionDetail) => (r.walk_speed_kmh * 1000) / 60;

function rankingFloor(m: Methodology | null): number | null {
  const metrics = m?.metrics as { metrics?: { min_residents_for_ranking?: number } } | null;
  return metrics?.metrics?.min_residents_for_ranking ?? null;
}

function AreaSection(props: Props & { area: AreaMetrics }) {
  const { area, region, threshold } = props;
  const floor = rankingFloor(props.methodology);
  const u = region.underserved_minutes;
  return (
    <section aria-label="Selected area">
      <h2>{displayName(area.name, area.area_type)}</h2>
      <p className="muted">{int(area.pop_total)} residents (2020 Census)</p>
      <ul className="shares">
        {area.thresholds.map((t) => (
          <li key={t.threshold_min} className={t.threshold_min === threshold ? "current" : ""}>
            <span className="label">Within {t.threshold_min} min</span>
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
        <dt>Beyond a {u}-minute walk</dt>
        <dd>
          {int(area.underserved_pop)} residents ({pct(area.underserved_share)})
        </dd>
        <dt>Typical resident's walk</dt>
        <dd>
          {meters(area.median_walk_m)} ({minutes(area.median_walk_m, metersPerMinute(region))}),
          population-weighted median
        </dd>
        <dt>Park area within {u} min</dt>
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
                <button
                  type="button"
                  className="link"
                  onClick={() => props.onSelectArea(a.area_id)}
                >
                  {displayName(a.name, a.area_type)}
                </button>
              )}
            </span>
          ))}
          . Shared blocks count in each.
        </p>
      ) : null}
    </section>
  );
}

function BlockSection({ block, region }: { block: BlockProperties; region: RegionDetail }) {
  const u = region.underserved_minutes;
  return (
    <section aria-label="Selected block">
      <h3>This census block</h3>
      {block.reachable && block.network_m !== null ? (
        <p>
          {int(block.pop20)} residents. Walk to the nearest park:{" "}
          <strong>{meters(block.network_m)}</strong> (about{" "}
          {minutes(block.network_m, metersPerMinute(region))}).{" "}
          {block.parks_reachable
            ? `${block.parks_reachable} park${block.parks_reachable === 1 ? "" : "s"} within ${u} min (${(block.reachable_park_ha ?? 0).toFixed(1)} ha).`
            : `No park within ${u} min.`}
        </p>
      ) : (
        <p>{int(block.pop20)} residents. No walking route to a park on the mapped paths.</p>
      )}
    </section>
  );
}

export function SidePanel(props: Props) {
  const { selection, hovered } = props;
  return (
    <aside className="panel" aria-live="polite">
      {selection.loading ? <p className="muted">Loading…</p> : null}
      {selection.error ? <p className="error">{selection.error}</p> : null}
      {selection.metrics ? <AreaSection {...props} area={selection.metrics} /> : null}
      {selection.block ? <BlockSection block={selection.block} region={props.region} /> : null}
      {!selection.metrics && !selection.loading && !selection.block ? (
        <section>
          <h2>Can residents walk to a park?</h2>
          <p>
            Search by name or click the map. Colors show the share of residents within the selected
            walk time; zoom in to see each block's walk to the nearest park.
          </p>
          {hovered ? (
            <p className="muted">
              {displayName(hovered.name, hovered.area_type)}: {pct(hovered.pct_within)} within{" "}
              {props.threshold} min
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
