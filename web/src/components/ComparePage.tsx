/**
 * Compare 2-4 areas (D-025 Q6): a grouped bar chart across walk times, then a table with the
 * walking and straight-line shares, underserved residents, the typical walk, reachable park
 * area and census context (with its reliability). The selection lives in the URL.
 */

import { useEffect, useState } from "react";

import { type AreaMetrics, api, type Methodology, type RegionDetail } from "../api/client";
import type { AreaFeature } from "../api/geojson";
import { displayName, int, meters, pct, typeLabel } from "../format";
import { MAX_COMPARE } from "../state/url";
import { GroupedShareBars } from "./charts";
import { estimateText, indicatorLabels } from "./context";
import { SearchBox } from "./Controls";

type Props = {
  region: RegionDetail;
  methodology: Methodology | null;
  areaType: string;
  features: AreaFeature[];
  selected: string[];
  onChange: (ids: string[]) => void;
  onOpenArea: (areaId: string) => void;
};

export function ComparePage(props: Props) {
  const { region, selected } = props;
  const [areas, setAreas] = useState<AreaMetrics[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (selected.length < 2) {
      setAreas(null);
      return;
    }
    const ctl = new AbortController();
    setError(null);
    api
      .compare(region.region_id, selected, ctl.signal)
      .then((c) => setAreas(c.areas))
      .catch((e: unknown) => ctl.signal.aborted || setError(String((e as Error).message ?? e)));
    return () => ctl.abort();
  }, [region.region_id, selected.join(",")]);

  const name = (a: { name: string; area_type: string }) => displayName(a.name, a.area_type);
  const featureName = (id: string) => {
    const f = props.features.find((x) => x.id === id);
    return f ? name(f.properties) : id === "city" ? region.name : id;
  };
  const labels = indicatorLabels(props.methodology);
  const u = region.underserved_minutes;
  const full = selected.length >= MAX_COMPARE;
  const choices = props.features.filter((f) => !selected.includes(f.id));

  return (
    <main className="page">
      <article className="wide">
        <p className="overline">Compare</p>
        <h1>How do these places compare?</h1>
        <p className="lead">
          Pick 2 to {MAX_COMPARE} areas. Add one from here, or use "Add to compare" on the map.
        </p>
        <div className="page-controls">
          {full ? (
            <p className="note">
              Four areas is the most that compare clearly. Remove one to add another.
            </p>
          ) : (
            <SearchBox
              features={choices}
              areaType={props.areaType}
              onSelect={(id) => props.onChange([...selected, id])}
            />
          )}
          <button
            type="button"
            className="chip"
            disabled={selected.includes("city") || full}
            onClick={() => props.onChange([...selected, "city"])}
          >
            + Whole city
          </button>
        </div>
        <ul className="chips" aria-label="Areas being compared">
          {selected.map((id) => (
            <li key={id} className="chip">
              {featureName(id)}
              <button
                type="button"
                aria-label={`Remove ${featureName(id)}`}
                onClick={() => props.onChange(selected.filter((x) => x !== id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        {selected.length < 2 ? (
          <p className="empty">
            {selected.length === 0 ? "No areas picked yet." : "Pick one more area to compare."}
          </p>
        ) : null}
        {error ? <p className="error">{error}</p> : null}
        {areas ? (
          <>
            <GroupedShareBars
              title={`Share of residents within each walk time of a park or natural area`}
              keys={region.thresholds.map((t) => `${t.minutes} min`)}
              groups={areas.map((a) => ({
                label: name(a),
                values: a.thresholds.map((t) => t.pct_within),
              }))}
            />
            <div className="table-scroll">
              <table className="compare-table">
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="sr-only">Measure</span>
                    </th>
                    {areas.map((a) => (
                      <th key={a.area_id} scope="col">
                        <button
                          type="button"
                          className="link"
                          onClick={() => props.onOpenArea(a.area_id)}
                        >
                          {name(a)}
                        </button>
                        <span className="muted small">
                          {" "}
                          {a.area_type === "city"
                            ? "whole city"
                            : typeLabel(a.area_type).toLowerCase()}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Residents</th>
                    {areas.map((a) => (
                      <td key={a.area_id}>{int(a.pop_total)}</td>
                    ))}
                  </tr>
                  {region.thresholds.map((t, i) => (
                    <tr key={t.minutes}>
                      <th scope="row">
                        Within a {t.minutes}-min walk <span className="muted">· straight line</span>
                      </th>
                      {areas.map((a) => {
                        const s = a.thresholds[i];
                        return (
                          <td key={a.area_id}>
                            {pct(s?.pct_within)}
                            <span className="muted"> · {pct(s?.pct_within_straight ?? null)}</span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  <tr>
                    <th scope="row">Farther than {u} min</th>
                    {areas.map((a) => (
                      <td key={a.area_id}>
                        {int(a.underserved_pop)}{" "}
                        <span className="muted">({pct(a.underserved_share)})</span>
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <th scope="row">Typical walk</th>
                    {areas.map((a) => (
                      <td key={a.area_id}>{meters(a.median_walk_m)}</td>
                    ))}
                  </tr>
                  <tr>
                    <th scope="row">Park area within {u} min</th>
                    {areas.map((a) => (
                      <td key={a.area_id}>{a.reachable_park_ha_median.toFixed(1)} ha</td>
                    ))}
                  </tr>
                  {(areas[0]?.context ?? []).map((c) => (
                    <tr key={c.indicator}>
                      <th scope="row">
                        {labels[c.indicator] ?? c.indicator}
                        <span className="muted small"> (census estimate)</span>
                      </th>
                      {areas.map((a) => {
                        const v = a.context.find((x) => x.indicator === c.indicator);
                        return (
                          <td key={a.area_id} className={v ? `ctx-${v.reliability}` : ""}>
                            {v ? estimateText(v) : "n/a"}
                            {v?.reliability === "low" ? (
                              <span className="badge badge-low">unreliable</span>
                            ) : null}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">
              Census figures are context, not cause, and carry margins of error; unreliable values
              are greyed. Overlapping areas share residents.
            </p>
          </>
        ) : null}
      </article>
    </main>
  );
}
