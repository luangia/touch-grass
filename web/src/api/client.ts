/** Typed access to the TouchGrass API (DECISIONS.md D-023, D-024 Q3). */

import type { components } from "./schema";
import type { AreaCollection } from "./geojson";

type Schemas = components["schemas"];
export type RegionSummary = Schemas["RegionSummary"];
export type RegionDetail = Schemas["RegionDetail"];
export type AreaMetrics = Schemas["AreaMetrics"];
export type AreaMetricsResponse = Schemas["AreaMetricsResponse"];
export type Methodology = Schemas["Methodology"];
export type Threshold = Schemas["Threshold"];
export type Ranking = Schemas["Ranking"];
export type Comparison = Schemas["Comparison"];
export type ResidentComparison = Schemas["ResidentComparison"];
export type ContextValue = Schemas["ContextValue"];
export type RankMetric = "pct_within" | "underserved_pop" | "priority_score" | "barrier_gap";

export const API_URL: string = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, signal ? { signal } : {});
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, `cannot reach the API at ${API_URL}`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** Statuses a sleeping free-tier host can answer with while it wakes (D-026 Q1). */
const TRANSIENT = new Set([0, 502, 503, 504]);

/**
 * Retry a request while the API host wakes up: a network failure or a 502/503/504 is tried
 * again after `delayMs`, up to `attempts` times in all. Any other error is final.
 */
export async function withWakeRetry<T>(
  request: () => Promise<T>,
  {
    attempts = 6,
    delayMs = 5000,
    signal,
  }: { attempts?: number; delayMs?: number; signal?: AbortSignal } = {},
): Promise<T> {
  for (let i = 1; ; i++) {
    try {
      return await request();
    } catch (err) {
      const transient = err instanceof ApiError && TRANSIENT.has(err.status);
      if (!transient || i >= attempts || signal?.aborted) throw err;
      await new Promise<void>((resolve) => setTimeout(resolve, delayMs));
      if (signal?.aborted) throw err;
    }
  }
}

/** True when the app talks to an API on this machine (a dev setup, not a deployment). */
export const API_IS_LOCAL = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(API_URL);

const region = (id: string) => `/api/v1/regions/${encodeURIComponent(id)}`;

export const api = {
  regions: (signal?: AbortSignal) => getJson<RegionSummary[]>("/api/v1/regions", signal),
  region: (id: string, signal?: AbortSignal) => getJson<RegionDetail>(region(id), signal),
  methodology: (id: string, signal?: AbortSignal) =>
    getJson<Methodology>(`${region(id)}/methodology`, signal),
  areas: (id: string, areaType: string, threshold: number, signal?: AbortSignal) =>
    getJson<AreaCollection>(
      `${region(id)}/areas?type=${encodeURIComponent(areaType)}&threshold=${threshold}`,
      signal,
    ),
  metrics: (id: string, areaId: string, signal?: AbortSignal) =>
    getJson<AreaMetricsResponse>(
      `${region(id)}/areas/${encodeURIComponent(areaId)}/metrics`,
      signal,
    ),
  rankings: (
    id: string,
    q: { areaType: string; metric: RankMetric; threshold: number; limit: number },
    signal?: AbortSignal,
  ) =>
    getJson<Ranking>(
      `${region(id)}/rankings?type=${encodeURIComponent(q.areaType)}&metric=${q.metric}` +
        `&threshold=${q.threshold}&limit=${q.limit}`,
      signal,
    ),
  compare: (id: string, areaIds: string[], signal?: AbortSignal) =>
    getJson<Comparison>(
      `${region(id)}/compare?area_ids=${areaIds.map(encodeURIComponent).join(",")}`,
      signal,
    ),
  parksUrl: (id: string) => `${API_URL}${region(id)}/parks`,
  blockTilesUrl: (id: string) => `${API_URL}${region(id)}/tiles/blocks/{z}/{x}/{y}.pbf`,
};
