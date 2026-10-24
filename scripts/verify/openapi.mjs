// A3: the OpenAPI document of every site, as served by `wrangler dev` on port 8799.
//   node scripts/verify/openapi.mjs
// For each site in the registry: openapi 3.1.x, the site's server URL, the common paths, every registry
// dataset's paths for its kind (current and pinned), every $ref resolving inside the document, unique
// operationIds, and declared path parameters for every {template} in a path.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, fail, startServer, waitForHttp } from "./lib.mjs";

const registry = join(ROOT, "packages", "registry", "src");
const sites = JSON.parse(readFileSync(join(registry, "sites.json"), "utf8"));
const datasets = JSON.parse(readFileSync(join(registry, "datasets.json"), "utf8"));
const base = "http://127.0.0.1:8799";

const COMMON = ["/v1", "/v1/catalog", "/v1/status", "/v1/openapi.json"];
const METHODS = new Set(["get", "put", "post", "delete", "options", "head", "patch", "trace"]);

function expectedPaths(d) {
  const out = [];
  for (const prefix of [`/v1/${d.id}`, `/v1/${d.id}@{version}`]) {
    out.push(prefix, `${prefix}/locations`, `${prefix}/download/{file}`);
    if (d.kind === "series") out.push(`${prefix}/{series}/{location}.{format}`);
    else if (d.kind === "table") out.push(`${prefix}/table/{partition}.{format}`);
    else out.push(`${prefix}/{folder}/{file}`);
  }
  out.push(`/v1/${d.id}/versions`);
  return out;
}

function resolvePointer(doc, ref) {
  if (!ref.startsWith("#/")) return undefined; // only local references are allowed
  return ref
    .slice(2)
    .split("/")
    .map((p) => decodeURIComponent(p).replace(/~1/g, "/").replace(/~0/g, "~"))
    .reduce((at, part) => (at && typeof at === "object" ? at[part] : undefined), doc);
}

function collectRefs(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => collectRefs(n, out));
  else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      if (k === "$ref" && typeof v === "string") out.push(v);
      else collectRefs(v, out);
    }
  }
  return out;
}

function deref(doc, node) {
  let n = node;
  for (let i = 0; n && n.$ref && i < 10; i++) n = resolvePointer(doc, n.$ref);
  return n;
}

const server = startServer("pnpm", ["exec", "wrangler", "dev", "--port", "8799", "--ip", "127.0.0.1", "--var", "ENV:development"], {
  cwd: join(ROOT, "apps", "api"),
});
process.on("exit", () => server.stop());

try {
  await waitForHttp(`${base}/v1?site=climatedata`, { server });
  let totalPaths = 0;
  let totalRefs = 0;
  for (const site of sites) {
    const res = await fetch(`${base}/v1/openapi.json?site=${site.id}`);
    check(res.status === 200, `${site.id}: HTTP ${res.status}`);
    check(/max-age=3600/.test(res.headers.get("cache-control") ?? ""), `${site.id}: cache-control ${res.headers.get("cache-control")}`);
    const doc = await res.json();
    const where = (m) => `${site.id}: ${m}`;

    check(typeof doc.openapi === "string" && doc.openapi.startsWith("3.1"), where(`openapi is ${doc.openapi}`));
    check(doc.info?.title && doc.info?.version, where("info.title/version missing"));
    check(doc.servers?.[0]?.url === `https://api.${site.domain}`, where(`servers ${JSON.stringify(doc.servers)}`));
    check(doc.components?.schemas?.Problem, where("no Problem schema"));
    check(doc.components?.securitySchemes?.ApiKeyHeader && doc.components?.securitySchemes?.BearerAuth, where("no security schemes"));
    check(Array.isArray(doc.security) && doc.security.some((s) => Object.keys(s).length === 0), where("anonymous access is not allowed in security"));

    const paths = doc.paths ?? {};
    for (const p of COMMON) check(paths[p]?.get, where(`missing ${p}`));
    const siteDatasets = datasets.filter((d) => d.site === site.id);
    for (const d of siteDatasets) for (const p of expectedPaths(d)) check(paths[p]?.get, where(`${d.id} (${d.kind}) is missing ${p}`));

    const ids = [];
    for (const [p, item] of Object.entries(paths)) {
      const templated = [...p.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
      for (const [method, op] of Object.entries(item)) {
        if (!METHODS.has(method)) continue;
        check(typeof op.operationId === "string" && op.operationId, where(`${method} ${p} has no operationId`));
        ids.push(op.operationId);
        check(op.responses && Object.keys(op.responses).length, where(`${op.operationId} has no responses`));
        const declared = new Set(
          [...(item.parameters ?? []), ...(op.parameters ?? [])].map((x) => deref(doc, x)).filter((x) => x?.in === "path").map((x) => x.name),
        );
        for (const name of templated) check(declared.has(name), where(`${op.operationId}: path parameter {${name}} is not declared`));
      }
    }
    const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
    check(dupes.length === 0, where(`duplicate operationIds: ${[...new Set(dupes)].join(", ")}`));

    const refs = collectRefs(doc);
    const broken = [...new Set(refs.filter((r) => resolvePointer(doc, r) === undefined))];
    check(broken.length === 0, where(`unresolved $refs: ${broken.join(", ")}`));

    totalPaths += Object.keys(paths).length;
    totalRefs += refs.length;
  }
  server.stop();
  console.log(`OPENAPI OK (${sites.length} sites, ${datasets.length} registry datasets, ${totalPaths} paths, ${totalRefs} $refs resolved, operationIds unique)`);
} catch (err) {
  fail(`${err instanceof Error ? err.stack : err}\n${server.log().slice(-2000)}`);
} finally {
  server.stop();
}
