/**
 * The "touch grass" toy's physics: pure functions, no DOM, so they can be tested.
 *
 * Each blade is a damped spring around upright (angle 0, radians). The pointer pushes the
 * blades it passes through; a slow wind sways them. A blade counts as "touched" each time it
 * is pushed while near rest, so the counter keeps rising as long as you keep brushing.
 */

export type Blade = {
  x: number; // base position, px from the left
  h: number; // height, px
  w: number; // base width, px
  shade: number; // index into the palette
  phase: number; // wind phase offset
  a: number; // angle from upright, radians (positive leans right)
  v: number; // angular velocity, radians per frame
};

export const MAX_ANGLE = 1.15; // radians: blades bend, they don't fold flat
const STIFFNESS = 0.055; // spring pull back to upright, per frame
const DAMPING = 0.11; // per frame: a few visible sways, then rest
const DAMPING_REDUCED = 0.45; // reduced motion: settle without swaying back and forth
const REST_ANGLE = 0.09; // a blade this still counts as "at rest" and can be touched again
const REST_SPEED = 0.03;
const TOUCH_FORCE = 0.04; // the smallest push that counts as a touch

/** Small deterministic random generator (mulberry32), so the field is the same every load. */
export function seededRandom(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A field of blades across `width` px, at most `height` px tall. */
export function makeBlades(width: number, height: number, seed = 7): Blade[] {
  const rand = seededRandom(seed);
  const blades: Blade[] = [];
  const spacing = 5.5;
  for (let x = 2; x < width - 2; x += spacing * (0.6 + rand() * 0.8)) {
    blades.push({
      x,
      h: height * (0.35 + rand() * 0.6),
      w: 3 + rand() * 3,
      shade: Math.floor(rand() * 3),
      phase: rand() * Math.PI * 2,
      a: (rand() - 0.5) * 0.12,
      v: 0,
    });
  }
  return blades;
}

const atRest = (b: Blade) => Math.abs(b.a) < REST_ANGLE && Math.abs(b.v) < REST_SPEED;

/**
 * The pointer at (px, py), moving by dx px since the last event, in a strip `height` px tall
 * (y grows downward, the ground is at y = height). Blades within `radius` px whose tops reach
 * the pointer's height are pushed in the direction of travel (or away, for a tap).
 * Returns how many blades were newly touched.
 */
export function brush(
  blades: Blade[],
  px: number,
  py: number,
  dx: number,
  height: number,
  radius = 26,
): number {
  let touched = 0;
  for (const b of blades) {
    const dist = Math.abs(b.x - px);
    if (dist > radius) continue;
    if (py < height - b.h - 6) continue; // above this blade's tip
    const falloff = 1 - dist / radius;
    const dir = dx !== 0 ? Math.sign(dx) : Math.sign(b.x - px) || 1;
    const speed = dx !== 0 ? Math.min(Math.abs(dx), 40) / 40 : 0.6;
    const force = dir * falloff * (0.05 + 0.13 * speed);
    if (Math.abs(force) >= TOUCH_FORCE && atRest(b)) touched += 1;
    b.v += force;
  }
  return touched;
}

/**
 * Advance every blade by `frames` (1 = one 60 Hz frame; capped so a stalled tab doesn't
 * explode the springs). `time` is in ms, for the wind. Returns true while anything moves.
 */
export function step(
  blades: Blade[],
  frames: number,
  time: number,
  { wind = true, reducedMotion = false }: { wind?: boolean; reducedMotion?: boolean } = {},
): boolean {
  const f = Math.min(Math.max(frames, 0), 3);
  const damping = reducedMotion ? DAMPING_REDUCED : DAMPING;
  const breeze = wind && !reducedMotion;
  let moving = false;
  for (const b of blades) {
    const gust = breeze ? 0.0018 * Math.sin(time * 0.0011 + b.phase + b.x * 0.01) : 0;
    b.v += (-STIFFNESS * b.a - damping * b.v + gust) * f;
    b.a += b.v * f;
    if (b.a > MAX_ANGLE) [b.a, b.v] = [MAX_ANGLE, Math.min(b.v, 0)];
    if (b.a < -MAX_ANGLE) [b.a, b.v] = [-MAX_ANGLE, Math.max(b.v, 0)];
    if (Math.abs(b.a) > 0.002 || Math.abs(b.v) > 0.0005) moving = true;
  }
  return moving || breeze;
}

/** Text for the counter. */
export function touchedText(n: number): string {
  if (n === 0) return "Brush the grass with your pointer or finger.";
  return `You've touched ${n.toLocaleString("en-US")} blade${n === 1 ? "" : "s"} of grass.`;
}
