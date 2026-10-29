// Keeps the live datasites and visualizedata.org from drifting apart. Reads every site's live /v1/catalog and
// /v1/<dataset>, this repo's contracts, and a visualizedata.org checkout beside this one (or the path given):
// its src/data/site/sources.json (what /sources/ says about each site), pipeline/config/indicators.json and
// us-indicators.json (the datasets its indicators read) and src/data/{indicators,us-indicators} (what it built).
//
//   node scripts/verify/visualizedata-sync.mjs [path to visualizedata.org] [--json out.json]
//
// Fails (exit 1, one FAIL line per drift) when:
//   1. a site's live catalog doesn't list exactly its contracts (the API and the website disagree)
//   2. /v1/<dataset> doesn't serve the catalog's current version, or answers for an unpublished dataset
//   3. a dataset is unpublished although its pipeline needs no credentials (it should have run)
//   4. visualizedata.org's sources.json lists other datasets than a catalog, or calls a published dataset
//      unpublished or the reverse (rebuild it: python -m pipeline.places.sources)
//   5. a visualizedata.org indicator registry names a dataset that is missing or unpublished
//   6. apps/web/src/lib/visualizedata.json is out of step with visualizedata.org (run sync-visualizedata.mjs)
// and then lists, without failing, what visualizedata.org doesn't use yet and what is waiting for credentials.
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./lib.mjs";

const args = process.argv.slice(2);
const jsonAt = args.indexOf("--json");
const jsonOut = jsonAt === -1 ? null : args[jsonAt + 1];
const repo = args.find((a, i) => !a.startsWith("--") && (jsonAt === -1 || i !== jsonAt + 1)) ?? join(ROOT, "..", "visualizedata.org");
const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));

const sites = readJson(join(ROOT, "packages", "registry", "src", "sites.json")).filter((s) => s.status === "live");
const registry = readJson(join(ROOT, "packages", "registry", "src", "datasets.json"));
const mirror = readJson(join(ROOT, "apps", "web", "src", "lib", "visualizedata.json"));
const sources = readJson(join(repo, "src", "data", "site", "sources.json"));
const worldReg = readJson(join(repo, "pipeline", "config", "indicators.json"));
const usReg = readJson(join(repo, "pipeline", "config", "us-indicators.json"));

const failures = [];
const drift = (message) => failures.push(message);

async function getJson(url) {
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, { headers: { accept: "application/json" }, signal: AbortSignal.timeout(30_000) });
      return { status: res.status, body: res.headers.get("content-type")?.includes("json") ? await res.json() : null };
    } catch (err) {
      if (attempt === 3) throw new Error(`${url}: ${err.message}`);
    }
  }
}

// --- 1-3: each site's live catalog and dataset endpoints against its contracts -----------------------------

const live = new Map(); // "site.dev" -> Map(id -> catalog entry)
for (const site of sites) {
  const api = `https://api.${site.domain}/v1`;
  const { status, body } = await getJson(`${api}/catalog`);
  if (status !== 200 || !body?.datasets) {
    drift(`${site.domain}: /v1/catalog answered ${status}`);
    continue;
  }
  const entries = new Map(body.datasets.map((d) => [d.id, d]));
  live.set(site.domain, entries);
  const contracts = registry.filter((d) => d.site === site.id);
  const contractIds = new Set(contracts.map((d) => d.id));
  for (const id of contractIds) if (!entries.has(id)) drift(`${site.domain}: contract ${id} is not in the live catalog (API not redeployed?)`);
  for (const id of entries.keys()) if (!contractIds.has(id)) drift(`${site.domain}: live catalog lists ${id}, which has no contract here`);

  await Promise.all(
    contracts.filter((c) => entries.has(c.id)).map(async (c) => {
      const entry = entries.get(c.id);
      const meta = await getJson(`${api}/${c.id}`);
      if (entry.current === null) {
        if (meta.status !== 404) drift(`${site.domain}/${c.id}: unpublished in the catalog but /v1/${c.id} answered ${meta.status}`);
        if (!c.needs_credentials) drift(`${site.domain}/${c.id}: never published, and its pipeline needs no credentials`);
      } else if (meta.status !== 200) {
        drift(`${site.domain}/${c.id}: catalog says ${entry.current} but /v1/${c.id} answered ${meta.status}`);
      } else if (meta.body?.version !== entry.current) {
        drift(`${site.domain}/${c.id}: catalog says ${entry.current} but /v1/${c.id} serves ${meta.body?.version}`);
      }
    }),
  );
}

// --- 4: what visualizedata.org's /sources/ page says about each site --------------------------------------

const used = new Map(); // "site.dev/id" -> sources.json entry
for (const s of sources.sites) {
  const entries = live.get(s.site);
  if (!entries) {
    if (sites.some((x) => x.domain === s.site)) continue; // its catalog failed above
    drift(`sources.json lists ${s.site}, which is not a live site`);
    continue;
  }
  const listed = new Map(s.datasets.map((d) => [d.id, d]));
  for (const id of entries.keys()) if (!listed.has(id)) drift(`sources.json: ${s.site} ${id} is in the catalog but not on /sources/`);
  for (const [id, d] of listed) {
    used.set(`${s.site}/${id}`, d);
    const entry = entries.get(id);
    if (!entry) drift(`sources.json: ${s.site} ${id} is on /sources/ but no longer in the catalog`);
    else if (d.published !== (entry.current !== null))
      drift(`sources.json: ${s.site} ${id} says ${d.published ? "published" : "not published"}, the catalog says ${entry.current ? "published" : "not published"}`);
  }
}
for (const site of sites) if (!sources.sites.some((s) => s.site === site.domain)) drift(`sources.json has no entry for ${site.domain}`);

// --- 5: the indicator registries name real, published datasets -------------------------------------------

const named = [
  ...Object.entries(worldReg.datasets).filter(([, d]) => d.via).map(([id, d]) => [d.via, id, "indicators.json"]),
  ...Object.entries(usReg.datasets).filter(([, d]) => d.site).map(([id, d]) => [d.site, id, "us-indicators.json"]),
];
for (const [site, id, file] of named) {
  const entry = live.get(site)?.get(id);
  if (!live.has(site)) continue;
  if (!entry) drift(`${file}: ${site} ${id} is not in the catalog`);
  else if (entry.current === null) drift(`${file}: ${site} ${id} is not published`);
}

// --- 6: this repo's copy of what visualizedata.org builds from each dataset -------------------------------

const built = new Set();
for (const dir of ["indicators", "us-indicators"]) {
  for (const f of readdirSync(join(repo, "src", "data", dir)).filter((f) => f.endsWith(".json"))) {
    const ind = readJson(join(repo, "src", "data", dir, f));
    if (ind.source?.via) built.add(ind.id);
  }
}
const mirrored = new Set(Object.keys(mirror.indicators));
for (const id of built) if (!mirrored.has(id)) drift(`visualizedata.json lacks indicator ${id} (node scripts/sync-visualizedata.mjs)`);
for (const id of mirrored) if (!built.has(id)) drift(`visualizedata.json has indicator ${id}, which visualizedata.org no longer builds`);
const usedKeys = new Set(
  [...used].filter(([, d]) => d.used).map(([k]) => {
    const [domain, id] = k.split("/");
    return `${sites.find((s) => s.domain === domain)?.id}/${id}`;
  }),
);
for (const k of usedKeys) if (!mirror.datasets[k]) drift(`visualizedata.json lacks dataset ${k} (node scripts/sync-visualizedata.mjs)`);
for (const k of Object.keys(mirror.datasets)) if (!usedKeys.has(k)) drift(`visualizedata.json has dataset ${k}, which visualizedata.org no longer uses`);

// --- report -------------------------------------------------------------------------------------------------

const notShown = [];
const waiting = [];
for (const [domain, entries] of live) {
  const site = sites.find((s) => s.domain === domain);
  for (const [id, entry] of entries) {
    if (entry.current === null) {
      if (registry.find((d) => d.site === site.id && d.id === id)?.needs_credentials) waiting.push(`${domain}/${id}`);
    }
    else if (!used.get(`${domain}/${id}`)?.used) notShown.push(`${domain}/${id} (${entry.title})`);
  }
}
const behind = [...used]
  .filter(([k, d]) => d.used && d.versions.length && !d.versions.includes(live.get(k.split("/")[0])?.get(k.split("/")[1])?.current))
  .map(([k]) => k);

if (jsonOut) writeFileSync(jsonOut, JSON.stringify({ failures, notShown, waiting, behind, checked_at: new Date().toISOString() }, null, 2) + "\n");
console.log(`Not used by visualizedata.org yet (${notShown.length}):${notShown.map((x) => `\n  ${x}`).join("") || " none"}`);
console.log(`Listed but waiting for credentials (${waiting.length}): ${waiting.join(", ") || "none"}`);
console.log(`Used, and visualizedata.org was built from an older version (${behind.length}): ${behind.join(", ") || "none"}`);
if (failures.length) {
  for (const f of failures) console.error(`FAIL: ${f}`);
  process.exit(1);
}
console.log(`SYNC OK (${live.size} sites, ${[...live.values()].reduce((n, m) => n + m.size, 0)} datasets, ${named.length} registry datasets, ${built.size} indicators)`);
