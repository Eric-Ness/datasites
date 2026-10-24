import { describe, expect, it } from "vitest";
import { dataset, datasets } from "./fixtures/registry";
import {
  bulkDownloads,
  datasetEndpoints,
  defaultMeasure,
  defaultSelection,
  describeLocations,
  formatsOffered,
  groupBySource,
  sampleLocation,
  timeColumn,
  usesGeoRegistry,
  valueColumns,
} from "../src/lib/datasets";

const co2 = dataset("climatedata", "co2-monthly")!;
const county = dataset("climatedata", "county-series")!;
const gages = dataset("waterdata", "gage-series")!;
const factors = dataset("carbondata", "factor-table")!;
const grid = dataset("carbondata", "grid-table")!;
const files = dataset("visualizedata", "boundary-files")!;
const base = "https://api.example.dev";

describe("columns", () => {
  it("finds the time column and the chartable values", () => {
    expect(timeColumn(co2)?.name).toBe("date");
    expect(valueColumns(co2).map((c) => c.name)).toEqual(["value", "uncertainty"]);
    expect(defaultMeasure(co2)).toBe("value");
    expect(valueColumns(county).map((c) => c.name)).toEqual(["value", "anomaly", "rank"]);
    expect(timeColumn(files)).toBeUndefined();
    expect(defaultMeasure(files)).toBeNull();
  });
});

describe("sample selections", () => {
  it("uses an explicit location, a registry example, or nothing for patterns", () => {
    expect(sampleLocation(co2)).toBe("noaa-mlo");
    expect(sampleLocation(county)).toBe("us-county-06037");
    expect(sampleLocation(gages)).toBeNull();
    expect(defaultSelection(co2)).toEqual({ series: "average", location: "noaa-mlo", partition: null, file: null });
    expect(defaultSelection(factors).partition).toBe("all");
    expect(defaultSelection(files)).toEqual({ series: null, location: null, partition: null, file: null });
  });
});

describe("describeLocations", () => {
  it("describes explicit, registry and pattern locations", () => {
    expect(describeLocations(co2).text).toBe("Covers 2 places.");
    expect(describeLocations(co2).explicit).toHaveLength(2);
    expect(describeLocations(county, { "us-county": 3222 }).text).toContain("all 3,222 US counties");
    expect(describeLocations(gages).text).toContain("usgs-\\d{8,15}");
  });
});

describe("endpoints", () => {
  it("lists exact URLs for series datasets", () => {
    const urls = datasetEndpoints(co2, base, "climatedata").map((e) => e.example);
    expect(urls).toContain("https://api.example.dev/v1/co2-monthly");
    expect(urls).toContain("https://api.example.dev/v1/co2-monthly/average/noaa-mlo.json");
    expect(urls).toContain("https://api.example.dev/v1/co2-monthly/trend/noaa-mlo.csv");
    expect(urls).toContain("https://api.example.dev/v1/co2-monthly/download/co2-monthly.parquet");
  });

  it("leaves pattern locations as templates", () => {
    const series = datasetEndpoints(gages, base, "waterdata").find((e) => e.template.endsWith("{location}.json"));
    expect(series?.example).toBeNull();
  });

  it("lists partitions and table/all for tables", () => {
    const partitioned = datasetEndpoints(factors, base, "carbondata");
    expect(partitioned.map((e) => e.template)).toContain("/v1/factor-table/table/{scope}.json");
    expect(partitioned.map((e) => e.example)).toContain("https://api.example.dev/v1/factor-table/table/all.json");
    const flat = datasetEndpoints(grid, base, "carbondata");
    expect(flat.map((e) => e.example)).toContain("https://api.example.dev/v1/grid-table/table/all.csv");
  });

  it("has no bulk downloads for files datasets", () => {
    expect(bulkDownloads(files, base, "visualizedata")).toEqual([]);
    expect(datasetEndpoints(files, base, "visualizedata").some((e) => e.template.includes("/download/"))).toBe(false);
    expect(bulkDownloads(grid, "http://localhost:8787", "carbondata").map((d) => d.url)).toEqual([
      "http://localhost:8787/v1/grid-table/download/grid-table.csv?site=carbondata",
      "http://localhost:8787/v1/grid-table/download/grid-table.parquet?site=carbondata",
    ]);
  });
});

describe("sources", () => {
  it("groups datasets by source and notices the geo registry", () => {
    const groups = groupBySource(datasets);
    expect(groups).toHaveLength(datasets.length);
    expect(groupBySource([co2, co2]).map((g) => g.datasets.length)).toEqual([2]);
    expect(usesGeoRegistry([co2])).toBe(false);
    expect(usesGeoRegistry([co2, county])).toBe(true);
  });

  it("describes the formats a site offers", () => {
    expect(formatsOffered(["series"])).toBe(
      "Each series for each place as a small JSON or CSV file, plus every dataset as one CSV or Parquet file.",
    );
    expect(formatsOffered(["files"])).toBe("Ready-made files at stable paths.");
    expect(formatsOffered(["table", "files"])).toMatch(/^Tables as JSON or CSV.*ready-made files.*Parquet file\.$/);
    expect(formatsOffered([])).toMatch(/Parquet/);
  });
});
