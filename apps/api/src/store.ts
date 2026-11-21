import { edgeCache } from "./cache";

// JSON reads from R2 behind two caches: a small per-isolate memo, and (for requests) the colo's edge cache,
// which every isolate in the colo shares. The API sees little traffic, so most requests land on an isolate
// whose memo is empty; without the edge tier each one made two to four sequential R2 reads before any data.

const MAX_ENTRIES = 500;
const MISSING_TTL_MS = 30_000;

export const TTL = {
  manifest: 30_000,
  catalog: 60_000,
  state: 15_000,
  immutable: Number.POSITIVE_INFINITY,
} as const;

interface Memo {
  value: unknown;
  expires: number;
}

const memo = new Map<string, Memo>();

/**
 * Waits before each retry of a failed R2 read. R2 sometimes answers "We encountered an internal error. Please try
 * again. (10001)" for a moment: on 2026-11-20 at 13:00 UTC six sites' reads failed at once and the freshness check
 * paged, then the next run 15 minutes later was fine. A missing object (null) is an answer, not an error, and isn't
 * retried. If every try fails, the last error is thrown as before.
 */
export const R2_RETRY_DELAYS_MS = [250, 1_000];

async function getJsonWithRetry<T>(bucket: R2Bucket, key: string): Promise<T | null> {
  for (let attempt = 0; ; attempt++) {
    try {
      const obj = await bucket.get(key);
      return obj ? ((await obj.json()) as T) : null;
    } catch (err) {
      const delay = R2_RETRY_DELAYS_MS[attempt];
      if (delay === undefined) throw err;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
}

/**
 * The edge tier for one request: the origin its cache keys live on (the request's own host, so entries never
 * cross sites) and the request's waitUntil, so a store outlives the response. The freshness cron passes none.
 */
export interface EdgeTier {
  origin: string;
  defer: (work: Promise<unknown>) => void;
}

/** Edge entries carry their memo expiry, so a value read back from the edge is never kept past its first ttl. */
const EXPIRES_HEADER = "x-datasites-expires";

export function edgeKey(origin: string, key: string): Request {
  return new Request(`${origin}/__edge-cache/json/${key}`, { method: "GET" });
}

async function fromEdge(cache: Cache, req: Request, now: number): Promise<Memo | null> {
  try {
    const hit = await cache.match(req);
    if (!hit) return null;
    const header = hit.headers.get(EXPIRES_HEADER);
    const expires = header === null ? Number.POSITIVE_INFINITY : Number(header);
    if (!(expires > now)) {
      await hit.body?.cancel();
      return null;
    }
    return { value: await hit.json(), expires };
  } catch {
    return null;
  }
}

async function toEdge(cache: Cache, req: Request, entry: Memo, now: number): Promise<void> {
  try {
    const finite = Number.isFinite(entry.expires);
    const maxAge = finite ? Math.max(1, Math.ceil((entry.expires - now) / 1000)) : 31_536_000;
    const headers: Record<string, string> = {
      "content-type": "application/json",
      "cache-control": `public, max-age=${maxAge}${finite ? "" : ", immutable"}`,
    };
    if (finite) headers[EXPIRES_HEADER] = String(entry.expires);
    await cache.put(req, new Response(JSON.stringify(entry.value), { headers }));
  } catch {
    /* the edge cache is an optimization */
  }
}

function remember(key: string, entry: Memo): void {
  if (memo.size >= MAX_ENTRIES) {
    const oldest = memo.keys().next().value;
    if (oldest !== undefined) memo.delete(oldest);
  }
  memo.set(key, entry);
}

export async function readJson<T>(bucket: R2Bucket, key: string, ttlMs: number, edge?: EdgeTier): Promise<T | null> {
  const now = Date.now();
  const hit = memo.get(key);
  if (hit && hit.expires > now) return hit.value as T | null;

  const cache = edge ? edgeCache() : null;
  const req = cache && edge ? edgeKey(edge.origin, key) : null;
  if (cache && req) {
    const shared = await fromEdge(cache, req, now);
    if (shared) {
      remember(key, shared);
      return shared.value as T | null;
    }
  }

  const value = await getJsonWithRetry<T>(bucket, key);
  // Never cache a missing object forever: it may be published a minute from now.
  const entry = { value, expires: now + (value === null ? Math.min(ttlMs, MISSING_TTL_MS) : ttlMs) };
  remember(key, entry);
  if (cache && req && edge) edge.defer(toEdge(cache, req, entry, now));
  return value;
}

/** Test hook: forget everything memoized in this isolate. */
export function clearMemo(): void {
  memo.clear();
}
