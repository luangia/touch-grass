/** View state ⇄ URL query string (D-024 Q8): links reproduce a view; back/forward work. */

/** What the map colors: residents near a park (by area), or the walk from each block. */
export type Mode = "residents" | "blocks";
export const MODES: Mode[] = ["residents", "blocks"];

export type ViewState = {
  mode: Mode | null;
  region: string | null;
  areaType: string | null;
  threshold: number | null;
  area: string | null;
};

export const EMPTY: ViewState = {
  mode: null,
  region: null,
  areaType: null,
  threshold: null,
  area: null,
};

export function parseView(search: string): ViewState {
  const q = new URLSearchParams(search);
  const t = q.get("threshold");
  const threshold = t !== null && /^\d+$/.test(t) ? Number(t) : null;
  const m = q.get("view");
  return {
    mode: MODES.includes(m as Mode) ? (m as Mode) : null,
    region: q.get("region") || null,
    areaType: q.get("type") || null,
    threshold,
    area: q.get("area") || null,
  };
}

export function formatView(view: ViewState): string {
  const q = new URLSearchParams();
  if (view.region) q.set("region", view.region);
  if (view.mode) q.set("view", view.mode);
  if (view.areaType) q.set("type", view.areaType);
  if (view.threshold !== null) q.set("threshold", String(view.threshold));
  if (view.area) q.set("area", view.area);
  const s = q.toString();
  return s ? `?${s}` : "";
}
