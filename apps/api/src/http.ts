import type { Context } from "hono";
import { durationMs } from "./duration";
import type { AppEnv } from "./middleware";

export const CORS_HEADERS: Record<string, string> = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, HEAD, OPTIONS",
  "access-control-allow-headers": "Authorization, X-API-Key, If-None-Match, Range",
  "access-control-expose-headers":
    "ETag, Last-Modified, X-Data-Version, X-Plan, Link, Content-Range, Accept-Ranges, Retry-After, WWW-Authenticate",
  "access-control-max-age": "86400",
};

const HOUR = 3_600_000;
const STALE = "stale-while-revalidate=86400, stale-if-error=604800";

/** Browser cache lifetime for "latest" URLs, by how often the dataset changes. Capped at an hour so a rollback reaches clients quickly. */
export function cacheControl(cadence: string, pinned: boolean): string {
  if (pinned) return "public, max-age=31536000, immutable";
  let maxAge = 3600;
  try {
    const ms = durationMs(cadence);
    if (ms < 2 * HOUR) maxAge = 300;
    else if (ms < 48 * HOUR) maxAge = 1800;
  } catch {
    maxAge = 300;
  }
  return `public, max-age=${maxAge}, ${STALE}`;
}

export function shortCache(seconds: number): string {
  return `public, max-age=${seconds}`;
}

/** RFC 9457 problem details. */
export function problem(
  c: Context<AppEnv>,
  status: number,
  slug: string,
  title: string,
  detail: string,
  extra: Record<string, unknown> = {},
): Response {
  const site = c.get("site");
  const body = {
    type: `https://${site?.domain ?? "datasites"}/docs/errors#${slug}`,
    title,
    status,
    detail,
    instance: new URL(c.req.url).pathname,
    request_id: c.req.header("cf-ray") ?? null,
    ...extra,
  };
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/problem+json", "cache-control": "no-store" },
  });
}

export function json(body: unknown, cache: string, extraHeaders: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": cache, ...extraHeaders },
  });
}

/** Run work after the response is sent when an execution context exists (it doesn't in unit tests). */
export function defer(c: Context<AppEnv>, work: Promise<unknown>): void {
  try {
    c.executionCtx.waitUntil(work);
  } catch {
    work.catch(() => {});
  }
}
