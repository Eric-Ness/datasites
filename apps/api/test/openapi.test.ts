import { describe, expect, it } from "vitest";
import { datasetsForSite, sites, siteById, type Dataset } from "@datasites/registry";
import { app } from "../src/index";
import { buildOpenApi, columnSchema } from "../src/openapi";
import { seededBucket } from "./fixtures";

type Doc = Record<string, any>;

function request(path: string, init: RequestInit = {}, site = "climatedata") {
  return app.request(`http://localhost${path}?site=${site}`, init, { DATA: seededBucket().asBucket(), ENV: "production" } as unknown as Env);
}

/** Every $ref in the document must point at something that exists. */
function unresolvedRefs(doc: Doc): string[] {
  const missing: string[] = [];
  const walk = (node: unknown) => {
    if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === "object") {
      for (const [k, v] of Object.entries(node)) {
        if (k === "$ref" && typeof v === "string") {
          const target = v
            .replace(/^#\//, "")
            .split("/")
            .reduce<any>((at, part) => at?.[part.replace(/~1/g, "/").replace(/~0/g, "~")], doc);
          if (target === undefined) missing.push(v);
        } else walk(v);
      }
    }
  };
  walk(doc);
  return missing;
}

function operationIds(doc: Doc): string[] {
  return Object.values(doc.paths as Record<string, Doc>).flatMap((item) => Object.values(item).map((op: Doc) => op.operationId));
}

const base = (over: Partial<Dataset>): Dataset => ({
  site: "climatedata",
  id: "x",
  kind: "series",
  title: "X",
  description: "",
  cadence: "P1D",
  check_every: "P1D",
  sla: "P2D",
  discontinued: false,
  source: { name: "S", publisher: null, url: "https://example.org", license: "CC0-1.0", commercial_ok: true, attribution: "S" },
  series: {},
  locations: {},
  schema: [],
  key: [],
  partition: null,
  ...over,
});

const TABLE = base({
  id: "county-population",
  kind: "table",
  title: "County population",
  key: ["fips", "year"],
  partition: "year",
  schema: [
    { name: "fips", type: "str", unit: null, nullable: false, description: "5-digit FIPS" },
    { name: "year", type: "year", unit: null, nullable: false, description: null },
    { name: "population", type: "int", unit: "people", nullable: true, description: null },
    { name: "as_of", type: "date", unit: null, nullable: false, description: null },
    { name: "updated", type: "datetime", unit: null, nullable: true, description: null },
  ],
});
const FILES = base({ id: "boundaries", kind: "files", title: "Boundaries" });
const COUNTY = base({
  id: "county-climate",
  series: { tavg: "Average temperature", pcp: "Precipitation" },
  locations: { registry: ["us-county", "us-state"] },
  schema: [{ name: "date", type: "month", unit: null, nullable: false, description: null }],
});

describe("GET /v1/openapi.json", () => {
  it("serves OpenAPI 3.1 for the site, cacheable for an hour, with an ETag", async () => {
    const res = await request("/v1/openapi.json");
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("public, max-age=3600");
    expect(res.headers.get("content-type")).toContain("application/json");
    const doc = (await res.json()) as Doc;
    expect(doc.openapi).toBe("3.1.0");
    expect(doc.info.title).toBe(`${siteById("climatedata")!.name} API`);
    expect(doc.servers).toEqual([{ url: "https://api.climatedata.dev", description: siteById("climatedata")!.name }]);
    for (const p of ["/v1", "/v1/catalog", "/v1/status", "/v1/openapi.json"]) expect(doc.paths[p]).toBeDefined();

    const again = await request("/v1/openapi.json", { headers: { "if-none-match": res.headers.get("etag")! } });
    expect(again.status).toBe(304);
  });

  it("is not mistaken for a dataset called openapi.json", async () => {
    const res = await request("/v1/openapi.json");
    expect(((await res.json()) as Doc).openapi).toBeDefined();
  });

  it("is deterministic", () => {
    const site = siteById("climatedata")!;
    expect(JSON.stringify(buildOpenApi(site))).toBe(JSON.stringify(buildOpenApi(site)));
  });

  it("is valid for every site: dataset paths, resolvable $refs, unique operationIds", () => {
    for (const site of sites) {
      const doc = buildOpenApi(site) as Doc;
      expect(doc.servers[0].url).toBe(`https://api.${site.domain}`);
      for (const d of datasetsForSite(site.id)) {
        expect(doc.paths[`/v1/${d.id}`], `${site.id}/${d.id}`).toBeDefined();
        expect(doc.paths[`/v1/${d.id}@{version}`]).toBeDefined();
      }
      expect(unresolvedRefs(doc)).toEqual([]);
      const ids = operationIds(doc);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it("documents series datasets with the series as an enum and rows from the contract", () => {
    const doc = buildOpenApi(siteById("climatedata")!) as Doc;
    const op = doc.paths["/v1/co2-monthly/{series}/{location}.{format}"].get;
    const series = op.parameters.find((p: Doc) => p.name === "series");
    expect(series.schema.enum).toEqual(["average", "trend"]);
    const location = op.parameters.find((p: Doc) => p.name === "location");
    expect(location.schema.enum).toEqual(["noaa-mlo", "world"]);
    const row = doc.components.schemas.Co2MonthlyRow;
    expect(row.properties.date).toMatchObject({ type: "string", pattern: "^\\d{4}-\\d{2}$" });
    expect(row.properties.value).toMatchObject({ type: "number", "x-unit": "ppm" });
    expect(row.properties.uncertainty.type).toEqual(["number", "null"]);
    expect(row.properties.interpolated.type).toEqual(["boolean", "null"]);
    for (const p of ["/v1/co2-monthly/versions", "/v1/co2-monthly/locations", "/v1/co2-monthly/download/{file}", "/v1/co2-monthly@{version}/{series}/{location}.{format}"]) {
      expect(doc.paths[p], p).toBeDefined();
    }
    expect(op.responses["404"]).toEqual({ $ref: "#/components/responses/NotFound" });
    expect(doc.components.schemas.Problem.required).toEqual(["type", "title", "status"]);
  });

  it("documents table, files and registry-located datasets", () => {
    const doc = buildOpenApi(siteById("climatedata")!, [TABLE, FILES, COUNTY]) as Doc;
    expect(doc.paths["/v1/county-population/table/{partition}.{format}"]).toBeDefined();
    expect(doc.paths["/v1/county-population@{version}/table/{partition}.{format}"]).toBeDefined();
    expect(doc.paths["/v1/boundaries/{folder}/{file}"]).toBeDefined();
    const row = doc.components.schemas.CountyPopulationRow;
    expect(row.required).toEqual(["fips", "year", "population", "as_of", "updated"]);
    expect(row.properties.year).toMatchObject({ type: "integer" });
    expect(row.properties.population).toMatchObject({ type: ["integer", "null"], "x-unit": "people" });
    expect(row.properties.as_of).toMatchObject({ type: "string", format: "date" });
    expect(row.properties.updated).toMatchObject({ type: ["string", "null"], format: "date-time" });
    const loc = doc.paths["/v1/county-climate/{series}/{location}.{format}"].get.parameters.find((p: Doc) => p.name === "location");
    expect(loc.schema.enum).toBeUndefined();
    expect(new RegExp(loc.schema.pattern).test("us-county-06037")).toBe(true);
    expect(new RegExp(loc.schema.pattern).test("us-ca")).toBe(true);
    expect(new RegExp(loc.schema.pattern).test("usa")).toBe(false);
    expect(unresolvedRefs(doc)).toEqual([]);
  });

  it("allows anonymous access and offers both key schemes", () => {
    const doc = buildOpenApi(siteById("climatedata")!) as Doc;
    expect(doc.security).toEqual([{}, { ApiKeyHeader: [] }, { BearerAuth: [] }]);
    expect(doc.components.securitySchemes.ApiKeyHeader).toMatchObject({ type: "apiKey", in: "header", name: "X-API-Key" });
    expect(doc.components.securitySchemes.BearerAuth).toMatchObject({ type: "http", scheme: "bearer" });
  });

  it("maps every column type", () => {
    const col = (type: any) => columnSchema({ name: "c", type, unit: null, nullable: false, description: null });
    expect(col("bool")).toEqual({ type: "boolean" });
    expect(col("str")).toEqual({ type: "string" });
    expect(col("float")).toEqual({ type: "number" });
    expect(col("int")).toEqual({ type: "integer" });
  });
});
