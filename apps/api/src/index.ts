import { Hono } from "hono";
import { BILLING_CRON, reportUsage, webhook } from "./billing";
import { catalog, datasetMeta, download, index, locations, seriesFile, versions } from "./data";
import { problem, shortCache, json } from "./http";
import { authenticate, cors, meter, rateLimit, resolveSite, type AppEnv } from "./middleware";
import { openapi } from "./openapi";
import { runFreshnessCheck, siteStatus } from "./status";

// The main module may export only handlers and Durable Object classes (workerd rejects anything else).
export { UsageCounter } from "./usage";

export const app = new Hono<AppEnv>();

// Stripe calls this directly: signature-verified, so it skips authentication, rate limits and metering.
app.post("/billing/webhook", (c) => webhook(c));

// meter wraps authenticate and rateLimit so 401s and 429s reach the analytics too (they are never billed).
app.use("*", cors, resolveSite, meter, authenticate, rateLimit);

app.get("/", (c) => index(c));
app.get("/v1", (c) => index(c));
app.get("/v1/catalog", (c) => catalog(c));
app.get("/v1/status", async (c) => json(await siteStatus(c.env.DATA, c.get("site").id, Date.now()), shortCache(60)));
app.get("/v1/openapi.json", (c) => openapi(c));
app.get("/v1/:ref", (c) => datasetMeta(c, c.req.param("ref")));
app.get("/v1/:ref/versions", (c) => versions(c, c.req.param("ref")));
app.get("/v1/:ref/locations", (c) => locations(c, c.req.param("ref")));
app.get("/v1/:ref/download/:file", (c) => download(c, c.req.param("ref"), c.req.param("file")));
app.get("/v1/:ref/:series/:file", (c) => seriesFile(c, c.req.param("ref"), c.req.param("series"), c.req.param("file")));

app.notFound((c) => problem(c, 404, "not-found", "Not found", "No such endpoint.", { hint: "GET /v1" }));
app.onError((err, c) => {
  console.error(err);
  return problem(c, 500, "internal", "Internal error", "Something went wrong on our side.");
});

export default {
  fetch: app.fetch,
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext) {
    if (controller.cron === BILLING_CRON) {
      ctx.waitUntil(
        reportUsage(env).then((summary) => {
          if (summary) console.log(`usage report (before ${summary.today}): ${summary.reported} sent, ${summary.skipped} skipped, ${summary.failed} failed`);
        }),
      );
      return;
    }
    ctx.waitUntil(
      runFreshnessCheck(env).then((problems) => {
        if (problems.length) console.warn(`freshness problems:\n${problems.join("\n")}`);
      }),
    );
  },
} satisfies ExportedHandler<Env>;
