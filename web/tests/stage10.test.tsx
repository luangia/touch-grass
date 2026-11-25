import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ContextValue } from "../src/api/client";
import { parameterRows } from "../src/components/AboutPage";
import { barWidth } from "../src/components/charts";
import { ComparePage } from "../src/components/ComparePage";
import {
  comparisonSentence,
  ContextList,
  estimateText,
  ResidentComparisonBlock,
} from "../src/components/context";
import { RankingsPage, tieRanks } from "../src/components/RankingsPage";
import { detourText } from "../src/components/SidePanel";
import { AREAS, METHODOLOGY, metrics, REGION } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const comparison = METHODOLOGY.resident_comparison!;

describe("ranking ties", () => {
  it("shows equal displayed values as ties", () => {
    expect(tieRanks(["100%", "100%", "99%", "99%", "98%"])).toEqual(["1", "=", "3", "=", "5"]);
    expect(tieRanks([])).toEqual([]);
  });
});

describe("chart scale", () => {
  it("bars start at zero and stay on a 0-100% axis", () => {
    expect([barWidth(0), barWidth(0.5), barWidth(1), barWidth(1.2), barWidth(-0.1)]).toEqual([
      0, 50, 100, 100, 0,
    ]);
    expect(barWidth(null)).toBe(0);
    expect(barWidth(Number.NaN)).toBe(0);
  });
});

describe("demographic wording", () => {
  it("states a clear difference in points, with its margin", () => {
    const s = comparisonSentence(comparison.indicators[0]!, 10);
    expect(s).toContain("People of color: 40.0% (± 3.0%) where residents live farther");
    expect(s).toContain("A clear difference: 10.0 points (± 3.6) higher farther away.");
  });

  it("says 'no clear difference' when the gap is within the margin", () => {
    const s = comparisonSentence(comparison.indicators[1]!, 10);
    expect(s).toMatch(/No clear difference: the gap is within the margin of error\.$/);
    expect(s).not.toMatch(/higher|lower/);
  });

  it("frames the comparison as context, not cause", () => {
    render(<ResidentComparisonBlock comparison={comparison} />);
    expect(screen.getByText(/doesn't show why/)).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("formats estimates with their margin, and suppressed ones as such", () => {
    const share = { indicator: "x", kind: "share", estimate: 0.09, moe: 0.07 } as ContextValue;
    const count = { indicator: "x", kind: "count", estimate: 2208, moe: 386 } as ContextValue;
    expect(estimateText(share)).toBe("9% ± 7%");
    expect(estimateText(count)).toBe("2,208 ± 386");
    expect(estimateText({ ...share, estimate: null })).toBe("no estimate");
  });

  it("greys low-reliability values and labels them, in the order given (never sorted)", () => {
    const values = [
      { indicator: "b", kind: "share", estimate: 0.5, moe: 0.4, reliability: "low" },
      { indicator: "a", kind: "share", estimate: 0.1, moe: 0.01, reliability: "high" },
    ] as ContextValue[];
    const { container } = render(
      <ContextList values={values} labels={{ a: "Alpha", b: "Beta" }} />,
    );
    expect([...container.querySelectorAll("dt")].map((d) => d.textContent)).toEqual([
      "Beta",
      "Alpha",
    ]);
    expect(container.querySelector(".ctx-low")?.textContent).toContain("unreliable");
  });
});

describe("straight line", () => {
  it("describes a block's detour", () => {
    expect(detourText(1222, 745)).toBe("745 m in a straight line: the walk is 1.6× as long");
    expect(detourText(100, 0)).toBeNull();
    expect(detourText(100, undefined)).toBeNull();
  });
});

describe("parameters", () => {
  it("labels the run's own parameters", () => {
    const rows = parameterRows({
      ...METHODOLOGY,
      analysis: { walk_speed_kmh: 4.8, thresholds_min: [5, 10, 15], access_tolerance_m: 15 },
      metrics: {
        metrics: {
          underserved_minutes: 10,
          min_residents_for_ranking: 1000,
          cv_high_max: 0.15,
          cv_medium_max: 0.3,
        },
      },
    });
    expect(Object.fromEntries(rows)).toMatchObject({
      "Walking speed": "4.8 km/h",
      "Walk times": "5 min, 10 min, 15 min",
      "Underserved beyond": "a 10-minute walk",
      "Fewest residents to be ranked": "1,000",
      "Census reliability (coefficient of variation)":
        "high below 15%, medium up to 30%, low above",
    });
    expect(parameterRows(null)).toEqual([]);
  });
});

function stubFetch(handler: (path: string) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = new URL(url);
      return new Response(JSON.stringify(handler(u.pathname + u.search)), { status: 200 });
    }),
  );
}

describe("RankingsPage", () => {
  it("renders four titled lists whose rows open the map", async () => {
    stubFetch((path) => ({
      region_id: "testville-zz",
      run_id: "abc123",
      area_type: "neighborhood",
      metric: new URLSearchParams(path.split("?")[1]).get("metric"),
      threshold_min: 10,
      order: "asc",
      min_residents: 1000,
      entries: [
        {
          rank: 1,
          area_id: "neighborhood:1",
          name: "OAK HOLLOW",
          value: 0.5,
          pop_total: 2000,
          underserved_pop: 1000,
          pct_within: 0.5,
        },
        {
          rank: 2,
          area_id: "neighborhood:2",
          name: "Riverbend",
          value: 0.5,
          pop_total: 3000,
          underserved_pop: 900,
          pct_within: 0.5,
        },
      ],
    }));
    const onOpenArea = vi.fn();
    render(
      <RankingsPage
        region={REGION}
        methodology={METHODOLOGY}
        areaType="neighborhood"
        threshold={10}
        onAreaType={vi.fn()}
        onThreshold={vi.fn()}
        onOpenArea={onOpenArea}
      />,
    );
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Oak Hollow" })).toHaveLength(4),
    );
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      "Neighborhoods where the fewest residents live within a 10-minute walk of a park",
      "Neighborhoods with the most residents farther than a 10-minute walk",
      "Neighborhoods where underserved residents are most concentrated",
      "Neighborhoods where barriers cost the most (10-minute walk)",
    ]);
    const first = screen.getAllByRole("region")[0]!;
    expect(within(first).getAllByText("=")).toHaveLength(1); // two 50% rows: a tie
    fireEvent.click(within(first).getByRole("button", { name: "Riverbend" }));
    expect(onOpenArea).toHaveBeenCalledWith("neighborhood:2");
  });
});

describe("ComparePage", () => {
  it("asks for a second area, then compares with chart and table", async () => {
    stubFetch(() => ({
      region_id: "testville-zz",
      run_id: "abc123",
      threshold_min: 10,
      areas: [metrics(), metrics({ area_id: "city", area_type: "city", name: "Testville" })],
    }));
    const onChange = vi.fn();
    const { rerender } = render(
      <ComparePage
        region={REGION}
        methodology={METHODOLOGY}
        areaType="neighborhood"
        features={AREAS.features}
        selected={["neighborhood:1"]}
        onChange={onChange}
        onOpenArea={vi.fn()}
      />,
    );
    expect(screen.getByText("Pick one more area to compare.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "+ Whole city" }));
    expect(onChange).toHaveBeenCalledWith(["neighborhood:1", "city"]);
    rerender(
      <ComparePage
        region={REGION}
        methodology={METHODOLOGY}
        areaType="neighborhood"
        features={AREAS.features}
        selected={["neighborhood:1", "city"]}
        onChange={onChange}
        onOpenArea={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole("table")).toBeTruthy());
    expect(
      screen.getByRole("img", { name: /Share of residents within each walk time/ }),
    ).toBeTruthy();
    expect(screen.getAllByText("62%").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Remove Oak Hollow" }));
    expect(onChange).toHaveBeenLastCalledWith(["city"]);
  });
});
