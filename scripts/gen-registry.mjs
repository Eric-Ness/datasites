// Builds packages/registry/src/{sites,datasets}.json from contracts/sites/**.
// The YAML contracts are the source of truth; Workers and the website import the generated JSON.
//   node scripts/gen-registry.mjs          write the files
//   node scripts/gen-registry.mjs --check  exit 1 if either file is out of date (CI)
import { readFileSync, readdirSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const sitesDir = join(root, "contracts", "sites");
const outDir = join(root, "packages", "registry", "src");

const SITE_FIELDS = ["id", "domain", "name", "tagline", "description", "status", "phase"];
const STATUSES = new Set(["coming-soon", "live"]);
const KINDS = new Set(["series", "table", "files"]);
const squash = (text) => String(text ?? "").replace(/\s+/g, " ").trim();

const siteDirs = readdirSync(sitesDir, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort();

const sites = siteDirs.map((name) => {
  const file = join(sitesDir, name, "site.yaml");
  const site = parse(readFileSync(file, "utf8"));
  for (const key of SITE_FIELDS) {
    if (site[key] === undefined || site[key] === "") throw new Error(`${file}: missing "${key}"`);
  }
  if (site.id !== name) throw new Error(`${file}: id "${site.id}" must match folder "${name}"`);
  if (!STATUSES.has(site.status)) throw new Error(`${file}: unknown status "${site.status}"`);
  return Object.fromEntries(SITE_FIELDS.map((key) => [key, key === "description" ? squash(site[key]) : site[key]]));
});

function locationsSpec(raw) {
  if (!raw) return {};
  if ("registry" in raw || "pattern" in raw) {
    const registry = raw.registry ? [raw.registry].flat() : [];
    return { ...(raw.explicit ? { explicit: raw.explicit } : {}), ...(registry.length ? { registry } : {}), ...(raw.pattern ? { pattern: raw.pattern } : {}) };
  }
  return { explicit: raw };
}

const datasets = [];
for (const site of siteDirs) {
  const dir = join(sitesDir, site, "datasets");
  if (!existsSync(dir)) continue;
  for (const fileName of readdirSync(dir).filter((f) => f.endsWith(".yaml")).sort()) {
    const file = join(dir, fileName);
    const c = parse(readFileSync(file, "utf8"));
    if (c.id !== fileName.replace(/\.yaml$/, "")) throw new Error(`${file}: id "${c.id}" must match the file name`);
    const kind = c.kind ?? "series";
    if (!KINDS.has(kind)) throw new Error(`${file}: unknown kind "${kind}"`);
    datasets.push({
      site,
      id: c.id,
      kind,
      title: c.title,
      description: squash(c.description),
      cadence: c.cadence,
      check_every: c.check_every,
      sla: c.sla,
      discontinued: Boolean(c.discontinued),
      needs_credentials: (c.requires_env ?? []).length > 0,
      source: {
        name: c.source?.name,
        publisher: c.source?.publisher ?? null,
        url: c.source?.url,
        license: c.source?.license,
        commercial_ok: c.source?.commercial_ok ?? null,
        attribution: squash(c.source?.attribution),
      },
      series: c.series ?? {},
      locations: locationsSpec(c.locations),
      schema: (c.schema ?? []).map((col) => ({
        name: col.name,
        type: col.type,
        unit: col.unit ?? null,
        nullable: Boolean(col.nullable),
        description: col.description ?? null,
      })),
      key: c.key ?? [],
      partition: c.partition ?? null,
    });
  }
}

const outputs = {
  "sites.json": JSON.stringify(sites, null, 2) + "\n",
  "datasets.json": JSON.stringify(datasets, null, 2) + "\n",
};

if (process.argv.includes("--check")) {
  const stale = Object.entries(outputs).filter(([name, json]) => {
    const file = join(outDir, name);
    return !existsSync(file) || readFileSync(file, "utf8") !== json;
  });
  if (stale.length) {
    console.error(`packages/registry/src/${stale.map(([n]) => n).join(", ")} out of date. Run: pnpm gen`);
    process.exit(1);
  }
  console.log(`registry up to date (${sites.length} sites, ${datasets.length} datasets)`);
} else {
  for (const [name, json] of Object.entries(outputs)) writeFileSync(join(outDir, name), json);
  console.log(`wrote ${sites.length} sites and ${datasets.length} datasets to packages/registry/src/`);
}
