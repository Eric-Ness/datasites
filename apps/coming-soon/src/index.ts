import { siteByHost, siteById, type Site } from "@datasites/registry";
import { comingSoonPage, favicon, privacyPage } from "./page";

const SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isDevHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname.endsWith(".workers.dev");
}

function html(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": status === 200 ? "public, max-age=300" : "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "strict-origin-when-cross-origin",
    },
  });
}

async function verifyTurnstile(env: Env, token: string, ip: string | null): Promise<boolean> {
  const secret = env.TURNSTILE_SECRET_KEY;
  if (!secret) {
    // Without a secret only local development may skip the check.
    if (env.ENV === "production") throw new Error("TURNSTILE_SECRET_KEY is not set");
    return true;
  }
  const form = new FormData();
  form.append("secret", secret);
  form.append("response", token);
  if (ip) form.append("remoteip", ip);
  const res = await fetch(SITEVERIFY, { method: "POST", body: form });
  if (!res.ok) return false;
  const outcome = (await res.json()) as { success?: boolean };
  return outcome.success === true;
}

async function signup(request: Request, env: Env, site: Site, query: string): Promise<Response> {
  const form = await request.formData();
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  const page = (kind: "ok" | "error", text: string, status: number) =>
    html(
      comingSoonPage(site, { turnstileSiteKey: env.TURNSTILE_SITE_KEY, query, notice: { kind, text }, email }),
      status,
    );

  // Bots fill the hidden "website" field; pretend it worked.
  if (String(form.get("website") ?? "") !== "") return page("ok", "Thanks. You're on the list.", 200);

  if (email.length > 254 || !EMAIL.test(email)) return page("error", "That doesn't look like an email address.", 400);

  const token = String(form.get("cf-turnstile-response") ?? "");
  if (!(await verifyTurnstile(env, token, request.headers.get("cf-connecting-ip")))) {
    return page("error", "We couldn't confirm you're not a bot. Please try again.", 400);
  }

  await env.DB.prepare(
    "INSERT INTO signups (email, site, created_at) VALUES (?, ?, ?) ON CONFLICT (email, site) DO NOTHING",
  )
    .bind(email, site.id, new Date().toISOString())
    .run();

  return page("ok", `Thanks. We'll email ${email} once when ${site.name} launches.`, 200);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // `wrangler dev` rewrites the hostname to the first route, so local development is flagged by ENV instead.
    const dev = isDevHost(url.hostname) || env.ENV === "development";
    const requested = dev ? url.searchParams.get("site") : null;
    const site = (requested ? siteById(requested) : undefined) ?? siteByHost(url.hostname);
    if (!site) {
      const hint = dev ? " Add ?site=<id>, for example ?site=climatedata." : "";
      return new Response(`No site is served at ${url.hostname}.${hint}\n`, { status: 404 });
    }
    const query = dev ? `?site=${site.id}` : "";

    if (url.pathname === "/" && (request.method === "GET" || request.method === "HEAD")) {
      return html(comingSoonPage(site, { turnstileSiteKey: env.TURNSTILE_SITE_KEY, query }));
    }
    if (url.pathname === "/signup" && request.method === "POST") return signup(request, env, site, query);
    if (url.pathname === "/privacy") return html(privacyPage(site, query));
    if (url.pathname === "/favicon.svg") {
      return new Response(favicon(site.id), {
        headers: { "content-type": "image/svg+xml", "cache-control": "public, max-age=86400" },
      });
    }
    if (url.pathname === "/robots.txt") {
      return new Response("User-agent: *\nAllow: /\n", { headers: { "content-type": "text/plain" } });
    }
    return Response.redirect(`${url.origin}/${query}`, 302);
  },
} satisfies ExportedHandler<Env>;
