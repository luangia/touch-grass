/**
 * "Touch grass": a strip of grass to brush while a sleeping server wakes up.
 *
 * Shown only on a slow first load (with the wake notice), so it costs nothing on a fast one.
 * No data and no requests: the physics is in ./grass.ts; this component draws it on a canvas
 * and turns pointer and keyboard input into pushes. The animation loop pauses whenever the
 * grass is still, and wind is off for people who prefer reduced motion.
 */

import { useEffect, useRef, useState } from "react";

import { type Blade, brush, makeBlades, step, touchedText } from "./grass";

const PALETTE = {
  light: ["#2f7d55", "#4c9a6a", "#7cc596"],
  dark: ["#4fa872", "#72c999", "#3d8a5e"], // all clearly brighter than the dark background
};

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

function draw(ctx: CanvasRenderingContext2D, blades: Blade[], width: number, height: number): void {
  const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const colors = PALETTE[theme];
  ctx.clearRect(0, 0, width, height);
  for (const b of blades) {
    const tipX = b.x + Math.sin(b.a) * b.h;
    const tipY = height - Math.cos(b.a) * b.h;
    // The bend is stronger near the tip: put the curve's control point at half the angle.
    const cx = b.x + Math.sin(b.a * 0.5) * b.h * 0.55;
    const cy = height - Math.cos(b.a * 0.5) * b.h * 0.55;
    ctx.fillStyle = colors[b.shade % colors.length]!;
    ctx.beginPath();
    ctx.moveTo(b.x - b.w / 2, height);
    ctx.quadraticCurveTo(cx - b.w * 0.3, cy, tipX, tipY);
    ctx.quadraticCurveTo(cx + b.w * 0.3, cy, b.x + b.w / 2, height);
    ctx.fill();
  }
}

export function GrassToy(props: { onTouch?: (total: number) => void }) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [touched, setTouched] = useState(0);
  const total = useRef(0);
  const onTouch = useRef(props.onTouch);
  onTouch.current = props.onTouch;

  useEffect(() => {
    const el = canvas.current;
    const box = wrap.current;
    const ctx = el?.getContext("2d");
    if (!el || !box || !ctx) return; // no canvas (e.g. a test DOM): the text still shows

    const reducedMotion = prefersReducedMotion();
    let width = 0;
    let height = 0; // from CSS: taller on phones
    let blades: Blade[] = [];
    let frame = 0;
    let last = 0;
    let lastX: number | null = null;

    const resize = () => {
      width = Math.max(1, Math.round(box.clientWidth));
      height = Math.max(1, Math.round(box.clientHeight));
      const dpr = window.devicePixelRatio || 1;
      el.width = Math.round(width * dpr);
      el.height = Math.round(height * dpr);
      el.style.width = `${width}px`;
      el.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      blades = makeBlades(width, height - 8);
      draw(ctx, blades, width, height);
    };

    const loop = (now: number) => {
      const frames = last ? (now - last) / (1000 / 60) : 1;
      last = now;
      const moving = step(blades, frames, now, { reducedMotion });
      draw(ctx, blades, width, height);
      frame = moving ? requestAnimationFrame(loop) : 0;
      if (!moving) last = 0;
    };
    const wake = () => {
      if (!frame) frame = requestAnimationFrame(loop);
    };

    const count = (n: number) => {
      if (!n) return;
      total.current += n;
      setTouched(total.current);
      onTouch.current?.(total.current);
    };

    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      const dx = lastX === null ? 0 : x - lastX;
      lastX = x;
      count(brush(blades, x, y, dx, height));
      wake();
    };
    const onLeave = () => {
      lastX = null;
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(box);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerdown", onMove);
    el.addEventListener("pointerleave", onLeave);

    // Keyboard: Space or Enter sends a gust through a random patch.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== " " && e.key !== "Enter") return;
      e.preventDefault();
      const start = Math.random() * Math.max(1, width - 120);
      let n = 0;
      for (let x = start; x < start + 120; x += 12) n += brush(blades, x, height - 10, 12, height);
      count(n);
      wake();
    };
    box.addEventListener("keydown", onKey);
    wake();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerdown", onMove);
      el.removeEventListener("pointerleave", onLeave);
      box.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <figure className="grass-toy">
      <div
        ref={wrap}
        className="grass-field"
        tabIndex={0}
        role="group"
        aria-label="Grass to touch while you wait. Brush it with your pointer or finger, or press Space."
      >
        <canvas ref={canvas} aria-hidden="true" />
      </div>
      <figcaption className="grass-count" data-touched={touched}>
        {touchedText(touched)}
      </figcaption>
    </figure>
  );
}
