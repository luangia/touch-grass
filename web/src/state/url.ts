/** View state ⇄ URL query string (D-024 Q8): links reproduce a view; back/forward work. */

/** What the map colors: residents near a park (by area), or the walk from each block. */
export type Mode = "residents" | "blocks";
export const MODES: Mode[] = ["residents", "blocks"];

export type ViewState = {
  mode: Mode | null;
  /** Areas picked for the Compare page (D-025 Q6). */
  compare: string[];
  region: string | null;
  areaType: string | null;
  threshold: number | null;
  area: string | null;
};

export const MAX_COMPARE = 4;

export const EMPTY: ViewState = {
  mode: null,
  compare: [],
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
  const compare = (q.get("compare") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return {
    mode: MODES.includes(m as Mode) ? (m as Mode) : null,
    compare: [...new Set(compare)].slice(0, MAX_COMPARE),
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
  if (view.compare.length) q.set("compare", view.compare.join(","));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Pages by path, no router library needed (D-025 Q1). */
export type Page = "map" | "rankings" | "compare" | "about";
export const PAGES: Page[] = ["map", "rankings", "compare", "about"];

/**
 * The path the app is served under: "/" locally, "/touch-grass/" on GitHub Pages (D-026 Q3).
 * Vite sets BASE_URL from `base` in vite.config.ts; it always ends with "/".
 */
export const BASE: string = import.meta.env.BASE_URL ?? "/";

function normalizeBase(base: string): string {
  const b = base.startsWith("/") ? base : `/${base}`;
  return b.endsWith("/") ? b : `${b}/`;
}

export function pageFromPath(pathname: string, base: string = BASE): Page {
  const b = normalizeBase(base);
  const path = `${pathname}/`.startsWith(b) ? `/${pathname.slice(b.length)}` : null;
  const m = path?.match(/^\/(rankings|compare|about)\/?$/);
  return m ? (m[1] as Page) : "map";
}

export function pathFor(page: Page, base: string = BASE): string {
  const b = normalizeBase(base);
  return page === "map" ? b : `${b}${page}`;
}
