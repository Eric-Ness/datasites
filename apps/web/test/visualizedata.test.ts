import { datasets } from "@datasites/registry";
import { describe, expect, it } from "vitest";
import { datasetPageModel } from "../src/lib/dataset-page";
import { indicatorPages, navItems, sitePages } from "../src/lib/routes";
import { chartedDatasets, chartsFor, formatIndicator, indicatorsForSite, VISUALIZEDATA_ORG } from "../src/lib/visualizedata";

const byKey = new Map(datasets.map((d) => [`${d.site}/${d.id}`, d]));

describe("visualizedata.org links", () => {
  it("only names datasets in the registry, with links on visualizedata.org", () => {
    expect(chartedDatasets().length).toBeGreaterThan(0);
    for (const key of chartedDatasets()) {
      expect(byKey.has(key), key).toBe(true);
      const [site, id] = key.split("/") as [string, string];
      const charts = chartsFor(site, id)!;
      expect(charts.indicators.length + charts.pages.length, key).toBeGreaterThan(0);
      for (const link of [...charts.indicators, ...charts.pages]) {
        expect(link.href).toMatch(/^https:\/\/visualizedata\.org\/[a-z0-9/_-]*$/);
        expect(link.label.trim()).not.toBe("");
      }
    }
  });

  it("links a dataset to the indicators built from it", () => {
    const charts = chartsFor("electiondata", "county-president")!;
    expect(charts.indicators).toContainEqual({
      id: "us-pres-margin",
      label: "Presidential vote margin",
      href: `${VISUALIZEDATA_ORG}/indicators/us/us-pres-margin/`,
    });
    expect(chartsFor("electiondata", "no-such-dataset")).toBeNull();
  });

  it("gives every indicator a definition, a trend and its latest year", () => {
    const linked = chartedDatasets().flatMap((key) => {
      const [site, id] = key.split("/") as [string, string];
      return chartsFor(site, id)!.indicators.map((i) => ({ ...i, site }));
    });
    for (const link of linked) {
      const ind = indicatorsForSite(link.site, datasets).find((i) => i.id === link.id);
      expect(ind, link.id).toBeDefined();
      expect(ind!.href).toBe(link.href);
      expect(ind!.unit.trim(), link.id).not.toBe("");
      expect(ind!.latest?.highest.length, link.id).toBeGreaterThan(0);
      for (const [year, value] of ind!.trend.points) {
        expect(Number.isInteger(year)).toBe(true);
        expect(Number.isFinite(value)).toBe(true);
      }
    }
  });

  it("lists airdata's ten air indicators, all from the yearly AQI dataset", () => {
    const air = indicatorsForSite("airdata", datasets);
    expect(air).toHaveLength(10);
    expect(new Set(air.map((i) => i.dataset))).toEqual(new Set(["county-aqi-annual"]));
    expect(air.map((i) => i.name)).toContain("Days with good air");
    // Without the dataset (an unpublished registry) there are no indicator pages.
    expect(indicatorsForSite("airdata", [])).toEqual([]);
  });

  it("formats values the way visualizedata.org does", () => {
    expect(formatIndicator(12.345, { unit: "pts", decimals: 1, format: "margin" })).toBe("R+12.3");
    expect(formatIndicator(-4.06, { unit: "pts", decimals: 1, format: "margin" })).toBe("D+4.1");
    expect(formatIndicator(1.25, { unit: "°F", decimals: 1, format: "signed" })).toBe("+1.3 °F");
    expect(formatIndicator(-0.5, { unit: "°F", decimals: 1, format: "signed" })).toBe("−0.5 °F");
    expect(formatIndicator(4525460, { unit: "jobs", decimals: 0, format: "compact" })).toBe("4.5M jobs");
    expect(formatIndicator(30769700000000, { unit: "US$", decimals: 0, format: "compact" })).toBe("$30.8T");
    expect(formatIndicator(4121.06, { unit: "US$", decimals: 0, format: "dollars" })).toBe("$4,121");
    expect(formatIndicator(45.21, { unit: "% of days", decimals: 1, format: null })).toBe("45.2% of days");
    expect(formatIndicator(80, { unit: "AQI", decimals: 0, format: null })).toBe("80 AQI");
  });

  it("adds an 'On visualizedata.org' section only to charted datasets", () => {
    const charted = byKey.get("electiondata/county-president")!;
    const model = datasetPageModel({ id: "electiondata" }, charted, "https://api.electiondata.dev");
    expect(model.sections.map((s) => s.id).slice(0, 2)).toEqual(["explore", "visualizedata"]);
    expect(model.charts?.indicators.length).toBeGreaterThan(0);

    const uncharted = datasets.find((d) => !chartsFor(d.site, d.id))!;
    const plain = datasetPageModel({ id: uncharted.site }, uncharted, "https://api.example.dev");
    expect(plain.sections.map((s) => s.id)).not.toContain("visualizedata");
    expect(plain.charts).toBeNull();
  });
});

describe("indicator pages", () => {
  it("adds the indicator list and pages to the sitemap and navigation of sites that have them", () => {
    const pages = sitePages({ id: "airdata" }, datasets).map((p) => p.path);
    expect(pages).toContain("/indicators/");
    expect(pages).toContain("/indicators/us-good-air-days/");
    expect(indicatorPages({ id: "airdata" }, datasets)).toHaveLength(11);
    expect(navItems("airdata", datasets).map((n) => n.href)).toContain("/indicators/");
  });

  it("leaves them out where visualizedata.org builds no indicators", () => {
    expect(indicatorPages({ id: "wildfiredata" }, datasets)).toEqual([]);
    expect(navItems("wildfiredata", datasets).map((n) => n.href)).not.toContain("/indicators/");
  });
});
