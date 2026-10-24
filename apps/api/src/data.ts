import type { Context } from "hono";
import { cacheControl, json, problem, shortCache } from "./http";
import type { AppEnv } from "./middleware";
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

  const entry: FileEntry | undefined = r.doc.files[path];
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

  const wantsRange = c.req.header("range") !== undefined;
  const obj = await c.env.DATA.get(keys.blob(entry.blob), wantsRange ? { range: c.req.raw.headers } : undefined);
  if (!obj) {
    return problem(c, 503, "data-unavailable", "Data temporarily unavailable", `The stored file for ${path} is missing.`);
  }

  if (wantsRange && obj.range) {
    const { offset, length } = rangeOf(obj.range, obj.size);
    headers["content-range"] = `bytes ${offset}-${offset + length - 1}/${obj.size}`;
    headers["content-length"] = String(length);
    return new Response(obj.body, { status: 206, headers });
  }
  headers["content-length"] = String(obj.size);
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
      links: { catalog: "/v1/catalog", status: "/v1/status" },
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
    .map(([path, entry]) => ({ name: path.slice("download/".length), bytes: entry.bytes, url: `/v1/${dataset}/${path}` }));
  return json(
    {
      dataset,
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
      downloads,
      links: {
        locations: `/v1/${dataset}/locations`,
        versions: `/v1/${dataset}/versions`,
        this_version: `/v1/${dataset}@${doc.version}`,
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
  for (const path of Object.keys(r.doc.files)) {
    const m = SERIES_FILE.exec(path);
    if (!m || m[1] === "download") continue;
    const [, series, location] = m as unknown as [string, string, string];
    bySeries.set(series, [...(bySeries.get(series) ?? []), location]);
  }
  return json(
    {
      dataset: r.dataset,
      version: r.doc.version,
      series: [...bySeries.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([id, locs]) => ({ id, name: r.doc.series[id] ?? id, locations: locs.sort() })),
      locations: Object.entries(r.doc.locations).map(([id, name]) => ({ id, name })),
    },
    cacheControl(r.doc.cadence, r.pinned),
    versionHeaders(c, r),
  );
}

export function seriesFile(c: C, ref: string, series: string, file: string): Promise<Response> | Response {
  if (!/\.(json|csv)$/.test(file)) {
    return problem(c, 404, "unknown-format", "Unknown format", `Use .json or .csv, for example ${series}/<location>.json.`);
  }
  return serveFile(c, ref, `${series}/${file}`);
}

export function download(c: C, ref: string, file: string): Promise<Response> {
  return serveFile(c, ref, `download/${file}`);
}
