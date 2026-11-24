/**
 * How far the map may pan: the region's box plus a margin in kilometres (D-024 amendment).
 * Derived from the API's bbox, so it works for any region and names no place. A 600 km margin
 * takes in a city's whole state, its coast and into the neighbouring states, but not the world
 * (400 km, tried first, cut off the far side of a large state).
 */

/** [west, south, east, north] in degrees. */
export type Bounds = [number, number, number, number];

export const MAX_PAN_KM = 600;
const KM_PER_DEGREE_LAT = 111.32;

export function expandBounds(
  [west, south, east, north]: Bounds,
  km: number,
): [[number, number], [number, number]] {
  const dLat = km / KM_PER_DEGREE_LAT;
  // A degree of longitude shrinks with latitude; use the box edge nearest the pole so the
  // margin is at least `km` everywhere.
  const poleward = Math.min(89, Math.max(Math.abs(south), Math.abs(north)));
  const dLon = km / (KM_PER_DEGREE_LAT * Math.cos((poleward * Math.PI) / 180));
  const clampLat = (x: number) => Math.max(-85, Math.min(85, x)); // Web Mercator's limit
  const clampLon = (x: number) => Math.max(-180, Math.min(180, x));
  return [
    [clampLon(west - dLon), clampLat(south - dLat)],
    [clampLon(east + dLon), clampLat(north + dLat)],
  ];
}
