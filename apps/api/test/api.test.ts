import { beforeEach, describe, expect, it } from "vitest";
import { app } from "../src/index";
import { clearMemo } from "../src/store";
import { keys } from "../src/types";
import { BULK_CSV, CURRENT, MLO_CSV, MLO_JSON, OLD, BLOCKED, seededBucket } from "./fixtures";
import type { FakeR2 } from "./fake-r2";

let r2: FakeR2;

function request(path: string, init: RequestInit = {}, env: Record<string, unknown> = {}) {
  const url = path.startsWith("http") ? path : `http://localhost${path}${path.includes("?") ? "&" : "?"}site=climatedata`;
  return app.request(url, init, { DATA: r2.asBucket(), ENV: "production", ...env } as unknown as Env);
}

beforeEach(() => {
  clearMemo();
  r2 = seededBucket();
});

describe("site resolution", () => {
  it("serves a site by its api hostname", async () => {
    const res = await request("https://api.climatedata.dev/v1");
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ site: "climatedata", api_version: "v1" });
  });

  it("accepts ?site= on localhost", async () => {
    const res = await request("/v1");
    expect((await res.json()) as object).toMatchObject({ site: "climatedata" });
  });

  it("rejects unknown hosts with a problem document", async () => {
    const res = await request("https://api.example.com/v1");
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    expect(await res.json()).toMatchObject({ title: "Unknown site", status: 404 });
  });

  it("ignores ?site= on production hostnames", async () => {
    const res = await request("https://api.example.com/v1?site=climatedata");
    expect(res.status).toBe(404);
    const other = await request("https://api.waterdata.dev/v1?site=climatedata");
    expect(await other.json()).toMatchObject({ site: "waterdata" });
  });

  it("honors ?site= on any hostname in local development (wrangler dev rewrites the host)", async () => {
    const res = await request("https://api.climatedata.dev/v1?site=waterdata", {}, { ENV: "development" });
    expect(await res.json()).toMatchObject({ site: "waterdata" });
  });
});

describe("data files", () => {
  it("serves the current version of a series file", async () => {
    const res = await request("/v1/co2-monthly/average/noaa-mlo.json");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(MLO_JSON);
    expect(res.headers.get("etag")).toBe(`"${"1".repeat(64)}"`);
    expect(res.headers.get("x-data-version")).toBe(CURRENT);
    expect(res.headers.get("cache-control")).toContain("max-age=3600");
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    expect(res.headers.get("last-modified")).toBe(new Date("2026-10-09T19:30:00Z").toUTCString());
  });

  it("serves CSV by extension", async () => {
    const res = await request("/v1/co2-monthly/average/noaa-mlo.csv");
    expect(res.headers.get("content-type")).toBe("text/csv; charset=utf-8");
    expect(await res.text()).toBe(MLO_CSV);
  });

  it("answers If-None-Match with 304 without reading the blob", async () => {
    await request("/v1/co2-monthly/average/noaa-mlo.json"); // warm the manifest/version memo
    const readsBefore = r2.reads;
    const res = await request("/v1/co2-monthly/average/noaa-mlo.json", {
      headers: { "if-none-match": `"${"1".repeat(64)}"` },
    });
    expect(res.status).toBe(304);
    expect(r2.reads).toBe(readsBefore);
  });

  it("serves pinned versions as immutable", async () => {
    const res = await request(`/v1/co2-monthly@${OLD}/average/noaa-mlo.json`);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("old");
    expect(res.headers.get("cache-control")).toBe("public, max-age=31536000, immutable");
    expect(res.headers.get("last-modified")).toBeNull();
  });

  it("hides quarantined versions in production but not on staging", async () => {
    expect((await request(`/v1/co2-monthly@${BLOCKED}/average/noaa-mlo.json`)).status).toBe(404);
    clearMemo();
    const staging = await request(`/v1/co2-monthly@${BLOCKED}/average/noaa-mlo.json`, {}, { ENV: "staging" });
    expect(staging.status).toBe(200);
  });

  it("supports byte ranges on bulk downloads", async () => {
    const res = await request("/v1/co2-monthly/download/co2-monthly.csv", { headers: { range: "bytes=0-5" } });
    expect(res.status).toBe(206);
    expect(res.headers.get("content-range")).toBe(`bytes 0-5/${BULK_CSV.length}`);
    expect(await res.text()).toBe(BULK_CSV.slice(0, 6));
  });

  it("returns problem documents for unknown datasets, files and formats", async () => {
    const ds = await request("/v1/nope/average/noaa-mlo.json");
    expect(ds.status).toBe(404);
    expect(await ds.json()).toMatchObject({ title: "Unknown dataset", hint: "GET /v1/catalog" });

    const file = await request("/v1/co2-monthly/average/nowhere.json");
    expect(file.status).toBe(404);
    expect(await file.json()).toMatchObject({ title: "Not found", hint: "GET /v1/co2-monthly/locations" });

    const fmt = await request("/v1/co2-monthly/average/noaa-mlo.xml");
    expect(fmt.status).toBe(404);

    const ver = await request("/v1/co2-monthly@not-a-version");
    expect(ver.status).toBe(404);
    expect(await ver.json()).toMatchObject({ title: "Unknown version" });
  });
});

describe("big and non-series datasets", () => {
  function seedBig() {
    const json = (v: unknown) => JSON.stringify(v);
    const blob = (n: number) => String(n).repeat(64).slice(0, 64);
    r2.putText(keys.blob(blob(5)), "county-data");
    r2.putText(keys.blob(blob(6)), '{"type":"FeatureCollection","features":[]}');
    r2.putText(keys.blob(blob(7)), json({ "tavg/us-county-06037.json": { blob: blob(5), bytes: 11, rows: 1, type: "application/json" } }));
    r2.putText(keys.blob(blob(8)), json({ "us-county-06037": "Los Angeles County" }));
    r2.putText(keys.blob(blob(9)), json({ "table/x.json": { blob: blob(5), bytes: 11, rows: 2, type: "application/json" } }));
    const version = "2026-10-24.dddddddddd";
    r2.putJson(keys.manifest("climatedata", "county-climate"), {
      site: "climatedata", dataset: "county-climate", current: version, published_at: "2026-10-24T12:00:00Z",
      previous: [], snapshots: {}, quarantined: [], rev: 1,
    });
    r2.putJson(keys.version("climatedata", "county-climate", version), {
      version, site: "climatedata", dataset: "county-climate", kind: "series", title: "County climate", description: "",
      cadence: "P1M", contract_version: 1, transform_version: "t", coverage: { start: null, end: null },
      source: { id: "s", name: "s", url: "", license: "x", commercial_ok: true, attribution: "" },
      series: { tavg: "Average temperature" }, validation: { status: "pass", errors: [], warnings: [] }, run: null, tree: "t",
      files: { "download/county-climate.parquet": { blob: blob(5), bytes: 11, type: "application/vnd.apache.parquet" },
               "boundaries/states.geojson": { blob: blob(6), bytes: 42, type: "application/geo+json" },
               "index.json": { blob: blob(6), bytes: 42, type: "application/json" } },
      file_shards: { tavg: blob(7), table: blob(9) },
      locations_blob: blob(8),
    });
  }

  it("finds files through their series shard", async () => {
    seedBig();
    const res = await request("/v1/county-climate/tavg/us-county-06037.json");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("county-data");
    expect((await request("/v1/county-climate/tavg/us-county-99999.json")).status).toBe(404);
    expect((await request("/v1/county-climate/precip/us-county-06037.json")).status).toBe(404); // no such shard
  });

  it("serves table partitions and files with any extension", async () => {
    seedBig();
    expect((await request("/v1/county-climate/table/x.json")).status).toBe(200);
    const geo = await request("/v1/county-climate/boundaries/states.geojson");
    expect(geo.status).toBe(200);
    expect(geo.headers.get("content-type")).toBe("application/geo+json");
    expect((await request("/v1/county-climate/tavg/us-county-06037")).status).toBe(404); // no extension
  });

  it("serves a file at the top of the dataset, without shadowing versions or locations", async () => {
    seedBig();
    const top = await request("/v1/county-climate/index.json");
    expect(top.status).toBe(200);
    expect(top.headers.get("content-type")).toBe("application/json");
    expect((await request("/v1/county-climate/missing.json")).status).toBe(404);
    expect((await request("/v1/county-climate/locations")).status).toBe(200);
  });

  it("lists locations from the shards and the locations blob", async () => {
    seedBig();
    const body = (await (await request("/v1/county-climate/locations")).json()) as {
      kind: string;
      series: { id: string; locations: string[] }[];
      locations: { id: string; name: string }[];
    };
    expect(body.kind).toBe("series");
    expect(body.series.find((s) => s.id === "tavg")?.locations).toEqual(["us-county-06037"]);
    expect(body.locations).toEqual([{ id: "us-county-06037", name: "Los Angeles County" }]);
  });
});

describe("metadata endpoints", () => {
  it("lists datasets in the catalog", async () => {
    const body = (await (await request("/v1/catalog")).json()) as { datasets: { id: string }[] };
    expect(body.datasets.map((d) => d.id)).toEqual(["co2-monthly"]);
  });

  it("describes a dataset with its downloads and provenance", async () => {
    const body = (await (await request("/v1/co2-monthly")).json()) as Record<string, unknown>;
    expect(body).toMatchObject({
      dataset: "co2-monthly",
      version: CURRENT,
      pinned: false,
      source: { license: "LicenseRef-US-Government-Work", commercial_ok: true },
      downloads: [{ name: "co2-monthly.csv", url: "/v1/co2-monthly/download/co2-monthly.csv" }],
    });
  });

  it("lists series and locations from the files in the version", async () => {
    const body = (await (await request("/v1/co2-monthly/locations")).json()) as {
      series: { id: string; locations: string[] }[];
    };
    expect(body.series).toEqual([
      { id: "average", name: "Monthly mean", locations: ["noaa-mlo"] },
      { id: "trend", name: "Trend", locations: ["world"] },
    ]);
  });

  it("lists versions", async () => {
    const body = await (await request("/v1/co2-monthly/versions")).json();
    expect(body).toMatchObject({ current: CURRENT, previous: [OLD] });
  });

  it("reports dataset freshness", async () => {
    const res = await request("/v1/status");
    expect(res.headers.get("cache-control")).toBe("public, max-age=60");
    const body = (await res.json()) as { datasets: { id: string; status: string }[] };
    expect(body.datasets[0]).toMatchObject({ id: "co2-monthly" });
  });

  it("answers CORS preflight", async () => {
    const res = await request("/v1/co2-monthly", { method: "OPTIONS" });
    expect(res.status).toBe(204);
    expect(res.headers.get("access-control-allow-methods")).toContain("GET");
  });

  it("returns a problem document for unknown endpoints", async () => {
    const res = await request("/v2/whatever/else/entirely/here");
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
  });
});
