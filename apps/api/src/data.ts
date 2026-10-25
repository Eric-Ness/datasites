import type { Context } from "hono";
import { blobCacheKey, cacheable, matchBlob, putBlob } from "./cache";
import { cacheControl, defer, json, problem, shortCache } from "./http";
import { siteLink, type AppEnv } from "./middleware";
import { readJson, TTL } from "./store";
import { keys, type Catalog, type FileEntry, type Manifest, type VersionDoc } from "./types";

type C = Context<AppEnv>;

const DATASET_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
const VERSION_ID = /^\d{4}-\d{2}-\d{2}\.[0-9a-f]{10}$/;
const SERIES_FILE = /^([^/]+)\/([^/]+)\.json$/;

interface Resolved {
  dataset: string;
  manifest: Manifest;
  doc: VersionDoc;
  pinned: boolean;
}

function unknownDataset(c: C, dataset: string): Response {
  return problem(c, 404, "unknown-dataset", "Unknown dataset", `There is no dataset "${dataset}" on this site.`, {
    hint: "GET /v1/catalog",
  });
}

/** "co2-monthly" -> current version; "co2-monthly@2026-10-23.3f9a1c2e4b" -> that version. */
async function resolve(c: C, ref: string): Promise<Resolved | Response> {
  const at = ref.indexOf("@");
  const dataset = at === -1 ? ref : ref.slice(0, at);
  const version = at === -1 ? null : ref.slice(at + 1);
  const site = c.get("site");

  if (!DATASET_ID.test(dataset)) return unknownDataset(c, dataset);
  const unknownVersion = () =>
    problem(c, 404, "unknown-version", "Unknown version", `${dataset} has no version "${version}".`, {
      hint: `GET /v1/${dataset}/versions`,
    });
  if (version !== null && !VERSION_ID.test(version)) return unknownVersion();

  const manifest = await readJson<Manifest>(c.env.DATA, keys.manifest(site.id, dataset), TTL.manifest);
  if (!manifest) return unknownDataset(c, dataset);

  const doc = await readJson<VersionDoc>(
    c.env.DATA,
    keys.version(site.id, dataset, version ?? manifest.current),
    TTL.immutable,
  );
  if (!doc) {
    if (version !== null) return unknownVersion();
    return problem(c, 503, "data-unavailable", "Data temporarily unavailable", `${dataset} has no readable current version.`);
  }
  // Quarantined versions can be inspected on staging, never in production.
  if (doc.validation.status === "blocked" && c.env.ENV === "production") return unknownVersion();

  return { dataset, manifest, doc, pinned: version !== null };
}

/** A file's entry, from the version doc or (for big datasets) from its series shard. */
async function fileEntry(c: C, doc: VersionDoc, path: string): Promise<FileEntry | undefined> {
  const direct = doc.files[path];
  if (direct || !doc.file_shards) return direct;
  const shard = doc.file_shards[path.split("/", 1)[0]!];
  if (!shard) return undefined;
  const map = await readJson<Record<string, FileEntry>>(c.env.DATA, keys.blob(shard), TTL.immutable);
  return map?.[path];
}

/** Every file path in a version, loading all shards for big datasets. */
async function allFiles(c: C, doc: VersionDoc): Promise<Record<string, FileEntry>> {
  if (!doc.file_shards) return doc.files;
  const shards = await Promise.all(
    Object.values(doc.file_shards).map((hash) => readJson<Record<string, FileEntry>>(c.env.DATA, keys.blob(hash), TTL.immutable)),
  );
  return Object.assign({}, doc.files, ...shards.map((s) => s ?? {}));
}

async function locationNames(c: C, doc: VersionDoc): Promise<Record<string, string>> {
  if (doc.locations) return doc.locations;
  if (!doc.locations_blob) return {};
  return (await readJson<Record<string, string>>(c.env.DATA, keys.blob(doc.locations_blob), TTL.immutable)) ?? {};
}

function versionHeaders(c: C, r: Resolved): Record<string, string> {
  const headers: Record<string, string> = {
    "x-data-version": r.doc.version,
    link: `</v1/${r.dataset}>; rel="describedby"`,
  };
  if (r.doc.version === r.manifest.current) headers["last-modified"] = new Date(r.manifest.published_at).toUTCString();
  return headers;
}

function etagMatches(header: string | undefined, etag: string): boolean {
  if (!header) return false;
  if (header.trim() === "*") return true;
  return header.split(",").some((tag) => tag.trim().replace(/^W\//, "") === etag);
}

function rangeOf(range: R2Range, size: number): { offset: number; length: number } {
  const r = range as { offset?: number; length?: number; suffix?: number };
  if (r.suffix !== undefined) return { offset: size - r.suffix, length: r.suffix };
  const offset = r.offset ?? 0;
  return { offset, length: r.length ?? size - offset };
}

async function serveFile(c: C, ref: string, path: string): Promise<Response> {
  const r = await resolve(c, ref);
  if (r instanceof Response) return r;

  const entry = await fileEntry(c, r.doc, path);
  if (!entry) {
    return problem(c, 404, "unknown-file", "Not found", `${path} is not part of ${r.dataset} version ${r.doc.version}.`, {
      hint: `GET /v1/${r.dataset}/locations`,
    });
  }

  const etag = `"${entry.blob}"`;
  const headers: Record<string, string> = {
    "content-type": entry.type,
    etag,
    "cache-control": cacheControl(r.doc.cadence, r.pinned),
    "accept-ranges": "bytes",
    ...versionHeaders(c, r),
  };
  if (etagMatches(c.req.header("if-none-match"), etag)) return new Response(null, { status: 304, headers });

  const missing = () =>
    problem(c, 503, "data-unavailable", "Data temporarily unavailable", `The stored file for ${path} is missing.`);
  const wantsRange = c.req.header("range") !== undefined;
  const isHead = c.req.method === "HEAD";

  // Whole-body reads go through the edge cache; Range reads always go to R2.
  const cacheKey = wantsRange ? null : blobCacheKey(c.req.url, c.get("site").id, entry.blob);
  if (cacheKey) {
    const hit = await matchBlob(cacheKey);
    if (hit) {
      headers["content-length"] = String(hit.size);
      if (isHead) await hit.body?.cancel();
      return new Response(isHead ? null : hit.body, { headers });
    }
    if (isHead) {
      const meta = await c.env.DATA.head(keys.blob(entry.blob));
      if (!meta) return missing();
      headers["content-length"] = String(meta.size);
      return new Response(null, { headers });
    }
  }

  const obj = await c.env.DATA.get(keys.blob(entry.blob), wantsRange ? { range: c.req.raw.headers } : undefined);
  if (!obj) return missing();

  if (wantsRange && obj.range) {
    const { offset, length } = rangeOf(obj.range, obj.size);
    headers["content-range"] = `bytes ${offset}-${offset + length - 1}/${obj.size}`;
    headers["content-length"] = String(length);
    return new Response(obj.body, { status: 206, headers });
  }
  headers["content-length"] = String(obj.size);
  if (cacheKey && obj.body && cacheable(obj.size)) {
    const [client, copy] = obj.body.tee();
    defer(c, putBlob(cacheKey, copy, obj.size, entry.type));
    return new Response(client, { headers });
  }
  return new Response(obj.body, { headers });
}

export function index(c: C): Response {
  const site = c.get("site");
  return json(
    {
      site: site.id,
      name: site.name,
      tagline: site.tagline,
      api_version: "v1",
      links: { catalog: siteLink(c, "/v1/catalog"), status: siteLink(c, "/v1/status"), openapi: siteLink(c, "/v1/openapi.json") },
    },
    shortCache(300),
  );
}

export async function catalog(c: C): Promise<Response> {
  const site = c.get("site");
  const doc = await readJson<Catalog>(c.env.DATA, keys.catalog(site.id), TTL.catalog);
  return json(doc ?? { site: site.id, datasets: [] }, shortCache(300));
}

export async function datasetMeta(c: C, ref: string): Promise<Response> {
  const r = await resolve(c, ref);
  if (r instanceof Response) return r;
  const { doc, manifest, dataset } = r;
  const downloads = Object.entries(doc.files)
    .filter(([path]) => path.startsWith("download/"))
    .map(([path, entry]) => ({ name: path.slice("download/".length), bytes: entry.bytes, url: siteLink(c, `/v1/${dataset}/${path}`) }));
  return json(
    {
      dataset,
      kind: doc.kind ?? "series",
      title: doc.title,
      description: doc.description,
      version: doc.version,
      current_version: manifest.current,
      pinned: r.pinned,
      published_at: doc.version === manifest.current ? manifest.published_at : null,
      cadence: doc.cadence,
      source: doc.source,
      coverage: doc.coverage,
      validation: { status: doc.validation.status, warnings: doc.validation.warnings },
      series: Object.entries(doc.series).map(([id, name]) => ({ id, name })),
      ...(doc.kind === "table" ? { key: doc.key ?? [], partition: doc.partition ?? null } : {}),
      downloads,
      links: {
        locations: siteLink(c, `/v1/${dataset}/locations`),
        versions: siteLink(c, `/v1/${dataset}/versions`),
        this_version: siteLink(c, `/v1/${dataset}@${doc.version}`),
      },
    },
    r.pinned ? cacheControl(doc.cadence, true) : shortCache(300),
    versionHeaders(c, r),
  );
}

export async function versions(c: C, ref: string): Promise<Response> {
  const r = await resolve(c, ref);
  if (r instanceof Response) return r;
  const { manifest } = r;
  return json(
    {
      dataset: r.dataset,
      current: manifest.current,
      published_at: manifest.published_at,
      previous: manifest.previous,
      snapshots: manifest.snapshots,
    },
    shortCache(300),
  );
}

export async function locations(c: C, ref: string): Promise<Response> {
  const r = await resolve(c, ref);
  if (r instanceof Response) return r;
  const bySeries = new Map<string, string[]>();
  for (const path of Object.keys(await allFiles(c, r.doc))) {
    const m = SERIES_FILE.exec(path);
    if (!m || m[1] === "download") continue;
    const [, series, location] = m as unknown as [string, string, string];
    bySeries.set(series, [...(bySeries.get(series) ?? []), location]);
  }
  const names = await locationNames(c, r.doc);
  return json(
    {
      dataset: r.dataset,
      version: r.doc.version,
      kind: r.doc.kind ?? "series",
      // Table datasets list their partitions under the pseudo-series "table".
      series: [...bySeries.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([id, locs]) => ({ id, name: r.doc.series[id] ?? id, locations: locs.sort() })),
      locations: Object.entries(names).map(([id, name]) => ({ id, name })),
    },
    cacheControl(r.doc.cadence, r.pinned),
    versionHeaders(c, r),
  );
}

/** /v1/{dataset}/{series}/{location}.{json|csv}, /v1/{dataset}/table/{partition}.{json|csv}, or a files-dataset path. */
export function seriesFile(c: C, ref: string, series: string, file: string): Promise<Response> | Response {
  if (!/\.[a-z0-9]+$/i.test(file)) {
    return problem(c, 404, "unknown-format", "Unknown format", `Add a format extension, for example ${series}/${file}.json or .csv.`);
  }
  return serveFile(c, ref, `${series}/${file}`);
}

/** /v1/{dataset}/{path}: a files-dataset file at the top (the boundaries pack's index.json) or nested deeper. */
export function topFile(c: C, ref: string, file: string): Promise<Response> {
  return serveFile(c, ref, file);
}

export function download(c: C, ref: string, file: string): Promise<Response> {
  return serveFile(c, ref, `download/${file}`);
}
