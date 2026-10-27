import { datasets } from "@datasites/registry";
import { describe, expect, it } from "vitest";
import { datasetPageModel } from "../src/lib/dataset-page";
import { chartedDatasets, chartsFor, VISUALIZEDATA_ORG } from "../src/lib/visualizedata";

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
      label: "Presidential vote margin",
      href: `${VISUALIZEDATA_ORG}/indicators/us/us-pres-margin/`,
    });
    expect(chartsFor("electiondata", "no-such-dataset")).toBeNull();
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
