import { Hono } from "hono";
import { catalog, datasetMeta, download, index, locations, seriesFile, versions } from "./data";
import { problem, shortCache, json } from "./http";
import { authenticate, cors, rateLimit, resolveSite, type AppEnv } from "./middleware";
import { runFreshnessCheck, siteStatus } from "./status";

export const app = new Hono<AppEnv>();

app.use("*", cors, resolveSite, authenticate, rateLimit);

app.get("/", (c) => index(c));
app.get("/v1", (c) => index(c));
app.get("/v1/catalog", (c) => catalog(c));
app.get("/v1/status", async (c) => json(await siteStatus(c.env.DATA, c.get("site").id, Date.now()), shortCache(60)));
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
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(
      runFreshnessCheck(env).then((problems) => {
        if (problems.length) console.warn(`freshness problems:\n${problems.join("\n")}`);
      }),
    );
  },
} satisfies ExportedHandler<Env>;
