/**
 * About: what TouchGrass answers, how it's measured, what to keep in mind, and where the data
 * comes from. Every figure, parameter, caveat and source comes from the API, so this page
 * can't drift from what the map shows (D-024 amendment 2). Only the credits are fixed text.
 */

import type { AreaMetrics, Methodology, RegionDetail } from "../api/client";
import { int, pct } from "../format";

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
  const sources = methodology
    ? [...new Map(methodology.sources.map((s) => [s.attribution, s])).values()]
    : [];
  return (
    <main className="about-page">
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
                </li>
              ))}
              <li>Basemap: OpenFreeMap, © OpenMapTiles, data © OpenStreetMap contributors</li>
            </ul>
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
