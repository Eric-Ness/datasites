// Browser-side API access: the base URL from the page, JSON fetching with a timeout, and
// readable messages for every way a request can fail (network, timeout, problem+json).

import { apiUrl } from "../lib/urls";

export function pageApiBase(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="datasites:api-base"]')?.content ?? "";
}

export function pageSiteId(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="datasites:site"]')?.content ?? "";
}

/** The full URL of an API path for this page's site (or another site, for the gallery). */
export function api(path: string, siteId = pageSiteId(), base = pageApiBase()): string {
  return apiUrl(base, siteId, path);
}

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  hint?: string;
}

export type FailureKind = "network" | "timeout" | "http" | "parse";

export class ApiError extends Error {
  constructor(
    readonly kind: FailureKind,
    readonly url: string,
    readonly status: number | null = null,
    readonly problem: Problem | null = null,
  ) {
    super(problem?.detail ?? problem?.title ?? `${kind} error for ${url}`);
  }

  /** The problem slug ("unknown-dataset") from the type URL, if any. */
  get slug(): string | null {
    const type = this.problem?.type;
    return type && type.includes("#") ? type.slice(type.lastIndexOf("#") + 1) : null;
  }
}

const cache = new Map<string, Promise<unknown>>();

/** GETs JSON. Responses are shared for the page's lifetime; failed requests are forgotten so a retry refetches. */
export function getJson<T>(url: string, { timeoutMs = 15_000 }: { timeoutMs?: number } = {}): Promise<T> {
  const hit = cache.get(url);
  if (hit) return hit as Promise<T>;
  const promise = (async () => {
    let res: Response;
    try {
      res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), headers: { accept: "application/json" } });
    } catch (err) {
      const name = (err as { name?: string })?.name;
      throw new ApiError(name === "TimeoutError" || name === "AbortError" ? "timeout" : "network", url);
    }
    const text = await res.text().catch(() => "");
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      if (res.ok) throw new ApiError("parse", url, res.status);
    }
    if (!res.ok) throw new ApiError("http", url, res.status, (body as Problem | null) ?? null);
    return body as T;
  })();
  cache.set(url, promise);
  promise.catch(() => cache.delete(url));
  return promise;
}

/** A sentence describing a failure for people, naming the API host. */
export function describeFailure(err: unknown, host?: string): string {
  const where = host ? ` (${host})` : "";
  if (!(err instanceof ApiError)) return `Something went wrong while loading data${where}.`;
  if (err.kind === "network") return `Couldn't reach the API${where}. It may be down, or your connection may be offline.`;
  if (err.kind === "timeout") return `The API${where} took too long to answer.`;
  if (err.kind === "parse") return `The API${where} sent a response this page couldn't read.`;
  if (err.status === 429) return "Too many requests from your address. Wait a minute and try again.";
  if (err.slug === "unknown-dataset") return "This dataset hasn't been published yet. Its first version will appear here once the pipeline runs.";
  const p = err.problem;
  if (p?.title || p?.detail) return [p.title, p.detail].filter(Boolean).join(": ");
  return `The API${where} answered with HTTP ${err.status}.`;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
