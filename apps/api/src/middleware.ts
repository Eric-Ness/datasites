import type { MiddlewareHandler } from "hono";
import { siteByHost, siteById, type Site } from "@datasites/registry";
import { CORS_HEADERS, problem } from "./http";

export type Plan = "anonymous";

export type AppEnv = {
  Bindings: Env;
  Variables: { site: Site; plan: Plan };
};

/** CORS on every response; preflight answered directly. */
export const cors: MiddlewareHandler<AppEnv> = async (c, next) => {
  if (c.req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
  await next();
  for (const [name, value] of Object.entries(CORS_HEADERS)) c.res.headers.set(name, value);
};

function isDevHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname.endsWith(".workers.dev");
}

/**
 * api.climatedata.dev -> climatedata. On localhost, workers.dev previews and in local development
 * (`wrangler dev` rewrites the hostname to the first route, so ENV flags it), ?site= picks the site.
 */
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

/** Everyone is anonymous until API keys exist; the hook is here so adding keys is not a rewrite. */
export const authenticate: MiddlewareHandler<AppEnv> = async (c, next) => {
  c.set("plan", "anonymous");
  await next();
};

export const rateLimit: MiddlewareHandler<AppEnv> = async (c, next) => {
  const limiter = c.env.RL_ANON as RateLimit | undefined;
  if (limiter) {
    const key = c.req.header("cf-connecting-ip") ?? "unknown";
    const { success } = await limiter.limit({ key });
    if (!success) {
      const res = problem(c, 429, "rate-limited", "Too many requests", "Slow down, or use the bulk download files.");
      res.headers.set("retry-after", "60");
      return res;
    }
  }
  await next();
};
