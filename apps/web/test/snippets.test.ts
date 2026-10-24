import { describe, expect, it } from "vitest";
import { datasetSnippets, quickstart } from "../src/lib/snippets";

const prod = "https://api.climatedata.dev";
const local = "http://localhost:8787";

describe("series snippets", () => {
  const input = { siteId: "climatedata", id: "co2-monthly", kind: "series" as const, series: "average", location: "noaa-mlo" };

  it("use the exact production URLs", () => {
    const s = datasetSnippets({ ...input, base: prod });
    expect(s.curl).toContain('curl -s "https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.json"');
    expect(s.curl).toContain('curl -sO "https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.csv"');
    expect(s.python).toContain("import polars as pl");
    expect(s.python).toContain('pl.read_csv("https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.csv")');
    expect(s.python).toContain('pl.read_parquet("https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.parquet")');
    expect(s.javascript).toContain('await fetch("https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.json")');
    expect(s.javascript).toContain("const { meta, data } = await res.json();");
    for (const code of Object.values(s)) expect(code).not.toContain("?site=");
  });

  it("add ?site= to every URL on a local API", () => {
    const s = datasetSnippets({ ...input, base: local });
    expect(s.curl).toContain('"http://localhost:8787/v1/co2-monthly/average/noaa-mlo.json?site=climatedata"');
    expect(s.python).toContain('"http://localhost:8787/v1/co2-monthly/download/co2-monthly.parquet?site=climatedata"');
    const urls = Object.values(s).join("\n").match(/https?:\/\/[^"\s]+/g) ?? [];
    expect(urls.length).toBeGreaterThan(4);
    for (const url of urls) expect(url).toMatch(/\?site=climatedata$/);
  });

  it("show readable placeholders while the location is unknown", () => {
    const s = datasetSnippets({ siteId: "waterdata", base: prod, id: "streamflow-daily", kind: "series", series: "discharge", location: null });
    expect(s.curl).toContain("/v1/streamflow-daily/discharge/{location}.json");
    expect(s.curl).not.toContain("%7B");
  });
});

describe("table snippets", () => {
  it("use the chosen partition, defaulting to all", () => {
    const all = datasetSnippets({ siteId: "carbondata", base: "https://api.carbondata.dev", id: "emission-factors", kind: "table" });
    expect(all.curl).toContain('"https://api.carbondata.dev/v1/emission-factors/table/all.json"');
    const part = datasetSnippets({ siteId: "carbondata", base: "https://api.carbondata.dev", id: "emission-factors", kind: "table", partition: "ember" });
    expect(part.curl).toContain('"https://api.carbondata.dev/v1/emission-factors/table/ember.json"');
    expect(part.python).toContain('pl.read_csv("https://api.carbondata.dev/v1/emission-factors/table/ember.csv")');
    expect(part.javascript).toContain("const { meta, rows } = await res.json();");
  });
});

describe("files snippets", () => {
  it("start from the metadata and follow a file once one is known", () => {
    const meta = datasetSnippets({ siteId: "visualizedata", base: "https://api.visualizedata.dev", id: "boundaries", kind: "files" });
    expect(meta.curl).toContain('curl -s "https://api.visualizedata.dev/v1/boundaries"');
    expect(meta.python).toContain('json.load(urlopen("https://api.visualizedata.dev/v1/boundaries"))');
    const geo = datasetSnippets({ siteId: "visualizedata", base: "https://api.visualizedata.dev", id: "boundaries", kind: "files", file: "us/states.geojson" });
    expect(geo.curl).toContain('curl -sO "https://api.visualizedata.dev/v1/boundaries/us/states.geojson"');
    expect(geo.javascript).toContain("res.json()");
    const csv = datasetSnippets({ siteId: "visualizedata", base: "https://api.visualizedata.dev", id: "boundaries", kind: "files", file: "codes.csv" });
    expect(csv.python).toContain('pl.read_csv("https://api.visualizedata.dev/v1/boundaries/codes.csv")');
  });
});

describe("quickstart", () => {
  it("is three commands for a site with a series dataset", () => {
    const lines = quickstart({
      siteId: "climatedata",
      base: prod,
      first: { siteId: "climatedata", base: prod, id: "co2-monthly", kind: "series", series: "average", location: "noaa-mlo" },
    });
    expect(lines).toEqual([
      'curl -s "https://api.climatedata.dev/v1/catalog"',
      'curl -s "https://api.climatedata.dev/v1/co2-monthly"',
      'curl -s "https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.csv"',
    ]);
  });

  it("is three commands for tables, files and sites with no datasets", () => {
    const table = quickstart({ siteId: "carbondata", base: prod, first: { siteId: "carbondata", base: prod, id: "t", kind: "table" } });
    expect(table).toHaveLength(3);
    expect(table[2]).toContain("/v1/t/download/t.csv");
    const files = quickstart({ siteId: "visualizedata", base: prod, first: { siteId: "visualizedata", base: prod, id: "f", kind: "files" } });
    expect(files).toHaveLength(3);
    const none = quickstart({ siteId: "airdata", base: "https://api.airdata.dev" });
    expect(none).toEqual([
      'curl -s "https://api.airdata.dev/v1/catalog"',
      'curl -s "https://api.airdata.dev/v1/status"',
      'curl -s "https://api.airdata.dev/v1"',
    ]);
  });
});
