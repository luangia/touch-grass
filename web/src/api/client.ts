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
  parksUrl: (id: string) => `${API_URL}${region(id)}/parks`,
  blockTilesUrl: (id: string) => `${API_URL}${region(id)}/tiles/blocks/{z}/{x}/{y}.pbf`,
};
