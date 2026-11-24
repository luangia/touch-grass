/** Display helpers: numbers and names exactly as the panel and tooltips show them. */

export const pct = (x: number | null | undefined, digits = 0) =>
  x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(digits)}%`;

export const int = (x: number | null | undefined) =>
  x === null || x === undefined ? "n/a" : Math.round(x).toLocaleString("en-US");

export const meters = (x: number | null | undefined) =>
  x === null || x === undefined ? "no route" : `${Math.round(x).toLocaleString("en-US")} m`;

export const minutes = (m: number | null | undefined, metersPerMinute: number) =>
  m === null || m === undefined ? "no route" : `${Math.round(m / metersPerMinute)} min`;

/** "NORTH TABOR" → "North Tabor"; mixed-case names are left alone. Area ids stay digits. */
export function displayName(name: string, areaType: string): string {
  if (/^\d+$/.test(name)) return `${typeLabel(areaType)} ${name}`;
  if (name !== name.toUpperCase()) return name;
  return name
    .toLowerCase()
    .replace(/(^|[\s\-/(.])([a-z])/g, (_, p: string, c: string) => p + c.toUpperCase());
}

export const typeLabel = (areaType: string, plural = false) => {
  const s = areaType.replace(/_/g, " ");
  const cap = s.charAt(0).toUpperCase() + s.slice(1);
  return plural ? `${cap}s` : cap;
};
