// G5/G6: end-to-end checks against the real Workers running under `wrangler dev`.
//   node scripts/verify/e2e-local.mjs api          live NOAA pipeline -> local R2 -> API Worker
//   node scripts/verify/e2e-local.mjs coming-soon  coming-soon pages and signup -> local D1
// Uses ports 8797/8798 so it doesn't collide with `pnpm dev:*`, and stops its servers on exit.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, datasetKeys, fail, run, seedLocalR2, startServer, waitForHttp } from "./lib.mjs";

const mode = process.argv[2];
const sites = JSON.parse(readFileSync(join(ROOT, "packages", "registry", "src", "sites.json"), "utf8"));
const servers = [];
const stopAll = () => servers.forEach((s) => s.stop());
process.on("exit", stopAll);

function devServer(app, port) {
  const server = startServer("pnpm", ["exec", "wrangler", "dev", "--port", String(port), "--ip", "127.0.0.1", "--var", "ENV:development"], {
    cwd: join(ROOT, "apps", app),
  });
  servers.push(server);
  return server;
}

async function api() {
  // 1. live pipeline into the local bucket (.data/r2)
  // --force: this check must exercise the live fetch even if the dataset was checked moments ago.
  const pipeline = run("uv", ["run", "datasites", "run", "climatedata/co2-monthly", "--target", "local", "--force"], { cwd: join(ROOT, "pipelines") });
  const runLines = pipeline.stdout.trim().split(/\r?\n/);
  const outcomes = runLines.filter((l) => l.startsWith("{")).map((l) => JSON.parse(l));
  check(outcomes.length > 0 && outcomes.every((o) => ["published", "unchanged"].includes(o.outcome)), `pipeline outcomes: ${pipeline.stdout}`);
  check(runLines.some((l) => l.startsWith("RUN OK ")), `pipeline did not report RUN OK:\n${pipeline.stdout}`);

  // 2. copy just this dataset into wrangler's local R2, 3. serve it
  seedLocalR2(datasetKeys("climatedata", "co2-monthly"));
  const base = "http://127.0.0.1:8797";
  const server = devServer("api", 8797);
  await waitForHttp(`${base}/v1?site=climatedata`, { server });
  const q = "site=climatedata";

  const status = await (await fetch(`${base}/v1/status?${q}`)).json();
  const co2 = status.datasets.find((d) => d.id === "co2-monthly");
  // Only co2-monthly's state is seeded, so the site's other datasets read "pending"; judge this dataset alone.
  check(co2?.status === "ok", `co2-monthly status: ${JSON.stringify(co2)}`);

  const catalog = await (await fetch(`${base}/v1/catalog?${q}`)).json();
  const entry = catalog.datasets.find((d) => d.id === "co2-monthly");
  check(entry?.current === co2.version && /^\d{4}-\d{2}$/.test(entry.coverage.end), "catalog does not describe the current version");

  const csv = await fetch(`${base}/v1/co2-monthly/average/noaa-mlo.csv?${q}`);
  const lines = (await csv.text()).trim().split("\n");
  check(csv.status === 200 && csv.headers.get("content-type").startsWith("text/csv"), `CSV: ${csv.status}`);
  check(lines[0] === "date,value,uncertainty,interpolated" && lines[1] === "1958-03,315.71,,", `CSV starts: ${lines.slice(0, 2)}`);
  check(lines.length > 800, `CSV has only ${lines.length} lines`);
  const etag = csv.headers.get("etag");
  check(/^"[0-9a-f]{64}"$/.test(etag ?? ""), `CSV etag: ${etag}`);

  const again = await fetch(`${base}/v1/co2-monthly/average/noaa-mlo.csv?${q}`, { headers: { "if-none-match": etag } });
  check(again.status === 304, `If-None-Match gave ${again.status}, expected 304`);

  const range = await fetch(`${base}/v1/co2-monthly/download/co2-monthly.parquet?${q}`, { headers: { range: "bytes=0-3" } });
  const magic = Buffer.from(await range.arrayBuffer()).toString("latin1");
  check(range.status === 206 && magic === "PAR1", `Parquet range gave ${range.status} "${magic}", expected 206 "PAR1"`);

  const missing = await fetch(`${base}/v1/co2-monthly/average/mars.json?${q}`);
  const problem = await missing.json();
  check(missing.status === 404 && missing.headers.get("content-type") === "application/problem+json" && problem.hint, "unknown location is not a problem document");

  const other = await (await fetch(`${base}/v1?site=waterdata`)).json();
  check(other.site === "waterdata", `?site=waterdata served ${other.site}`);

  console.log(`E2E API OK (co2-monthly ${co2.version}, ${lines.length - 1} Mauna Loa months, 304/206/404 verified)`);
}

async function comingSoon() {
  const app = join(ROOT, "apps", "coming-soon");
  run("pnpm", ["exec", "wrangler", "d1", "migrations", "apply", "datasites-signups", "--local"], { cwd: app });
  const base = "http://127.0.0.1:8798";
  const server = devServer("coming-soon", 8798);
  await waitForHttp(`${base}/?site=climatedata`, { server });

  for (const site of sites) {
    const html = await (await fetch(`${base}/?site=${site.id}`)).text();
    check(html.includes(`<h1>${site.name} is coming soon</h1>`), `${site.id}: heading missing`);
    check(html.includes(`action="/signup?site=${site.id}"`), `${site.id}: form does not post back to its own site`);
  }

  const email = `e2e-${Date.now()}@example.com`;
  const sql = (command) => {
    const out = run("pnpm", ["exec", "wrangler", "d1", "execute", "datasites-signups", "--local", "--json", "--command", command], { cwd: app }).stdout;
    const start = out.indexOf("[");
    return JSON.parse(out.slice(start))[0].results;
  };
  const submit = (address) =>
    fetch(`${base}/signup?site=electiondata`, {
      method: "POST",
      body: new URLSearchParams({ email: address, "cf-turnstile-response": "XXXX.DUMMY.TOKEN.XXXX" }),
    });
  // Sweep every test row (not just this run's) so an interrupted run can't leave one behind.
  const cleanup = () => sql("DELETE FROM signups WHERE email LIKE 'e2e-%@example.com'");

  cleanup();
  try {
    const first = await submit(email);
    check(first.status === 200 && (await first.text()).includes("Thanks."), `signup returned ${first.status}`);
    const dup = await submit(email.toUpperCase());
    check(dup.status === 200, `repeat signup returned ${dup.status}`);
    const rows = sql(`SELECT site FROM signups WHERE email = '${email}'`);
    check(rows.length === 1 && rows[0].site === "electiondata", `expected one electiondata row, got ${JSON.stringify(rows)}`);
    const bad = await submit("not-an-email");
    check(bad.status === 400, `invalid email returned ${bad.status}, expected 400`);
  } finally {
    cleanup();
  }
  check(sql("SELECT COUNT(*) AS n FROM signups WHERE email LIKE 'e2e-%@example.com'")[0].n === 0, "test signups were not cleaned up");
  console.log(`E2E COMING-SOON OK (${sites.length} sites rendered, signup stored once under electiondata)`);
}

try {
  if (mode === "api") await api();
  else if (mode === "coming-soon") await comingSoon();
  else fail("usage: node scripts/verify/e2e-local.mjs api|coming-soon");
} finally {
  stopAll();
}
