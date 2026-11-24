/**
 * Light / dark theme (D-024 amendment 2). The first visit follows the system setting; the
 * toggle's choice is remembered in localStorage (a per-browser convenience: if storage is
 * unavailable, the system setting still applies). index.html sets the theme before first
 * paint so the page never flashes the wrong one.
 */

import type { Theme } from "./map/style";

export const THEME_KEY = "touchgrass-theme";

export function initialTheme(): Theme {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage blocked: fall through to the system setting */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme, remember: boolean): void {
  document.documentElement.dataset.theme = theme;
  if (!remember) return;
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* not remembered: fine */
  }
}
