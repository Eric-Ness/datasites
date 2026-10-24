import { describe, expect, it } from "vitest";
import { dataset, datasets, sites } from "./fixtures/registry";
import { clientConfigJson, datasetPageModel } from "../src/lib/dataset-page";
import { datasetJsonLd, jsonLdScript } from "../src/lib/jsonld";
import { problemExample, seriesShape, tableShape } from "../src/lib/docs";

const prod = (domain: string) => `https://api.${domain}`;

describe("datasetPageModel", () => {
  it("builds a page for every fixture dataset of every kind", () => {
    for (const ds of datasets) {
      const site = sites.find((s) => s.id === ds.site)!;
      const model = datasetPageModel(site, ds, prod(site.domain));
      expect(model.client.id).toBe(ds.id);
      expect(model.client.kind).toBe(ds.kind);
      expect(model.sections.map((s) => s.id)).toEqual(expect.arrayContaining(["explore", "api", "schema", "source", "endpoints"]));
      expect(model.snippets.curl).toContain(prod(site.domain));
      expect(model.endpoints.length).toBeGreaterThan(2);
    }
  });

  it("charts series datasets", () => {
    const ds = dataset("climatedata", "county-series")!;
    const model = datasetPageModel({ id: "climatedata" }, ds, "https://api.climatedata.dev", { "us-county": 3222 });
    expect(model.exploreTitle).toBe("Chart");
    expect(model.client.timeType).toBe("month");
    expect(model.client.measures.map((m) => m.name)).toEqual(["value", "anomaly", "rank"]);
    expect(model.client.selection).toMatchObject({ series: "tavg", location: "us-county-06037" });
    expect(model.locations?.text).toContain("3,222");
    expect(model.downloads.map((d) => d.name)).toEqual(["county-series.csv", "county-series.parquet"]);
    expect(model.snippets.curl).toContain("/v1/county-series/tavg/us-county-06037.json");
  });

  it("previews table datasets", () => {
    const ds = dataset("carbondata", "factor-table")!;
    const model = datasetPageModel({ id: "carbondata" }, ds, "http://localhost:8787");
    expect(model.exploreTitle).toBe("Preview");
    expect(model.client.partition).toBe("scope");
    expect(model.client.key).toEqual(["factor_id"]);
    expect(model.client.timeType).toBe("year");
    expect(model.locations).toBeNull();
    expect(model.snippets.curl).toContain("http://localhost:8787/v1/factor-table/table/all.json?site=carbondata");
  });

  it("lists files for files datasets", () => {
    const ds = dataset("visualizedata", "boundary-files")!;
    const model = datasetPageModel({ id: "visualizedata" }, ds, "https://api.visualizedata.dev");
    expect(model.exploreTitle).toBe("Files");
    expect(model.sections.map((s) => s.id)).not.toContain("downloads");
    expect(model.downloads).toEqual([]);
    expect(model.client.measures).toEqual([]);
  });

  it("serializes the client config safely inside a script tag", () => {
    const ds = { ...dataset("climatedata", "co2-monthly")!, title: "</script><b>x" };
    const json = clientConfigJson(datasetPageModel({ id: "climatedata" }, ds, "https://api.climatedata.dev").client);
    expect(json).not.toContain("</script>");
    expect(JSON.parse(json).title).toBe("</script><b>x");
  });
});

describe("Dataset JSON-LD", () => {
  const climate = sites.find((s) => s.id === "climatedata")!;

  it("describes a series dataset with its license, creator and downloads", () => {
    const ld = datasetJsonLd(climate, dataset("climatedata", "co2-monthly")!);
    expect(ld).toMatchObject({
      "@context": "https://schema.org",
      "@type": "Dataset",
      name: "Monthly atmospheric CO2",
      url: "https://climatedata.dev/datasets/co2-monthly/",
      license: "https://www.usa.gov/government-works",
      creator: { "@type": "Organization", name: "NOAA GML" },
      publisher: { "@type": "Organization", name: "Climate Data", url: "https://climatedata.dev/" },
    });
    const dist = ld.distribution as { contentUrl: string; encodingFormat: string }[];
    expect(dist.map((d) => d.contentUrl)).toEqual([
      "https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.csv",
      "https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.parquet",
    ]);
    expect((ld.variableMeasured as { name: string }[]).map((v) => v.name)).toEqual(["value", "uncertainty", "interpolated"]);
  });

  it("lists every license of an SPDX expression and points files datasets at their metadata", () => {
    const carbon = sites.find((s) => s.id === "carbondata")!;
    const table = datasetJsonLd(carbon, dataset("carbondata", "factor-table")!);
    expect(table.license).toEqual([
      "https://creativecommons.org/licenses/by/4.0/",
      "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    ]);
    const viz = sites.find((s) => s.id === "visualizedata")!;
    const files = datasetJsonLd(viz, dataset("visualizedata", "boundary-files")!);
    expect((files.distribution as { contentUrl: string }[])[0]!.contentUrl).toBe("https://api.visualizedata.dev/v1/boundary-files");
    const unknown = datasetJsonLd(carbon, dataset("carbondata", "grid-table")!);
    expect(unknown.license).toBe("https://example.org/grid");
  });

  it("escapes < in the script body", () => {
    expect(jsonLdScript({ a: "</script>" })).toBe('{"a":"\\u003c/script>"}');
  });
});

describe("docs shapes", () => {
  it("sketch series and table files from real contracts", () => {
    const series = seriesShape(dataset("climatedata", "co2-monthly")!);
    expect(series).toContain('"dataset": "co2-monthly"');
    expect(series).toContain('"location_name": "Mauna Loa Observatory, Hawaii"');
    expect(series).toContain('"date": "YYYY-MM"');
    expect(series).toContain('"uncertainty": <number> or null');
    const table = tableShape(dataset("carbondata", "factor-table")!);
    expect(table).toContain('"key": ["factor_id"]');
    expect(table).toContain('"partition": { "scope": "…" }');
    expect(seriesShape()).toContain('"dataset": "{dataset}"');
    expect(JSON.parse(problemExample("climatedata.dev", dataset("climatedata", "co2-monthly")!)).type).toBe(
      "https://climatedata.dev/docs/errors#unknown-file",
    );
  });
});
