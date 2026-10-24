// Edge cache for blob bodies through the Cache API. Blobs are content-addressed (the key is their sha256),
// so a cached body never goes stale. Only the body and its type are cached; every response header that
// depends on the request (version, pinning, plan, CORS) is rebuilt per request by the caller.
//
// The cache key is synthetic, on the request's own host and including the site id, so entries can never
// cross sites, and it never contains a plan, a key or a query string. Workers on workers.dev and in unit
// tests have no usable `caches`; every call degrades to a no-op there.

/** Bodies above this go straight from R2: they are bulk downloads, mostly read with Range anyway. */
export const MAX_CACHED_BYTES = 100 * 1024 * 1024;

function edgeCache(): Cache | null {
  try {
    return typeof caches !== "undefined" && caches?.default ? caches.default : null;
  } catch {
    return null;
  }
}

export function blobCacheKey(requestUrl: string, site: string, blob: string): Request {
  const { protocol, host } = new URL(requestUrl);
  return new Request(`${protocol}//${host}/__edge-cache/blob/${site}/${blob}`, { method: "GET" });
}

export interface CachedBlob {
  body: ReadableStream | null;
  size: number;
}

export async function matchBlob(key: Request): Promise<CachedBlob | null> {
  const cache = edgeCache();
  if (!cache) return null;
  try {
    const hit = await cache.match(key);
    if (!hit) return null;
    const size = Number(hit.headers.get("content-length") ?? NaN);
    if (!Number.isFinite(size)) {
      await hit.body?.cancel();
      return null;
    }
    return { body: hit.body, size };
  } catch {
    return null;
  }
}

/** Stores a body; call inside waitUntil. Failures are swallowed: the cache is an optimization. */
export async function putBlob(key: Request, body: ReadableStream, size: number, type: string): Promise<void> {
  const cache = edgeCache();
  if (!cache) {
    await body.cancel();
    return;
  }
  try {
    // A fixed-length stream lets the cache store the entry with a known Content-Length.
    const sized = typeof FixedLengthStream === "function" ? body.pipeThrough(new FixedLengthStream(size)) : body;
    await cache.put(
      key,
      new Response(sized, {
        headers: {
          "content-type": type,
          "content-length": String(size),
          "cache-control": "public, max-age=31536000, immutable",
        },
      }),
    );
  } catch {
    /* cache full or unavailable */
  }
}

export function cacheable(size: number): boolean {
  return edgeCache() !== null && size <= MAX_CACHED_BYTES;
}
