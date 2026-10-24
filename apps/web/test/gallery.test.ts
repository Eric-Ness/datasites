import { describe, expect, it } from "vitest";
import { datasets, sites } from "./fixtures/registry";
import { CURATED, galleryItems } from "../src/lib/gallery";

describe("galleryItems", () => {
  it("shows the curated CO2 charts when climatedata/co2-monthly exists", () => {
    const items = galleryItems(sites, datasets);
    const curated = items.filter((i) => CURATED.some((c) => c.id === i.id));
    expect(curated.length).toBeGreaterThanOrEqual(3);
    expect(curated.every((i) => i.site === "climatedata" && i.dataset === "co2-monthly")).toBe(true);
    expect(items.find((i) => i.id === "co2-growth")?.transform).toBe("yoy");
  });

  it("adds a default chart for every other series dataset, so new datasets appear without code changes", () => {
    const items = galleryItems(sites, datasets, (id) => (id === "us-county-06037" ? "Los Angeles County" : undefined));
    const county = items.find((i) => i.dataset === "county-series");
    expect(county).toMatchObject({ site: "climatedata", title: "Fixture county climate: Los Angeles County" });
    expect(county?.lines).toEqual([{ series: "tavg", location: "us-county-06037", label: "Average temperature" }]);
    const gages = items.find((i) => i.dataset === "gage-series");
    expect(gages?.lines[0]?.location).toBeNull();
    expect(items.some((i) => i.dataset === "factor-table" || i.dataset === "boundary-files")).toBe(false);
  });

  it("drops curated charts whose data is not in the registry", () => {
    const withoutCo2 = datasets.filter((d) => d.id !== "co2-monthly");
    const items = galleryItems(sites, withoutCo2);
    expect(items.some((i) => i.dataset === "co2-monthly")).toBe(false);
    const noTrend = datasets.map((d) => (d.id === "co2-monthly" ? { ...d, series: { average: "Monthly mean" } } : d));
    const ids = galleryItems(sites, noTrend).map((i) => i.id);
    expect(ids).toContain("keeling-curve");
    expect(ids).not.toContain("co2-seasons-removed");
  });

  it("only uses sites in the registry", () => {
    expect(galleryItems(sites.filter((s) => s.id !== "climatedata"), datasets).some((i) => i.site === "climatedata")).toBe(false);
  });
});
