import { describe, expect, it } from "vitest";
import { apiBaseFor, apiUrl, datasetRef, isDevHost, needsSiteParam, paths, resolveApiLink, siteUrl } from "../src/lib/urls";

describe("apiBaseFor", () => {
  it("defaults to https://api.<domain>", () => {
    expect(apiBaseFor("climatedata.dev")).toBe("https://api.climatedata.dev");
    expect(apiBaseFor("climatedata.dev", "")).toBe("https://api.climatedata.dev");
    expect(apiBaseFor("climatedata.dev", "   ")).toBe("https://api.climatedata.dev");
  });

  it("uses PUBLIC_API_BASE when set, without a trailing slash", () => {
    expect(apiBaseFor("climatedata.dev", "http://localhost:8787/")).toBe("http://localhost:8787");
    expect(apiBaseFor("climatedata.dev", "https://example.com/api/")).toBe("https://example.com/api");
  });

  it("rejects non-http bases", () => {
    expect(() => apiBaseFor("climatedata.dev", "ftp://localhost")).toThrow(/http/);
    expect(() => apiBaseFor("climatedata.dev", "not a url")).toThrow();
  });
});

describe("dev hosts", () => {
  it("recognises local and preview hosts like the API middleware does", () => {
    for (const host of ["localhost", "127.0.0.1", "[::1]", "web.localhost", "datasites-api-staging.someone.workers.dev"]) {
      expect(isDevHost(host), host).toBe(true);
    }
    for (const host of ["api.climatedata.dev", "climatedata.dev", "localhost.example.com"]) {
      expect(isDevHost(host), host).toBe(false);
    }
  });

  it("needsSiteParam follows the base's hostname", () => {
    expect(needsSiteParam("http://localhost:8787")).toBe(true);
    expect(needsSiteParam("https://api.waterdata.dev")).toBe(false);
  });
});

describe("apiUrl", () => {
  it("builds production URLs without ?site=", () => {
    expect(apiUrl("https://api.climatedata.dev", "climatedata", "/v1/catalog")).toBe("https://api.climatedata.dev/v1/catalog");
    expect(apiUrl("https://api.climatedata.dev", "climatedata", paths.series("co2-monthly", "average", "noaa-mlo"))).toBe(
      "https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.json",
    );
  });

  it("adds ?site=<id> on localhost, 127.0.0.1 and workers.dev", () => {
    expect(apiUrl("http://localhost:8787", "climatedata", "/v1/catalog")).toBe("http://localhost:8787/v1/catalog?site=climatedata");
    expect(apiUrl("http://127.0.0.1:8787", "waterdata", "/v1/status")).toBe("http://127.0.0.1:8787/v1/status?site=waterdata");
    expect(apiUrl("https://x.y.workers.dev", "airdata", "/v1")).toBe("https://x.y.workers.dev/v1?site=airdata");
  });

  it("keeps a base path and requires a leading slash", () => {
    expect(apiUrl("https://example.com/api", "climatedata", "/v1/catalog")).toBe("https://example.com/api/v1/catalog");
    expect(() => apiUrl("https://example.com", "climatedata", "v1/catalog")).toThrow();
  });
});

describe("paths", () => {
  it("covers every API path", () => {
    expect(paths.root()).toBe("/v1");
    expect(paths.status()).toBe("/v1/status");
    expect(paths.dataset("co2-monthly")).toBe("/v1/co2-monthly");
    expect(paths.dataset("co2-monthly", "2026-10-24.d0afea5995")).toBe("/v1/co2-monthly@2026-10-24.d0afea5995");
    expect(paths.versions("co2-monthly")).toBe("/v1/co2-monthly/versions");
    expect(paths.locations("co2-monthly")).toBe("/v1/co2-monthly/locations");
    expect(paths.series("co2-monthly", "trend", "world", "csv")).toBe("/v1/co2-monthly/trend/world.csv");
    expect(paths.table("emission-factors", "epa-hub")).toBe("/v1/emission-factors/table/epa-hub.json");
    expect(paths.download("co2-monthly", "parquet")).toBe("/v1/co2-monthly/download/co2-monthly.parquet");
    expect(paths.file("boundaries", "us/states.geojson")).toBe("/v1/boundaries/us/states.geojson");
  });

  it("percent-encodes partition values and versions", () => {
    expect(paths.table("t", "Scope 1/2")).toBe("/v1/t/table/Scope%201%2F2.json");
    expect(datasetRef("x", "a b")).toBe("x@a%20b");
  });
});

describe("links", () => {
  it("siteUrl is on the apex domain", () => {
    expect(siteUrl("waterdata.dev")).toBe("https://waterdata.dev/");
    expect(siteUrl("waterdata.dev", "/datasets/")).toBe("https://waterdata.dev/datasets/");
    expect(siteUrl("waterdata.dev", "docs/")).toBe("https://waterdata.dev/docs/");
  });

  it("resolveApiLink turns API-relative links into full URLs for the page's base", () => {
    expect(resolveApiLink("https://api.climatedata.dev", "climatedata", "/v1/co2-monthly/download/co2-monthly.csv")).toBe(
      "https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.csv",
    );
    expect(resolveApiLink("http://localhost:8787", "climatedata", "/v1/x?y=1")).toBe("http://localhost:8787/v1/x?site=climatedata&y=1");
    expect(resolveApiLink("http://localhost:8787", "climatedata", "https://elsewhere.example/a")).toBe("https://elsewhere.example/a");
  });
});
