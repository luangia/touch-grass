import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bboxOf } from "../src/api/geojson";
import { AreaTypeToggle, SearchBox, ThresholdToggle } from "../src/components/Controls";
import { SidePanel, type Selection } from "../src/components/SidePanel";
import { displayName } from "../src/format";
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
      region: "testville-zz",
      areaType: "neighborhood",
      threshold: 10,
      area: "neighborhood:1",
    };
    expect(parseView(formatView(v))).toEqual(v);
    expect(parseView("?threshold=abc&region=")).toEqual({
      region: null,
      areaType: null,
      threshold: null,
      area: null,
    });
    expect(formatView({ region: null, areaType: null, threshold: null, area: null })).toBe("");
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
    threshold: 10,
    hovered: null,
    methodology: METHODOLOGY,
    onSelectArea: vi.fn(),
  };

  it("shows shares per threshold, underserved residents and the typical walk", () => {
    render(<SidePanel {...base} selection={{ ...EMPTY, metrics: metrics() }} />);
    expect(screen.getByRole("heading", { name: "Oak Hollow" })).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();
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

  it("describes a clicked block, including no route", () => {
    const block = {
      geoid20: "990010001001001",
      pop20: 12,
      reachable: true,
      network_m: 1000,
      walk_min: 12.5,
      parks_reachable: 0,
      reachable_park_ha: 0,
    };
    const { rerender } = render(<SidePanel {...base} selection={{ ...EMPTY, block }} />);
    expect(screen.getByText(/1,000 m/)).toBeTruthy();
    expect(screen.getByText(/No park within 10 min/)).toBeTruthy();
    rerender(
      <SidePanel
        {...base}
        selection={{ ...EMPTY, block: { ...block, reachable: false, network_m: null } }}
      />,
    );
    expect(screen.getByText(/No walking route/)).toBeTruthy();
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
