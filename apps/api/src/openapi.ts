// OpenAPI 3.1 document for one site, generated from the registry (the contracts). Deterministic: the same
// registry always yields byte-identical JSON, so the ETag is stable across isolates and deploys.

import type { Context } from "hono";
import { datasetsForSite, siteById, type Column, type Dataset, type Site } from "@datasites/registry";
import { isDevHost, type AppEnv } from "./middleware";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Obj = { [key: string]: Json };

export const OPENAPI_CACHE = "public, max-age=3600";
const VERSION_PATTERN = "^\\d{4}-\\d{2}-\\d{2}\\.[0-9a-f]{10}$";
const ref = (kind: string, name: string): Obj => ({ $ref: `#/components/${kind}/${name}` });
const schemaRef = (name: string) => ref("schemas", name);

function pascal(id: string): string {
  return id
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join("");
}

// ---- columns -> JSON Schema --------------------------------------------------------------------------

function baseColumnSchema(type: Column["type"]): Obj {
  switch (type) {
    case "month":
      return { type: "string", pattern: "^\\d{4}-\\d{2}$", examples: ["2026-09"] };
    case "date":
      return { type: "string", format: "date" };
    case "year":
      return { type: "integer", minimum: 0, maximum: 9999 };
    case "datetime":
      return { type: "string", format: "date-time" };
    case "float":
      return { type: "number" };
    case "int":
      return { type: "integer" };
    case "bool":
      return { type: "boolean" };
    case "str":
      return { type: "string" };
  }
}

export function columnSchema(col: Column): Obj {
  const schema = baseColumnSchema(col.type);
  if (col.nullable) schema.type = [schema.type as string, "null"];
  const parts = [col.description, col.unit ? `Unit: ${col.unit}.` : null].filter(Boolean);
  if (parts.length) schema.description = parts.join(" ");
  if (col.unit) schema["x-unit"] = col.unit;
  return schema;
}

function rowSchema(columns: Column[], title: string): Obj {
  const properties: Obj = {};
  for (const col of columns) properties[col.name] = columnSchema(col);
  return { title, type: "object", required: columns.map((c) => c.name), properties, additionalProperties: false };
}

// ---- parameters --------------------------------------------------------------------------------------

const LEVEL_PATTERNS: Record<string, string> = {
  world: "world",
  country: "[a-z]{3}",
  "us-state": "us-[a-z]{2}",
  "us-county": "us-county-\\d{5}",
};

function locationParameter(d: Dataset): Obj {
  const explicit = Object.keys(d.locations.explicit ?? {}).sort();
  const registry = d.locations.registry ?? [];
  const schema: Obj = { type: "string" };
  if (!registry.length && !d.locations.pattern && explicit.length && explicit.length <= 100) {
    schema.enum = explicit;
  } else {
    const alternatives = [
      ...registry.map((level) => LEVEL_PATTERNS[level] ?? null),
      ...(d.locations.pattern ? [d.locations.pattern] : []),
      ...explicit.map((id) => id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    ];
    if (alternatives.length && alternatives.every((a) => a !== null)) schema.pattern = `^(?:${alternatives.join("|")})$`;
  }
  return {
    name: "location",
    in: "path",
    required: true,
    description: `Location id. GET /v1/${d.id}/locations lists every location of every series.`,
    schema,
  };
}

function seriesParameter(d: Dataset): Obj {
  const ids = Object.keys(d.series).sort();
  return {
    name: "series",
    in: "path",
    required: true,
    description: ids.map((id) => `\`${id}\`: ${d.series[id]}`).join("; ") || "Series id.",
    schema: ids.length ? { type: "string", enum: ids } : { type: "string" },
  };
}

function partitionParameter(d: Dataset): Obj {
  const column = d.schema.find((c) => c.name === d.partition);
  return {
    name: "partition",
    in: "path",
    required: true,
    description: d.partition
      ? `A value of \`${d.partition}\`${column ? ` (${column.type})` : ""}, or \`all\` for the whole table when it has at most 50,000 rows. GET /v1/${d.id}/locations lists the partitions.`
      : "`all`: the table is not partitioned.",
    schema: d.partition ? { type: "string" } : { type: "string", enum: ["all"] },
  };
}

// ---- operations --------------------------------------------------------------------------------------

const ERRORS: Obj = {
  "401": ref("responses", "Unauthorized"),
  "403": ref("responses", "Forbidden"),
  "429": ref("responses", "TooManyRequests"),
  "500": ref("responses", "InternalError"),
};

function jsonResponse(description: string, schema: Obj, headers: Obj = {}): Obj {
  return {
    description,
    headers: { "X-Plan": ref("headers", "XPlan"), ...headers },
    content: { "application/json": { schema } },
  };
}

const VERSION_HEADERS: Obj = {
  "X-Data-Version": ref("headers", "XDataVersion"),
  Link: ref("headers", "Link"),
  "Last-Modified": ref("headers", "LastModified"),
  "Cache-Control": ref("headers", "CacheControl"),
};

const FILE_HEADERS: Obj = {
  ...VERSION_HEADERS,
  ETag: ref("headers", "ETag"),
  "Accept-Ranges": ref("headers", "AcceptRanges"),
  "Content-Length": ref("headers", "ContentLength"),
};

function op(operationId: string, summary: string, tags: string[], parameters: Json[], responses: Obj, description?: string): Obj {
  const o: Obj = { operationId, summary, tags, responses: { ...responses, ...ERRORS } };
  if (description) o.description = description;
  if (parameters.length) o.parameters = parameters;
  return { get: o };
}

function datasetPaths(d: Dataset, paths: Obj, schemas: Obj): void {
  const P = pascal(d.id);
  const tags = [d.id];
  const version = ref("parameters", "Version");
  const format = ref("parameters", "Format");
  const ifNoneMatch = ref("parameters", "IfNoneMatch");
  const range = ref("parameters", "Range");
  const notFound = { "404": ref("responses", "NotFound") };
  const unavailable = { "503": ref("responses", "ServiceUnavailable") };

  // Per-dataset data shapes, from the contract's columns.
  let dataSchema: Obj | null = null;
  if (d.kind === "series") {
    schemas[`${P}Row`] = rowSchema(d.schema, `${d.title}: one observation`);
    schemas[`${P}SeriesDocument`] = {
      title: `${d.title}: one series at one location`,
      type: "object",
      required: ["meta", "data"],
      properties: { meta: schemaRef("SeriesMeta"), data: { type: "array", items: schemaRef(`${P}Row`) } },
    };
    dataSchema = schemaRef(`${P}SeriesDocument`);
  } else if (d.kind === "table") {
    schemas[`${P}Row`] = rowSchema(d.schema, `${d.title}: one row`);
    schemas[`${P}TableDocument`] = {
      title: `${d.title}: one partition`,
      type: "object",
      required: ["meta", "rows"],
      properties: { meta: schemaRef("TableMeta"), rows: { type: "array", items: schemaRef(`${P}Row`) } },
    };
    dataSchema = schemaRef(`${P}TableDocument`);
  }

  const dataResponses = (what: string): Obj => ({
    "200": {
      description: what,
      headers: { "X-Plan": ref("headers", "XPlan"), ...FILE_HEADERS },
      content: {
        "application/json": { schema: dataSchema ?? {} },
        "text/csv": { schema: { type: "string" } },
      },
    },
    "304": ref("responses", "NotModified"),
    ...notFound,
    ...unavailable,
  });
  const fileResponses: Obj = {
    "200": {
      description: "The file.",
      headers: { "X-Plan": ref("headers", "XPlan"), ...FILE_HEADERS },
      content: { "*/*": { schema: { type: "string", contentMediaType: "application/octet-stream" } } },
    },
    "206": ref("responses", "PartialContent"),
    "304": ref("responses", "NotModified"),
    ...notFound,
    ...unavailable,
  };

  for (const pinned of [false, true]) {
    const base = pinned ? `/v1/${d.id}@{version}` : `/v1/${d.id}`;
    const suffix = pinned ? "Pinned" : "";
    const pin: Json[] = pinned ? [version] : [];
    const which = pinned ? "a pinned version" : "the current version";
    const pinNote = pinned
      ? "Pinned responses never change and are cacheable for a year (`Cache-Control: public, max-age=31536000, immutable`)."
      : undefined;

    paths[base] = op(
      `get${P}Metadata${suffix}`,
      `${d.title}: metadata of ${which}`,
      tags,
      pin,
      { "200": jsonResponse("Dataset metadata, provenance and links.", schemaRef("DatasetMetadata"), VERSION_HEADERS), ...notFound, ...unavailable },
      pinNote ?? d.description,
    );
    paths[`${base}/locations`] = op(
      `get${P}Locations${suffix}`,
      `${d.title}: series and locations in ${which}`,
      tags,
      pin,
      { "200": jsonResponse("Every series with its locations (tables: partitions under the pseudo-series `table`).", schemaRef("Locations"), VERSION_HEADERS), ...notFound, ...unavailable },
      pinNote,
    );
    paths[`${base}/download/{file}`] = op(
      `download${P}${suffix}`,
      `${d.title}: bulk file from ${which}`,
      tags,
      [
        ...pin,
        {
          name: "file",
          in: "path",
          required: true,
          description: `A bulk file name from the dataset metadata's \`downloads\`${d.kind === "files" ? "" : `, for example \`${d.id}.parquet\` or \`${d.id}.csv\``}. Supports Range, so DuckDB and polars can query Parquet remotely.`,
          schema: { type: "string" },
        },
        range,
        ifNoneMatch,
      ],
      fileResponses,
      pinNote,
    );
    if (d.kind === "series") {
      paths[`${base}/{series}/{location}.{format}`] = op(
        `get${P}Series${suffix}`,
        `${d.title}: one series at one location, from ${which}`,
        tags,
        [...pin, seriesParameter(d), locationParameter(d), format, ifNoneMatch],
        dataResponses("The series as JSON (`meta` plus `data` rows) or CSV, by the format extension."),
        pinNote,
      );
    } else if (d.kind === "table") {
      paths[`${base}/table/{partition}.{format}`] = op(
        `get${P}Table${suffix}`,
        `${d.title}: one table partition, from ${which}`,
        tags,
        [...pin, partitionParameter(d), format, ifNoneMatch],
        dataResponses(`Rows as JSON (\`meta\` plus \`rows\`) or CSV, by the format extension. Key: ${d.key.join(", ") || "none"}.`),
        pinNote,
      );
    } else {
      paths[`${base}/{folder}/{file}`] = op(
        `get${P}File${suffix}`,
        `${d.title}: a published file from ${which}`,
        tags,
        [
          ...pin,
          { name: "folder", in: "path", required: true, description: "First path segment of the file. Deeper paths, such as `perimeters/<id>/<snapshot>.geojson`, are served too.", schema: { type: "string" } },
          { name: "file", in: "path", required: true, description: "File name, with its extension.", schema: { type: "string" } },
          ifNoneMatch,
        ],
        fileResponses,
        pinNote,
      );
      paths[`${base}/{file}`] = op(
        `get${P}TopFile${suffix}`,
        `${d.title}: a published file at the top of the dataset (such as index.json), from ${which}`,
        tags,
        [
          ...pin,
          { name: "file", in: "path", required: true, description: "File name, with its extension.", schema: { type: "string", pattern: "^[^/]+\.[A-Za-z0-9]+$" } },
          ifNoneMatch,
        ],
        fileResponses,
        pinNote,
      );
    }
    if (!pinned) {
      paths[`${base}/versions`] = op(`get${P}Versions`, `${d.title}: version history`, tags, [], {
        "200": jsonResponse("Current and previous versions, and monthly snapshots.", schemaRef("Versions")),
        ...notFound,
        ...unavailable,
      });
    }
  }
}

// ---- shared components -------------------------------------------------------------------------------

const nullableString: Obj = { type: ["string", "null"] };
const coverage: Obj = {
  type: "object",
  required: ["start", "end"],
  properties: { start: nullableString, end: nullableString },
};

function commonSchemas(): Obj {
  return {
    Problem: {
      title: "RFC 9457 problem details",
      type: "object",
      required: ["type", "title", "status"],
      properties: {
        type: { type: "string", format: "uri", description: "Identifies the problem; the fragment is a stable slug such as `unknown-dataset`." },
        title: { type: "string" },
        status: { type: "integer", minimum: 400, maximum: 599 },
        detail: { type: "string" },
        instance: { type: "string", description: "Request path." },
        request_id: { type: ["string", "null"], description: "The cf-ray id; quote it when reporting a problem." },
        hint: { type: "string", description: "What to request instead." },
      },
      additionalProperties: true,
    },
    Index: {
      type: "object",
      required: ["site", "name", "api_version", "links"],
      properties: {
        site: { type: "string" },
        name: { type: "string" },
        tagline: { type: "string" },
        api_version: { type: "string", const: "v1" },
        links: { type: "object", additionalProperties: { type: "string" } },
      },
    },
    SourceSummary: {
      type: "object",
      required: ["name", "url", "license", "commercial_ok"],
      properties: {
        name: { type: "string" },
        url: { type: "string" },
        license: { type: "string", description: "SPDX id or LicenseRef-*." },
        commercial_ok: { type: ["boolean", "null"] },
      },
    },
    Source: {
      type: "object",
      required: ["id", "name", "url", "license", "commercial_ok", "attribution"],
      properties: {
        id: { type: "string" },
        name: { type: "string" },
        publisher: { type: ["string", "null"] },
        url: { type: "string" },
        license: { type: "string" },
        commercial_ok: { type: ["boolean", "null"] },
        attribution: { type: "string", description: "Exact attribution text to show with the data." },
        retrieved_at: { type: "string", format: "date-time" },
        source_updated_at: { type: ["string", "null"], format: "date-time" },
      },
    },
    Coverage: coverage,
    CatalogEntry: {
      type: "object",
      required: ["id", "title", "current", "cadence", "source", "coverage"],
      properties: {
        id: { type: "string" },
        kind: { type: "string", enum: ["series", "table", "files"] },
        title: { type: "string" },
        description: { type: "string" },
        current: nullableString,
        published_at: { type: ["string", "null"], format: "date-time" },
        cadence: { type: "string", description: "ISO 8601 duration." },
        check_every: { type: "string" },
        sla: { type: "string" },
        discontinued: { type: "boolean" },
        source: schemaRef("SourceSummary"),
        coverage: schemaRef("Coverage"),
      },
    },
    Catalog: {
      type: "object",
      required: ["site", "datasets"],
      properties: { site: { type: "string" }, datasets: { type: "array", items: schemaRef("CatalogEntry") } },
    },
    DatasetStatus: {
      type: "object",
      required: ["id", "status"],
      properties: {
        id: { type: "string" },
        status: { type: "string", enum: ["ok", "pending", "unpublished", "late", "stale-upstream", "failing", "blocked", "discontinued"] },
        version: nullableString,
        checked_at: { type: ["string", "null"], format: "date-time" },
        source_updated_at: { type: ["string", "null"], format: "date-time" },
        sla: { type: "string" },
        consecutive_failures: { type: "integer" },
        last_error: nullableString,
      },
    },
    Status: {
      type: "object",
      required: ["site", "generated_at", "overall", "datasets"],
      properties: {
        site: { type: "string" },
        generated_at: { type: "string", format: "date-time" },
        overall: { type: "string", enum: ["ok", "degraded"] },
        datasets: { type: "array", items: schemaRef("DatasetStatus") },
      },
    },
    DatasetMetadata: {
      type: "object",
      required: ["dataset", "kind", "title", "version", "current_version", "pinned", "source", "links"],
      properties: {
        dataset: { type: "string" },
        kind: { type: "string", enum: ["series", "table", "files"] },
        title: { type: "string" },
        description: { type: "string" },
        version: { type: "string", pattern: VERSION_PATTERN },
        current_version: { type: "string", pattern: VERSION_PATTERN },
        pinned: { type: "boolean" },
        published_at: { type: ["string", "null"], format: "date-time" },
        cadence: { type: "string" },
        source: schemaRef("Source"),
        coverage: schemaRef("Coverage"),
        validation: {
          type: "object",
          properties: { status: { type: "string", enum: ["pass", "warn", "blocked"] }, warnings: { type: "array", items: { type: "string" } } },
        },
        series: {
          type: "array",
          items: { type: "object", required: ["id", "name"], properties: { id: { type: "string" }, name: { type: "string" } } },
        },
        key: { type: "array", items: { type: "string" } },
        partition: nullableString,
        downloads: {
          type: "array",
          items: {
            type: "object",
            required: ["name", "bytes", "url"],
            properties: { name: { type: "string" }, bytes: { type: "integer" }, url: { type: "string" } },
          },
        },
        links: { type: "object", additionalProperties: { type: "string" } },
      },
    },
    Versions: {
      type: "object",
      required: ["dataset", "current", "previous", "snapshots"],
      properties: {
        dataset: { type: "string" },
        current: { type: "string", pattern: VERSION_PATTERN },
        published_at: { type: "string", format: "date-time" },
        previous: { type: "array", items: { type: "string", pattern: VERSION_PATTERN } },
        snapshots: {
          type: "object",
          description: "Month (YYYY-MM) -> the version current at the end of that month.",
          additionalProperties: { type: "string", pattern: VERSION_PATTERN },
        },
      },
    },
    Locations: {
      type: "object",
      required: ["dataset", "version", "series", "locations"],
      properties: {
        dataset: { type: "string" },
        version: { type: "string", pattern: VERSION_PATTERN },
        kind: { type: "string", enum: ["series", "table", "files"] },
        series: {
          type: "array",
          items: {
            type: "object",
            required: ["id", "name", "locations"],
            properties: { id: { type: "string" }, name: { type: "string" }, locations: { type: "array", items: { type: "string" } } },
          },
        },
        locations: {
          type: "array",
          items: { type: "object", required: ["id", "name"], properties: { id: { type: "string" }, name: { type: "string" } } },
        },
      },
    },
    SeriesMeta: {
      type: "object",
      description: "Fields that don't change between runs. Run-specific values (version, retrieval time) are in headers.",
      required: ["dataset", "series", "location"],
      properties: {
        dataset: { type: "string" },
        source: { type: "string" },
        license: { type: "string" },
        attribution: { type: "string" },
        series: { type: "string" },
        series_name: { type: "string" },
        location: { type: "string" },
        location_name: { type: "string" },
        units: { type: "object", additionalProperties: { type: "string" }, description: "Column -> unit." },
      },
    },
    TableMeta: {
      type: "object",
      required: ["dataset", "key", "rows"],
      properties: {
        dataset: { type: "string" },
        source: { type: "string" },
        license: { type: "string" },
        attribution: { type: "string" },
        key: { type: "array", items: { type: "string" } },
        units: { type: "object", additionalProperties: { type: "string" } },
        partition: { type: "object", description: "Partition column -> value; absent on `all`." },
        rows: { type: "integer" },
      },
    },
  };
}

function problemResponse(description: string, headers: Obj = {}): Obj {
  return {
    description,
    headers: { "X-Plan": ref("headers", "XPlan"), ...headers },
    content: { "application/problem+json": { schema: schemaRef("Problem") } },
  };
}

function components(schemas: Obj): Obj {
  return {
    schemas,
    parameters: {
      Version: {
        name: "version",
        in: "path",
        required: true,
        description: "A data version from GET /v1/{dataset}/versions, for example `2026-10-23.3f9a1c2e4b`.",
        schema: { type: "string", pattern: VERSION_PATTERN },
      },
      Format: {
        name: "format",
        in: "path",
        required: true,
        description: "Response format, chosen by extension.",
        schema: { type: "string", enum: ["json", "csv"] },
      },
      IfNoneMatch: {
        name: "If-None-Match",
        in: "header",
        required: false,
        description: "An ETag from an earlier response; answered with 304 when unchanged.",
        schema: { type: "string" },
      },
      Range: {
        name: "Range",
        in: "header",
        required: false,
        description: "A single byte range, for example `bytes=0-1023`.",
        schema: { type: "string" },
      },
    },
    headers: {
      XPlan: { description: "The plan the request was served under: anonymous, free or pro.", schema: { type: "string", enum: ["anonymous", "free", "pro"] } },
      XDataVersion: { description: "The data version served.", schema: { type: "string", pattern: VERSION_PATTERN } },
      ETag: { description: "Strong ETag: the sha256 of the file.", schema: { type: "string" } },
      LastModified: { description: "When the current version was published (current version only).", schema: { type: "string" } },
      Link: { description: "`rel=\"describedby\"` link to the dataset metadata.", schema: { type: "string" } },
      CacheControl: { description: "Up to an hour for current data; a year, immutable, for pinned versions.", schema: { type: "string" } },
      AcceptRanges: { schema: { type: "string", const: "bytes" } },
      ContentLength: { schema: { type: "integer" } },
      ContentRange: { schema: { type: "string" } },
      RetryAfter: { description: "Seconds to wait before retrying.", schema: { type: "integer" } },
      WWWAuthenticate: { schema: { type: "string" } },
    },
    responses: {
      NotModified: { description: "Unchanged since the ETag in If-None-Match.", headers: { ETag: ref("headers", "ETag") } },
      PartialContent: {
        description: "The requested byte range.",
        headers: { "Content-Range": ref("headers", "ContentRange"), "Content-Length": ref("headers", "ContentLength") },
        content: { "*/*": { schema: { type: "string", contentMediaType: "application/octet-stream" } } },
      },
      NotFound: problemResponse("Unknown dataset, version, file or format."),
      Unauthorized: problemResponse("The API key is malformed, unknown or revoked (`invalid-key`). Send no key to use anonymous access.", {
        "WWW-Authenticate": ref("headers", "WWWAuthenticate"),
      }),
      Forbidden: problemResponse("The API key is valid but its account is suspended (`account-suspended`)."),
      TooManyRequests: problemResponse("Rate limit reached (`rate-limited`).", { "Retry-After": ref("headers", "RetryAfter") }),
      InternalError: problemResponse("Unexpected error."),
      ServiceUnavailable: problemResponse("The data, or the key store, is temporarily unavailable.", { "Retry-After": ref("headers", "RetryAfter") }),
    },
    securitySchemes: {
      ApiKeyHeader: { type: "apiKey", in: "header", name: "X-API-Key", description: "A key of the form `dk_live_` + 32 base32 characters." },
      BearerAuth: { type: "http", scheme: "bearer", description: "The same key as `Authorization: Bearer <key>`." },
    },
  };
}

function description(site: Site): string {
  return [
    site.description,
    "",
    "**Access.** No key is needed: anonymous requests are rate-limited per IP address. A free API key raises the limit; send it as `X-API-Key: <key>` or `Authorization: Bearer <key>`. Every response carries `X-Plan` with the plan it was served under. A malformed, unknown or revoked key gets 401; leave the key off to fall back to anonymous access. A 429 carries `Retry-After`.",
    "",
    "**Versions.** Every dataset path also exists pinned to a data version, `/v1/{dataset}@{version}/...` (versions from `/v1/{dataset}/versions`). Pinned responses never change and may be cached for a year. Unpinned responses follow the current version and are cached for at most an hour.",
    "",
    "**Formats.** The path extension picks the format: `.json` or `.csv`. Bulk files under `download/` support `Range` and `HEAD`.",
    "",
    "**Errors.** Every error is an RFC 9457 `application/problem+json` document.",
  ].join("\n");
}

export function buildOpenApi(site: Site, siteDatasets: readonly Dataset[] = datasetsForSite(site.id)): Obj {
  const schemas = commonSchemas();
  const paths: Obj = {};
  const site_ = ["site"];
  paths["/v1"] = op("getIndex", "API index with links", site_, [], { "200": jsonResponse("Index.", schemaRef("Index")) });
  paths["/v1/catalog"] = op("getCatalog", "Every dataset on this site", site_, [], { "200": jsonResponse("The catalog.", schemaRef("Catalog")) });
  paths["/v1/status"] = op("getStatus", "Freshness of every dataset", site_, [], { "200": jsonResponse("Status per dataset.", schemaRef("Status")) });
  paths["/v1/openapi.json"] = op("getOpenApi", "This OpenAPI document", site_, [ref("parameters", "IfNoneMatch")], {
    "200": jsonResponse("OpenAPI 3.1 document.", { type: "object" }, { ETag: ref("headers", "ETag") }),
    "304": ref("responses", "NotModified"),
  });

  const datasets = [...siteDatasets].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const d of datasets) datasetPaths(d, paths, schemas);

  return {
    openapi: "3.1.0",
    info: {
      title: `${site.name} API`,
      version: "v1",
      summary: site.tagline,
      description: description(site),
    },
    servers: [{ url: `https://api.${site.domain}`, description: site.name }],
    // Anonymous ({}) or either key scheme.
    security: [{}, { ApiKeyHeader: [] }, { BearerAuth: [] }],
    tags: [
      { name: "site", description: "Site-wide endpoints." },
      ...datasets.map((d) => ({ name: d.id, description: `${d.title} (${d.kind}). ${d.source.name}; license ${d.source.license}.` })),
    ],
    paths,
    components: components(schemas),
  };
}

// ---- serving -----------------------------------------------------------------------------------------

const rendered = new Map<string, { body: string; etag: string }>();

/** The spec as served. `devOrigin` (workers.dev staging, localhost) replaces the production server URL. */
async function render(siteId: string, devOrigin: string | null): Promise<{ body: string; etag: string } | null> {
  const cacheKey = `${siteId} ${devOrigin ?? ""}`;
  const hit = rendered.get(cacheKey);
  if (hit) return hit;
  const site = siteById(siteId);
  if (!site) return null;
  const spec = buildOpenApi(site);
  if (devOrigin) {
    spec.servers = [{ url: devOrigin, description: `${site.name}, not production: add ?site=${site.id} to every request` }];
  }
  const body = JSON.stringify(spec);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
  const etag = `"${[...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("")}"`;
  const out = { body, etag };
  rendered.set(cacheKey, out);
  return out;
}

export async function openapi(c: Context<AppEnv>): Promise<Response> {
  const origin = new URL(c.req.url).origin;
  const doc = await render(c.get("site").id, isDevHost(new URL(origin).hostname) ? origin : null);
  if (!doc) throw new Error("site vanished from the registry");
  const headers = { "content-type": "application/json; charset=utf-8", "cache-control": OPENAPI_CACHE, etag: doc.etag };
  const inm = c.req.header("if-none-match");
  if (inm && inm.split(",").some((t) => t.trim().replace(/^W\//, "") === doc.etag)) return new Response(null, { status: 304, headers });
  return new Response(doc.body, { headers });
}
