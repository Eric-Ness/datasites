import type { Context, MiddlewareHandler } from "hono";
import { siteByHost, siteById, type Site } from "@datasites/registry";
import { CORS_HEADERS, defer, problem } from "./http";
import { hashKey, isCached, KEY_PATTERN, lookupKey, presentedKey, type Plan } from "./keys";
import { countRequest, utcDay, writeDataPoint } from "./usage";

export type { Plan } from "./keys";

export type AppEnv = {
  Bindings: Env;
  Variables: { site: Site; plan: Plan; account: string | null };
};

/** CORS on every response; preflight answered directly. X-Plan on every response too. */
export const cors: MiddlewareHandler<AppEnv> = async (c, next) => {
  if (c.req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
  await next();
  for (const [name, value] of Object.entries(CORS_HEADERS)) c.res.headers.set(name, value);
  c.res.headers.set("x-plan", c.get("plan") ?? "anonymous");
};

export function isDevHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname.endsWith(".workers.dev");
}

/**
 * api.climatedata.dev -> climatedata. On localhost, workers.dev previews and in local development
 * (`wrangler dev` rewrites the hostname to the first route, so ENV flags it), ?site= picks the site.
 */
/**
 * A link to another API path for this request's site. Where the host doesn't name the site (workers.dev
 * staging, localhost), the link carries ?site=<id>, so following it lands on the same site.
 */
export function siteLink(c: Context<AppEnv>, path: string): string {
  if (!isDevHost(new URL(c.req.url).hostname) && c.env.ENV !== "development") return path;
  return `${path}${path.includes("?") ? "&" : "?"}site=${encodeURIComponent(c.get("site").id)}`;
}

export const resolveSite: MiddlewareHandler<AppEnv> = async (c, next) => {
  const hostname = new URL(c.req.url).hostname;
  const dev = isDevHost(hostname) || c.env.ENV === "development";
  const requested = dev ? c.req.query("site") : undefined;
  const site = (requested ? siteById(requested) : undefined) ?? siteByHost(hostname);
  if (!site) {
    const hint = dev ? " Add ?site=<id>, for example ?site=climatedata." : "";
    return problem(c, 404, "unknown-site", "Unknown site", `No site is served at ${hostname}.${hint}`);
  }
  c.set("site", site);
  await next();
};

function clientIp(c: Context<AppEnv>): string {
  return c.req.header("cf-connecting-ip") ?? "unknown";
}

function invalidKey(c: Context<AppEnv>, detail: string): Response {
  const res = problem(c, 401, "invalid-key", "Invalid API key", detail, {
    hint: "Send a key as Authorization: Bearer <key> or X-API-Key: <key>, or send no key for anonymous access.",
  });
  res.headers.set("www-authenticate", 'Bearer realm="api", error="invalid_token"');
  return res;
}

function tooManyRequests(c: Context<AppEnv>, detail: string): Response {
  const res = problem(c, 429, "rate-limited", "Too many requests", detail);
  res.headers.set("retry-after", "60"); // every limiter binding uses a 60-second period
  return res;
}

/**
 * No credentials: anonymous. A key in `Authorization: Bearer` or `X-API-Key`:
 *  - malformed, unknown or revoked -> 401 invalid-key (the credentials are wrong)
 *  - valid, but its account is suspended -> 403 account-suspended (the credentials are fine, access is not)
 *  - valid -> the account's plan
 * Lookups are cached in isolate memory for 5 minutes. A lookup that misses the cache is first charged to the
 * client IP's anonymous limiter, so a flood of random well-formed keys can't turn into a flood of KV reads.
 * Without a KEYS binding (unit tests, or keys not yet switched on) every request is anonymous.
 */
export const authenticate: MiddlewareHandler<AppEnv> = async (c, next) => {
  c.set("plan", "anonymous");
  c.set("account", null);
  const key = presentedKey(c.req.raw.headers);
  const kv = c.env.KEYS as KVNamespace | undefined;
  if (key === undefined || !kv) return next();

  if (key === "") return invalidKey(c, "The Authorization header must be Bearer <key>, and must match X-API-Key if both are sent.");
  if (!KEY_PATTERN.test(key)) return invalidKey(c, "That is not a valid API key.");

  const hash = await hashKey(key);
  if (!isCached(hash)) {
    const limiter = c.env.RL_ANON as RateLimit | undefined;
    if (limiter && !(await limiter.limit({ key: `auth:${clientIp(c)}` })).success) {
      return tooManyRequests(c, "Too many key lookups from this address.");
    }
  }

  let principal;
  try {
    principal = await lookupKey(kv, hash);
  } catch (err) {
    console.error(`key lookup failed: ${err instanceof Error ? err.message : String(err)}`);
    const res = problem(c, 503, "auth-unavailable", "Authentication temporarily unavailable", "Try again shortly, or send no key for anonymous access.");
    res.headers.set("retry-after", "5");
    return res;
  }
  if (!principal) return invalidKey(c, "This API key is unknown or has been revoked.");
  if (principal.status === "suspended") {
    return problem(c, 403, "account-suspended", "Account suspended", "This API key is valid, but its account is suspended.");
  }
  c.set("plan", principal.plan);
  c.set("account", principal.account);
  await next();
};

function limiterFor(env: Env, plan: Plan): RateLimit | undefined {
  const bindings: Record<Plan, unknown> = { anonymous: env.RL_ANON, free: env.RL_FREE, pro: env.RL_PRO };
  return bindings[plan] as RateLimit | undefined;
}

/**
 * One rate-limit binding per plan: anonymous traffic keyed by IP, keyed traffic by account. The binding only
 * answers success/failure (no remaining count or reset time), so RateLimit/RateLimit-Policy headers can't be
 * sent honestly; a 429 carries Retry-After instead.
 */
export const rateLimit: MiddlewareHandler<AppEnv> = async (c, next) => {
  const plan = c.get("plan");
  const account = c.get("account");
  const limiter = limiterFor(c.env, plan);
  if (limiter) {
    const key = account ? `acct:${account}` : clientIp(c);
    const { success } = await limiter.limit({ key });
    if (!success) {
      return tooManyRequests(
        c,
        plan === "anonymous" ? "Slow down, use the bulk download files, or get a free API key." : `Your ${plan} plan's rate limit was reached.`,
      );
    }
  }
  await next();
};

/**
 * After the handler: an Analytics Engine data point for every request and, for keyed requests that were
 * served (status < 400, so 304s count and errors don't), one increment of the account's daily counter.
 * All of it runs in waitUntil, after the response is on its way.
 */
export const meter: MiddlewareHandler<AppEnv> = async (c, next) => {
  await next();
  const env = c.env;
  const point = {
    site: c.get("site").id,
    plan: c.get("plan") ?? "anonymous",
    account: c.get("account") ?? null,
    method: c.req.method,
    path: new URL(c.req.url).pathname,
    status: c.res.status,
    bytes: Number(c.res.headers.get("content-length") ?? 0) || 0,
    colo: String((c.req.raw as { cf?: { colo?: unknown } }).cf?.colo ?? ""),
  };
  defer(
    c,
    Promise.resolve().then(async () => {
      try {
        writeDataPoint(env, point);
        if (point.account && point.status < 400) await countRequest(env, point.account, utcDay(Date.now()));
      } catch (err) {
        console.error(`usage metering failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    }),
  );
};
