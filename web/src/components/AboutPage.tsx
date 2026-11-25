/**
 * About: what TouchGrass answers, how it's measured, what to keep in mind, and where the data
 * comes from. Every figure, parameter, caveat and source comes from the API, so this page
 * can't drift from what the map shows (D-024 amendment 2). Only the credits are fixed text.
 */

import type { AreaMetrics, Methodology, RegionDetail } from "../api/client";
import { int, pct } from "../format";
import { ResidentComparisonBlock } from "./context";

export const AUTHOR = { name: "Luan Pham", url: "https://luangia.github.io" };

type Props = {
  region: RegionDetail;
  methodology: Methodology | null;
  city: AreaMetrics | null;
  onOpenMap: () => void;
};

function rankingFloor(m: Methodology | null): number | null {
  const metrics = m?.metrics as { metrics?: { min_residents_for_ranking?: number } } | null;
  return metrics?.metrics?.min_residents_for_ranking ?? null;
}

const list = (xs: string[]) =>
  xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

export function AboutPage({ region, methodology, city, onOpenMap }: Props) {
  const u = region.underserved_minutes;
  const minutes = region.thresholds.map((t) => `${t.minutes}`);
  const atU = city?.thresholds.find((t) => t.threshold_min === u);
  const floor = rankingFloor(methodology);
  // One row per distinct credit + licence (several ACS tables share both).
  const sources = methodology
    ? [
        ...new Map(
          methodology.sources.map((s) => [`${s.attribution}|${s.license_name}`, s]),
        ).values(),
      ]
    : [];
  const params = parameterRows(methodology);
  return (
    <main className="page about-page">
      <article>
        <p className="overline">About</p>
        <h1>How far is a park, really?</h1>
        <p className="lead">
          TouchGrass measures how many residents of {region.name} can walk to a park or natural area
          along real streets and paths (not as the crow flies), and shows it neighborhood by
          neighborhood and block by block.
        </p>

        {city && atU ? (
          <dl className="stats" aria-label={`${region.name} at a glance`}>
            <div>
              <dt>Residents</dt>
              <dd>{int(city.pop_total)}</dd>
            </div>
            <div>
              <dt>Within a {u}-minute walk</dt>
              <dd>{pct(atU.pct_within)}</dd>
            </div>
            <div>
              <dt>Farther than {u} minutes</dt>
              <dd>{int(city.underserved_pop)}</dd>
            </div>
            {atU.pct_within_straight !== null ? (
              <div>
                <dt>Within {u} minutes as the crow flies</dt>
                <dd>{pct(atU.pct_within_straight)}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}

        <h2>How it's measured</h2>
        <ol className="steps">
          <li>
            <strong>Parks and natural areas</strong> come from OpenStreetMap, filtered by the
            project's rules (what's left out is listed below) and compared with an official local
            inventory where one exists.
          </li>
          <li>
            <strong>The walking network</strong> is OpenStreetMap's sidewalks, paths and streets
            open to people on foot. A park is reached where a path meets its edge or a mapped
            entrance.
          </li>
          <li>
            <strong>Every census block</strong> (2020 Census) gets the shortest walk from one point
            inside it to the nearest park, at {region.walk_speed_kmh} km/h.
          </li>
          <li>
            <strong>Residents are counted</strong>, not land or blocks: each area's share is the
            share of its residents within a {list(minutes)}-minute walk. Anyone farther than {u}{" "}
            minutes counts as underserved.
            {floor !== null
              ? ` Areas with fewer than ${int(floor)} residents are shown but not ranked.`
              : ""}
          </li>
        </ol>

        {city ? (
          <>
            <h2>Walking route vs straight line</h2>
            <p>
              Distance as the crow flies flatters access: freeways, rivers, hills and dead-end
              streets make real walks longer. Share of residents within each walk:
            </p>
            <table className="mini-table">
              <thead>
                <tr>
                  <th scope="col">Walk time</th>
                  <th scope="col">By walking route</th>
                  <th scope="col">As the crow flies</th>
                </tr>
              </thead>
              <tbody>
                {city.thresholds.map((t) => (
                  <tr key={t.threshold_min}>
                    <th scope="row">{t.threshold_min} minutes</th>
                    <td>{pct(t.pct_within, 1)}</td>
                    <td>{pct(t.pct_within_straight ?? null, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">
              The straight line runs to the nearest edge of a park, so it's a floor on any walk. The
              Rankings page lists the places where the gap is largest.
            </p>
          </>
        ) : null}

        {methodology?.resident_comparison?.indicators.length ? (
          <>
            <h2>Who lives farther from parks</h2>
            <ResidentComparisonBlock comparison={methodology.resident_comparison} />
          </>
        ) : null}

        {methodology?.caveats.length ? (
          <>
            <h2>What to keep in mind</h2>
            <ul className="caveats">
              {methodology.caveats.map((c) => (
                <li key={c.id}>
                  <strong>{c.title}</strong> {c.text}
                </li>
              ))}
            </ul>
          </>
        ) : null}

        {sources.length ? (
          <>
            <h2>Data sources</h2>
            <ul className="sources">
              {sources.map((s) => (
                <li key={s.source_id}>
                  {s.attribution}
                  {s.vintage ? <span className="muted"> · {s.vintage}</span> : null}
                  <br />
                  <span className="muted small">
                    Licence:{" "}
                    {s.license_url ? (
                      <a href={s.license_url} target="_blank" rel="noopener noreferrer">
                        {s.license_name}
                      </a>
                    ) : (
                      s.license_name
                    )}
                  </span>
                </li>
              ))}
              <li>
                Basemap: OpenFreeMap, © OpenMapTiles, data © OpenStreetMap contributors
                <br />
                <span className="muted small">
                  Licence:{" "}
                  <a
                    href="https://opendatacommons.org/licenses/odbl/1-0/"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open Database License (ODbL) 1.0
                  </a>{" "}
                  (map data)
                </span>
              </li>
            </ul>
          </>
        ) : null}

        {params.length ? (
          <>
            <h2>Parameters</h2>
            <table className="mini-table">
              <tbody>
                {params.map(([label, value]) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    <td>{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <details className="raw-params">
              <summary>Every parameter, as recorded with the run</summary>
              <pre>
                {JSON.stringify(
                  { analysis: methodology?.analysis, metrics: methodology?.metrics },
                  null,
                  2,
                )}
              </pre>
            </details>
          </>
        ) : null}

        <h2>Credits</h2>
        <p>
          Built by{" "}
          <a href={AUTHOR.url} target="_blank" rel="noopener noreferrer">
            {AUTHOR.name}
          </a>
          .
        </p>
        <p className="muted small">
          Analysis run {region.latest_run_id ?? "n/a"}
          {region.acs_vintage ? ` · ACS ${region.acs_vintage}` : ""}
          {region.osm_snapshot ? ` · OpenStreetMap snapshot ${region.osm_snapshot}` : ""}
        </p>

        <p>
          <button type="button" className="button-primary" onClick={onOpenMap}>
            Open the map
          </button>
        </p>
      </article>
    </main>
  );
}

type Params = Record<string, unknown>;

/** The parameters a reader needs, labelled, from the run's own record (not from code). */
export function parameterRows(m: Methodology | null): [string, string][] {
  if (!m) return [];
  const a = m.analysis as Params;
  const metrics = ((m.metrics as Params | null)?.metrics ?? {}) as Params;
  const rows: [string, string][] = [];
  const add = (label: string, v: unknown, fmt: (x: never) => string = String) => {
    if (v !== undefined && v !== null) rows.push([label, fmt(v as never)]);
  };
  add("Walking speed", a.walk_speed_kmh, (x: number) => `${x} km/h`);
  add("Walk times", a.thresholds_min, (x: number[]) => x.map((t) => `${t} min`).join(", "));
  add("Underserved beyond", metrics.underserved_minutes, (x: number) => `a ${x}-minute walk`);
  add("A path reaches a park within", a.access_tolerance_m, (x: number) => `${x} m of its edge`);
  add("Largest path gap bridged to a park", a.max_park_gap_m, (x: number) => `${x} m`);
  add("Fewest residents to be ranked", metrics.min_residents_for_ranking, (x: number) => int(x));
  add(
    "Census reliability (coefficient of variation)",
    metrics.cv_high_max,
    (x: number) =>
      `high below ${Math.round(x * 100)}%, medium up to ${Math.round(Number(metrics.cv_medium_max) * 100)}%, low above`,
  );
  return rows;
}
