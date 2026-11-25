/**
 * Rankings (D-025 Q7): four titled lists, each answering one question. Rates skip areas below
 * the resident floor (D-022 Q2); census context is never ranked (D-022 Q7). Equal displayed
 * values are shown as ties. Every row opens the area on the map.
 */

import { useEffect, useState } from "react";

import {
  api,
  type Methodology,
  type RankMetric,
  type Ranking,
  type RegionDetail,
} from "../api/client";
import { displayName, int, pct, typeLabel } from "../format";
import { AreaTypeToggle, ThresholdToggle } from "./Controls";

type Props = {
  region: RegionDetail;
  methodology: Methodology | null;
  areaType: string;
  threshold: number;
  onAreaType: (t: string) => void;
  onThreshold: (t: number) => void;
  onOpenArea: (areaId: string) => void;
};

const SHORT = 10;
const ALL = 100;

/** Rank labels with ties: equal displayed values share a rank, later rows show "=". */
export function tieRanks(display: string[]): string[] {
  return display.map((d, i) => {
    if (i > 0 && d === display[i - 1]) return "=";
    return String(i + 1);
  });
}

type ListSpec = {
  metric: RankMetric;
  title: (t: number, u: number, type: string) => string;
  note: string;
  value: (v: number) => string;
  atUnderserved: boolean;
};

const LISTS: ListSpec[] = [
  {
    metric: "pct_within",
    title: (t, _u, type) =>
      `${typeLabel(type, true)} where the fewest residents live within a ${t}-minute walk of a park`,
    note: "Share of residents. Areas below the resident floor are left out: a few people swing the share.",
    value: (v) => pct(v),
    atUnderserved: false,
  },
  {
    metric: "underserved_pop",
    title: (_t, u, type) =>
      `${typeLabel(type, true)} with the most residents farther than a ${u}-minute walk`,
    note: "Number of people, so large areas lead: the scale of need, not the rate.",
    value: (v) => int(v),
    atUnderserved: true,
  },
  {
    metric: "priority_score",
    title: (_t, _u, type) =>
      `${typeLabel(type, true)} where underserved residents are most concentrated`,
    note: "Residents farther than a {u}-minute walk per km² of populated land, among areas above the resident floor.",
    value: (v) => `${int(v)} per km²`,
    atUnderserved: true,
  },
  {
    metric: "barrier_gap",
    title: (t, _u, type) =>
      `${typeLabel(type, true)} where barriers cost the most (${t}-minute walk)`,
    note: "Points between the share within reach as the crow flies and by walking route: freeways, rivers, hills and dead-end streets.",
    value: (v) => `${(v * 100).toFixed(0)} points`,
    atUnderserved: false,
  },
];

function RankingList(props: Props & { spec: ListSpec }) {
  const { spec, region } = props;
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<Ranking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const t = spec.atUnderserved ? region.underserved_minutes : props.threshold;
  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    api
      .rankings(
        region.region_id,
        {
          areaType: props.areaType,
          metric: spec.metric,
          threshold: t,
          limit: expanded ? ALL : SHORT,
        },
        ctl.signal,
      )
      .then(setData)
      .catch((e: unknown) => ctl.signal.aborted || setError(String((e as Error).message ?? e)));
    return () => ctl.abort();
  }, [region.region_id, props.areaType, spec.metric, t, expanded]);
  const shown = data?.entries.map((e) => spec.value(e.value)) ?? [];
  const ranks = tieRanks(shown);
  return (
    <section
      className="ranking"
      aria-label={spec.title(t, region.underserved_minutes, props.areaType)}
    >
      <h2>{spec.title(t, region.underserved_minutes, props.areaType)}</h2>
      <p className="note">{spec.note.replace("{u}", String(region.underserved_minutes))}</p>
      {error ? <p className="error">{error}</p> : null}
      <ol className="rank-rows">
        {data?.entries.map((e, i) => (
          <li key={e.area_id}>
            <span className="rank" aria-hidden="true">
              {ranks[i]}
            </span>
            <button type="button" className="rank-name" onClick={() => props.onOpenArea(e.area_id)}>
              {displayName(e.name, props.areaType)}
            </button>
            <span className="rank-value">{shown[i]}</span>
            <span className="rank-context muted">
              {int(e.pop_total)} residents · {pct(e.pct_within)} within {t} min
            </span>
          </li>
        ))}
      </ol>
      {data && (data.entries.length === SHORT || expanded) ? (
        <button type="button" className="link" onClick={() => setExpanded((x) => !x)}>
          {expanded ? "Show top 10" : "Show all"}
        </button>
      ) : null}
    </section>
  );
}

export function RankingsPage(props: Props) {
  return (
    <main className="page">
      <article className="wide">
        <p className="overline">Rankings</p>
        <h1>Which places are farthest from a park?</h1>
        <p className="lead">
          Four ways to rank, each answering a different question. Click a name to see it on the map.
        </p>
        <div className="page-controls">
          <ThresholdToggle
            thresholds={props.region.thresholds}
            value={props.threshold}
            onChange={props.onThreshold}
          />
          <AreaTypeToggle
            types={props.region.area_types}
            value={props.areaType}
            onChange={props.onAreaType}
          />
        </div>
        <div className="ranking-grid">
          {LISTS.map((spec) => (
            <RankingList key={spec.metric} {...props} spec={spec} />
          ))}
        </div>
      </article>
    </main>
  );
}
