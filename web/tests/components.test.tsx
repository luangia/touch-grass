import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bboxOf } from "../src/api/geojson";
import { AreaTypeToggle, SearchBox, ThresholdToggle } from "../src/components/Controls";
import { SidePanel, type Selection } from "../src/components/SidePanel";
import { displayName } from "../src/format";
import { blockClasses } from "../src/map/style";
import { formatView, parseView } from "../src/state/url";
import { AREAS, feature, METHODOLOGY, metrics, REGION, SUMMARY } from "./fixtures";

// jsdom has no WebGL: the map itself is replaced; everything around it is real.
vi.mock("../src/map/MapView", () => ({
  MapView: () => <div data-testid="map" />,
}));

afterEach(cleanup);

const EMPTY: Selection = {
  areaId: null,
  metrics: null,
  loading: false,
  error: null,
  alsoIn: [],
  block: null,
};

describe("url state", () => {
  it("round-trips and ignores junk", () => {
    const v = {
      mode: "blocks" as const,
      region: "testville-zz",
      areaType: "neighborhood",
      threshold: 10,
      area: "neighborhood:1",
    };
    expect(parseView(formatView(v))).toEqual(v);
    expect(parseView("?threshold=abc&region=&view=everything")).toEqual({
      mode: null,
      region: null,
      areaType: null,
      threshold: null,
      area: null,
    });
    expect(
      formatView({ mode: null, region: null, areaType: null, threshold: null, area: null }),
    ).toBe("");
  });
});

describe("formatting", () => {
  it("title-cases all-caps names and labels numeric ones", () => {
    expect(displayName("OAK HOLLOW-WEST", "neighborhood")).toBe("Oak Hollow-West");
    expect(displayName("MT. PLEASANT", "neighborhood")).toBe("Mt. Pleasant");
    expect(displayName("Riverbend", "neighborhood")).toBe("Riverbend");
    expect(displayName("3", "council_district")).toBe("Council district 3");
  });

  it("computes a polygon bbox", () => {
    expect(bboxOf(feature("neighborhood:1", "X", 0.5).geometry)).toEqual([2, 1, 2.5, 1.5]);
  });
});

describe("SearchBox", () => {
  it("selects an area when a name matches exactly, case-insensitively", () => {
    const onSelect = vi.fn();
    render(<SearchBox features={AREAS.features} areaType="neighborhood" onSelect={onSelect} />);
    // A search input with a suggestion list is exposed as a combobox, labelled by its <label>.
    const input = screen.getByRole("combobox", { name: /find a neighborhood/i });
    fireEvent.change(input, { target: { value: "oak" } });
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "oak hollow" } });
    expect(onSelect).toHaveBeenCalledWith("neighborhood:1");
    expect(screen.getByText("3 to choose from")).toBeTruthy();
  });
});

describe("toggles", () => {
  it("threshold toggle offers the region's thresholds", () => {
    const onChange = vi.fn();
    render(<ThresholdToggle thresholds={REGION.thresholds} value={10} onChange={onChange} />);
    expect(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).value)).toEqual([
      "5",
      "10",
      "15",
    ]);
    fireEvent.click(screen.getByLabelText("15 min"));
    expect(onChange).toHaveBeenCalledWith(15);
  });

  it("area type toggle hides itself for a single type", () => {
    const { container } = render(
      <AreaTypeToggle
        types={[{ area_type: "neighborhood", areas: 3 }]}
        value="neighborhood"
        onChange={vi.fn()}
      />,
    );
    expect(container.innerHTML).toBe("");
  });
});

describe("SidePanel", () => {
  const base = {
    region: REGION,
    mode: "residents" as const,
    blockClasses: blockClasses(REGION.thresholds, REGION.underserved_minutes),
    threshold: 10,
    hovered: null,
    methodology: METHODOLOGY,
    onSelectArea: vi.fn(),
  };

  it("shows shares per threshold, underserved residents and the typical walk", () => {
    render(<SidePanel {...base} selection={{ ...EMPTY, metrics: metrics() }} />);
    expect(screen.getByRole("heading", { name: "Oak Hollow" })).toBeTruthy();
    const bars = screen.getByRole("list", { name: "Share of residents by walk time" });
    expect(
      within(bars)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Within a 5-min walk20%", "Within a 10-min walk62%", "Within a 15-min walk95%"]);
    expect(screen.getByText("760 residents (38%)")).toBeTruthy();
    expect(screen.getByText(/640 m \(8 min\)/)).toBeTruthy();
    expect(screen.queryByText(/left out of rankings/)).toBeNull();
  });

  it("explains why a small area isn't ranked, with the floor from the API", () => {
    render(<SidePanel {...base} selection={{ ...EMPTY, metrics: metrics({ ranked: false }) }} />);
    expect(screen.getByText(/Fewer than 1,000 residents/)).toBeTruthy();
  });

  it("lists every overlapping area at the clicked spot", () => {
    const onSelectArea = vi.fn();
    const alsoIn = [AREAS.features[0]!.properties, AREAS.features[1]!.properties];
    render(
      <SidePanel
        {...base}
        onSelectArea={onSelectArea}
        selection={{ ...EMPTY, metrics: metrics(), alsoIn }}
      />,
    );
    const note = screen.getByText(/in 2 overlapping areas/);
    fireEvent.click(within(note).getByRole("button", { name: "Riverbend" }));
    expect(onSelectArea).toHaveBeenCalledWith("neighborhood:2");
  });

  it("leads with a clicked block in the blocks view: distance, park, area, the 10-min line", () => {
    const onSelectArea = vi.fn();
    const block = {
      geoid20: "990010001001001",
      pop20: 12,
      reachable: true,
      network_m: 1000,
      walk_min: 12.5,
      nearest_park_name: "Willow Green",
      parks_reachable: 0,
      reachable_park_ha: 0,
    };
    const alsoIn = [AREAS.features[0]!.properties];
    render(
      <SidePanel
        {...base}
        mode="blocks"
        onSelectArea={onSelectArea}
        selection={{ ...EMPTY, block, alsoIn, metrics: metrics() }}
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Census block", "Oak Hollow"]); // block first in this view
    expect(screen.getByText("Willow Green")).toBeTruthy();
    expect(screen.getByText("1,000 m")).toBeTruthy();
    expect(screen.getByText("farther than the 10-minute line")).toBeTruthy();
    expect(screen.getByText(/no park within a 10-minute walk/)).toBeTruthy();
    const section = screen.getByRole("region", { name: "Selected census block" });
    fireEvent.click(within(section).getByRole("button", { name: "Oak Hollow" }));
    expect(onSelectArea).toHaveBeenCalledWith("neighborhood:1");
  });

  it("handles an unnamed park and a block with no route", () => {
    const block = { geoid20: "990010001001002", pop20: 3, reachable: true, network_m: 300 };
    const { rerender } = render(
      <SidePanel {...base} mode="blocks" selection={{ ...EMPTY, block }} />,
    );
    expect(screen.getByText(/to the nearest park/)).toBeTruthy();
    expect(screen.getByText(/within the 10-minute line/)).toBeTruthy();
    rerender(
      <SidePanel
        {...base}
        mode="blocks"
        selection={{ ...EMPTY, block: { geoid20: "990010001001003", pop20: 3, reachable: false } }}
      />,
    );
    expect(screen.getByText(/No walking route/)).toBeTruthy();
  });

  it("headlines the share in plain words", () => {
    render(<SidePanel {...base} selection={{ ...EMPTY, metrics: metrics() }} />);
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          el.textContent ===
            "62% of residents live within a 10-minute walk of a park or natural area.",
      ),
    ).toBeTruthy();
  });

  it("carries the caveats and source attributions from the API", () => {
    render(<SidePanel {...base} selection={EMPTY} />);
    expect(screen.getByText("Distances are walking routes.")).toBeTruthy();
    expect(screen.getByText("Example Agency")).toBeTruthy();
  });
});

describe("App", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch(routes: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = new URL(url).pathname + new URL(url).search;
        const hit = Object.entries(routes).find(([k]) => path.startsWith(k));
        if (!hit)
          return new Response(JSON.stringify({ detail: `no stub for ${path}` }), { status: 404 });
        return new Response(JSON.stringify(hit[1]), { status: 200 });
      }),
    );
  }

  it("loads the first region, its finest area type and the underserved threshold", async () => {
    stubFetch({
      "/api/v1/regions/testville-zz/areas?type=neighborhood&threshold=10": AREAS,
      "/api/v1/regions/testville-zz/methodology": METHODOLOGY,
      "/api/v1/regions/testville-zz": REGION,
      "/api/v1/regions": [SUMMARY],
    });
    const { App } = await import("../src/App");
    render(<App />);
    await waitFor(() => expect(screen.getByText("· Testville")).toBeTruthy());
    expect((screen.getByLabelText("10 min") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Neighborhoods (3)") as HTMLInputElement).checked).toBe(true);
    await waitFor(() => expect(screen.getByText("3 to choose from")).toBeTruthy());
  });

  it("the blocks view hides the walk-time choice and swaps the legend", async () => {
    window.history.replaceState(null, "", "/?view=blocks");
    stubFetch({
      "/api/v1/regions/testville-zz/areas?type=neighborhood&threshold=10": AREAS,
      "/api/v1/regions/testville-zz/methodology": METHODOLOGY,
      "/api/v1/regions/testville-zz": REGION,
      "/api/v1/regions": [SUMMARY],
    });
    const { App } = await import("../src/App");
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Walk from each block")).toBeTruthy());
    expect((screen.getByLabelText("Walk from each block") as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByLabelText("5 min")).toBeNull();
    expect(screen.getByText(/Walk from each census block to the nearest park/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Residents near a park"));
    await waitFor(() => expect(screen.getByLabelText("5 min")).toBeTruthy());
    expect(window.location.search).toBe("?view=residents");
  });

  it("says clearly when the API is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const { App } = await import("../src/App");
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/The map can't load: cannot reach the API/)).toBeTruthy(),
    );
  });
});
