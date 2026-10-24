// node-1.2 N5: every site's local catalog lists all of its contracts with a current version (or the dataset
// is waiting on credentials), and the real API Worker under `wrangler dev` serves metadata, locations and a
// data file for every published dataset.
//
// --allow-unpublished <site>/<id>[,...] names datasets that may be unpublished for a recorded reason (an
// upstream outage); each one is printed in the success line, so the exception is never silent.
//
// Seeding all of .data/r2 into wrangler's local R2 would take hours at county scale (one `wrangler r2 object
// put` per file), so this seeds only the objects the API reads for each dataset.
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { ROOT, check, seedLocalR2, startServer, waitForHttp } from "./lib.mjs";

const bucket = join(ROOT, ".data", "r2");
const sites = JSON.parse(readFileSync(join(ROOT, "packages", "registry", "src", "sites.json"), "utf8"));
const readJson = (key) => JSON.parse(readFileSync(join(bucket, key), "utf8"));
const blobKey = (hash) => `blobs/sha256/${hash.slice(0, 2)}/${hash}`;

// 1. catalogs in the local bucket match the contracts
const allowArg = process.argv.indexOf("--allow-unpublished");
const allowed = new Set(allowArg === -1 ? [] : (process.argv[allowArg + 1] ?? "").split(",").filter(Boolean));
const excused = [];
const keys = new Set();
const targets = [];
let waiting = 0;
for (const site of sites) {
  const dir = join(ROOT, "contracts", "sites", site.id, "datasets");
  if (!existsSync(dir)) continue;
  const contracts = readdirSync(dir).filter((f) => f.endsWith(".yaml")).map((f) => parse(readFileSync(join(dir, f), "utf8")));
  if (!contracts.length) continue;
  const catalogKey = `sites/${site.id}/catalog.json`;
  check(existsSync(join(bucket, catalogKey)), `${site.id}: no catalog in .data/r2 (run: uv run datasites run ${site.id} --target local --force)`);
  const catalog = readJson(catalogKey);
  keys.add(catalogKey);
  for (const c of contracts) {
    const entry = catalog.datasets.find((d) => d.id === c.id);
    check(entry, `${site.id}: catalog does not list ${c.id}`);
    if (!entry.current) {
      if (allowed.has(`${site.id}/${c.id}`)) {
        excused.push(`${site.id}/${c.id}`);
        continue;
      }
      check((c.requires_env ?? []).length > 0, `${site.id}/${c.id}: never published and it doesn't need credentials`);
      waiting += 1;
      continue;
    }
    const base = `sites/${site.id}/datasets/${c.id}`;
    const doc = readJson(`${base}/versions/${entry.current}.json`);
    keys.add(`${base}/manifest.json`).add(`${base}/versions/${entry.current}.json`);
    if (existsSync(join(bucket, `ops/state/${site.id}/${c.id}.json`))) keys.add(`ops/state/${site.id}/${c.id}.json`);
    let files = { ...doc.files };
    for (const hash of Object.values(doc.file_shards ?? {})) {
      keys.add(blobKey(hash));
      Object.assign(files, readJson(blobKey(hash)));
    }
    if (doc.locations_blob) keys.add(blobKey(doc.locations_blob));
    const kind = doc.kind ?? "series";
    const paths = Object.keys(files).sort();
    const sample =
      kind === "files"
        ? paths.find((p) => !p.startsWith("download/"))
        : paths.find((p) => p.endsWith(".json") && !p.startsWith("download/"));
    check(sample, `${site.id}/${c.id}: no servable data file in version ${doc.version}`);
    keys.add(blobKey(files[sample].blob));
    targets.push({ site: site.id, id: c.id, kind, sample, version: doc.version });
  }
}
check(targets.length > 0, "no published datasets found in .data/r2");

// 2. seed just those objects into wrangler's local R2
const api = join(ROOT, "apps", "api");
seedLocalR2(keys);

// 3. serve them through the real Worker
const server = startServer("pnpm", ["exec", "wrangler", "dev", "--port", "8797", "--ip", "127.0.0.1", "--var", "ENV:development"], { cwd: api });
try {
  const base = "http://127.0.0.1:8797";
  await waitForHttp(`${base}/v1?site=climatedata`, { server });
  for (const t of targets) {
    const q = `site=${t.site}`;
    const catalog = await (await fetch(`${base}/v1/catalog?${q}`)).json();
    check(catalog.datasets.some((d) => d.id === t.id && d.current === t.version), `${t.site}/${t.id}: API catalog doesn't show version ${t.version}`);
    const meta = await fetch(`${base}/v1/${t.id}?${q}`);
    check(meta.status === 200 && (await meta.json()).kind === t.kind, `${t.site}/${t.id}: metadata ${meta.status}`);
    if (t.kind !== "files") {
      const locs = await fetch(`${base}/v1/${t.id}/locations?${q}`);
      check(locs.status === 200 && (await locs.json()).series.length > 0, `${t.site}/${t.id}: locations ${locs.status}`);
    }
    const file = await fetch(`${base}/v1/${t.id}/${t.sample}?${q}`);
    check(file.status === 200 && file.headers.get("x-data-version") === t.version, `${t.site}/${t.id}: ${t.sample} gave ${file.status}`);
  }
} finally {
  server.stop();
}

const bySite = targets.reduce((acc, t) => ({ ...acc, [t.site]: (acc[t.site] ?? 0) + 1 }), {});
console.log(
  `CATALOGS OK (${targets.length} datasets served across ${Object.keys(bySite).length} sites: ${Object.entries(bySite)
    .map(([s, n]) => `${s}=${n}`)
    .join(", ")}; ${waiting} waiting on credentials${excused.length ? `; allowed unpublished: ${excused.join(", ")}` : ""})`,
);
