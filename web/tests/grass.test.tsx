import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type Blade,
  brush,
  MAX_ANGLE,
  makeBlades,
  seededRandom,
  step,
  touchedText,
} from "../src/components/grass";
import { AREAS, METHODOLOGY, REGION, SUMMARY } from "./fixtures";

// jsdom has no WebGL or canvas: the map and the toy are stand-ins; the App around them is real.
vi.mock("../src/map/MapView", () => ({ MapView: () => <div data-testid="map" /> }));
vi.mock("../src/components/GrassToy", () => ({
  GrassToy: (p: { onTouch?: (n: number) => void }) => (
    <button type="button" onClick={() => p.onTouch?.(5)}>
      brush the grass
    </button>
  ),
}));

afterEach(cleanup);

const H = 120;
const blade = (x: number, h = 100): Blade => ({ x, h, w: 4, shade: 0, phase: 0, a: 0, v: 0 });
const settle = (blades: Blade[]) => {
  for (let i = 0; i < 600; i++) step(blades, 1, i * 16, { wind: false });
};

describe("grass physics", () => {
  it("makes the same field every time, inside the strip", () => {
    const a = makeBlades(600, H);
    expect(makeBlades(600, H)).toEqual(a);
    expect(a.length).toBeGreaterThan(60);
    expect(a.every((b) => b.x > 0 && b.x < 600 && b.h <= H && b.h > 0)).toBe(true);
    expect(seededRandom(1)()).toBe(seededRandom(1)());
  });

  it("pushes nearby blades the way the pointer moves, and leaves the rest alone", () => {
    const blades = [blade(100), blade(110), blade(300)];
    expect(brush(blades, 105, 100, +20, H)).toBe(2);
    expect(blades[0]!.v).toBeGreaterThan(0);
    expect(blades[1]!.v).toBeGreaterThan(0);
    expect(blades[2]!.v).toBe(0);
    const left = [blade(100)];
    brush(left, 100, 100, -20, H);
    expect(left[0]!.v).toBeLessThan(0);
  });

  it("only blades tall enough to reach the pointer are touched", () => {
    const blades = [blade(100, 30)]; // tip at y = 90
    expect(brush(blades, 100, 20, 10, H)).toBe(0); // pointer far above the tip
    expect(brush(blades, 100, 95, 10, H)).toBe(1);
  });

  it("a moving blade isn't counted twice; once it settles it counts again", () => {
    const blades = [blade(100)];
    expect(brush(blades, 100, 100, 15, H)).toBe(1);
    step(blades, 1, 0, { wind: false });
    expect(brush(blades, 100, 100, 15, H)).toBe(0);
    settle(blades);
    expect(brush(blades, 100, 100, 15, H)).toBe(1);
  });

  it("springs back to upright, never folds past the limit, and stops when still", () => {
    const blades = [blade(100)];
    for (let i = 0; i < 20; i++) brush(blades, 100, 100, 40, H);
    for (let i = 0; i < 30; i++) {
      step(blades, 1, i * 16, { wind: false });
      expect(Math.abs(blades[0]!.a)).toBeLessThanOrEqual(MAX_ANGLE);
    }
    settle(blades);
    expect(Math.abs(blades[0]!.a)).toBeLessThan(0.002);
    expect(step(blades, 1, 0, { wind: false })).toBe(false); // the loop can pause
    expect(step(blades, 1, 0, { wind: true })).toBe(true); // wind keeps it going
  });

  it("with reduced motion: no wind, and a pushed blade settles without swinging back", () => {
    const blades = [blade(100)];
    brush(blades, 100, 100, 30, H);
    let crossed = false;
    for (let i = 0; i < 200; i++) {
      step(blades, 1, i * 16, { reducedMotion: true });
      if (blades[0]!.a < -0.01) crossed = true;
    }
    expect(crossed).toBe(false);
    expect(step(blades, 1, 0, { reducedMotion: true })).toBe(false); // wind is off
  });

  it("caps a long stall so the springs don't explode", () => {
    const blades = [blade(100)];
    brush(blades, 100, 100, 40, H);
    step(blades, 500, 0, { wind: false });
    expect(Number.isFinite(blades[0]!.a)).toBe(true);
    expect(Math.abs(blades[0]!.a)).toBeLessThanOrEqual(MAX_ANGLE);
  });

  it("counts in words", () => {
    expect(touchedText(0)).toMatch(/Brush the grass/);
    expect(touchedText(1)).toBe("You've touched 1 blade of grass.");
    expect(touchedText(1234)).toBe("You've touched 1,234 blades of grass.");
  });
});

describe("the loading screen hand-off", () => {
  let release: () => void = () => {};

  beforeEach(() => {
    window.history.replaceState(null, "", "/");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const routes: Record<string, unknown> = {
      "/api/v1/regions/testville-zz/areas": AREAS,
      "/api/v1/regions/testville-zz/methodology": METHODOLOGY,
      "/api/v1/regions/testville-zz": REGION,
    };
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = new URL(url).pathname;
        if (path === "/api/v1/regions") {
          await gate; // the sleeping server
          return new Response(JSON.stringify([SUMMARY]), { status: 200 });
        }
        const hit = Object.entries(routes).find(([k]) => path.startsWith(k));
        return new Response(JSON.stringify(hit?.[1] ?? {}), { status: hit ? 200 : 404 });
      }),
    );
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function slowStart() {
    const { App, WAKE_NOTICE_AFTER_MS } = await import("../src/App");
    render(<App />);
    expect(screen.queryByText("brush the grass")).toBeNull(); // not on a fast load
    await act(async () => {
      vi.advanceTimersByTime(WAKE_NOTICE_AFTER_MS);
    });
    expect(screen.getByText("brush the grass")).toBeTruthy();
    vi.useRealTimers(); // Testing Library's findBy/waitFor poll with real timers
  }

  it("someone brushing the grass gets a button when the map is ready", async () => {
    await slowStart();
    fireEvent.click(screen.getByText("brush the grass"));
    await act(async () => release());
    const go = await screen.findByRole("button", { name: "Your map is ready →" });
    expect(screen.queryByRole("link", { name: /TouchGrass/ })).toBeNull(); // still waiting
    fireEvent.click(go);
    expect(screen.getByRole("link", { name: /TouchGrass\s*Testville/ })).toBeTruthy();
  });

  it("someone who didn't touch it goes straight to the map", async () => {
    await slowStart();
    await act(async () => release());
    await waitFor(() =>
      expect(screen.getByRole("link", { name: /TouchGrass\s*Testville/ })).toBeTruthy(),
    );
    expect(screen.queryByRole("button", { name: "Your map is ready →" })).toBeNull();
  });
});
