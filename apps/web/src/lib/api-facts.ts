// What the API promises, for the docs pages. Each constant names the API source it mirrors;
// change them together.

import { parseDuration } from "./format";

/** apps/api/wrangler.jsonc, ratelimits RL_ANON (production). */
export const ANONYMOUS_RATE_LIMIT = { requests: 120, periodSeconds: 60 };

/** apps/api/src/middleware.ts: the Retry-After a 429 carries. */
export const RETRY_AFTER_SECONDS = 60;

/** pipelines/core outputs.py TABLE_ALL_MAX_ROWS: tables above this have no table/all.* file. */
export const TABLE_ALL_MAX_ROWS = 50_000;

/** apps/api/src/http.ts CORS_HEADERS. */
export const CORS = {
  methods: "GET, HEAD, OPTIONS",
  requestHeaders: ["Authorization", "X-API-Key", "If-None-Match", "Range"],
  exposedHeaders: ["ETag", "Last-Modified", "X-Data-Version", "Link", "Content-Range", "Accept-Ranges", "Retry-After"],
};

const HOUR = 3_600_000;

function approxMs(iso: string): number | null {
  const d = parseDuration(iso);
  if (!d) return null;
  return (
    d.years * 365 * 24 * HOUR +
    d.months * 30 * 24 * HOUR +
    d.weeks * 7 * 24 * HOUR +
    d.days * 24 * HOUR +
    d.hours * HOUR +
    d.minutes * 60_000 +
    d.seconds * 1000
  );
}

/** Browser cache lifetime (seconds) of "latest" data URLs, as apps/api/src/http.ts cacheControl sets it. */
export function latestMaxAge(cadence: string): number {
  const ms = approxMs(cadence);
  if (ms === null) return 300;
  if (ms < 2 * HOUR) return 300;
  if (ms < 48 * HOUR) return 1800;
  return 3600;
}

export const PINNED_CACHE = "public, max-age=31536000, immutable";
export const STALE_DIRECTIVES = "stale-while-revalidate=86400, stale-if-error=604800";

export interface ProblemType {
  slug: string;
  status: number;
  title: string;
  when: string;
  fix: string;
}

/** Every problem type the API returns (apps/api/src: data.ts, index.ts, middleware.ts). */
export const PROBLEM_TYPES: ProblemType[] = [
  {
    slug: "not-found",
    status: 404,
    title: "Not found",
    when: "The path is not an API endpoint.",
    fix: "Start from GET /v1, which links to the catalog and status.",
  },
  {
    slug: "unknown-dataset",
    status: 404,
    title: "Unknown dataset",
    when: "No dataset with that id has been published on this site.",
    fix: "GET /v1/catalog lists the dataset ids. A dataset in the catalog on this website may not be published yet.",
  },
  {
    slug: "unknown-version",
    status: 404,
    title: "Unknown version",
    when: "The version after @ is malformed, was never published, or was quarantined by validation.",
    fix: "GET /v1/{dataset}/versions lists the current, previous and snapshot versions.",
  },
  {
    slug: "unknown-file",
    status: 404,
    title: "Not found",
    when: "The series, location, partition or file is not part of that version.",
    fix: "GET /v1/{dataset}/locations lists every series with its locations (or, for tables, every partition).",
  },
  {
    slug: "unknown-format",
    status: 404,
    title: "Unknown format",
    when: "A data path has no format extension.",
    fix: "End the path with .json or .csv.",
  },
  {
    slug: "unknown-site",
    status: 404,
    title: "Unknown site",
    when: "The request reached the API on a hostname that is not one of the sites.",
    fix: "Use https://api.<domain>. On a local or preview API, add ?site=<id>.",
  },
  {
    slug: "rate-limited",
    status: 429,
    title: "Too many requests",
    when: `More than ${ANONYMOUS_RATE_LIMIT.requests} requests in ${ANONYMOUS_RATE_LIMIT.periodSeconds} seconds from one IP address.`,
    fix: `Wait for the Retry-After header (${RETRY_AFTER_SECONDS} seconds), cache with ETags, or fetch the bulk download once instead of many small files.`,
  },
  {
    slug: "data-unavailable",
    status: 503,
    title: "Data temporarily unavailable",
    when: "A stored file or version document could not be read.",
    fix: "Retry after a short wait. If it persists, the status page shows whether the dataset is affected.",
  },
  {
    slug: "internal",
    status: 500,
    title: "Internal error",
    when: "Something failed on our side.",
    fix: "Retry later. Include the request_id from the response if you report it.",
  },
];
