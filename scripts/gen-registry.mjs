// Builds packages/registry/src/sites.json from contracts/sites/*/site.yaml.
// The YAML contracts are the source of truth; Workers import the generated JSON.
//   node scripts/gen-registry.mjs          write the file
//   node scripts/gen-registry.mjs --check  exit 1 if the file is out of date (CI)
import { readFileSync, readdirSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const sitesDir = join(root, "contracts", "sites");
const outFile = join(root, "packages", "registry", "src", "sites.json");

const REQUIRED = ["id", "domain", "name", "tagline", "description", "status", "phase"];
const STATUSES = new Set(["coming-soon", "live"]);

const sites = readdirSync(sitesDir, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => {
    const file = join(sitesDir, d.name, "site.yaml");
    const site = parse(readFileSync(file, "utf8"));
    for (const key of REQUIRED) {
      if (site[key] === undefined || site[key] === "") throw new Error(`${file}: missing "${key}"`);
    }
    if (site.id !== d.name) throw new Error(`${file}: id "${site.id}" must match folder "${d.name}"`);
    if (!STATUSES.has(site.status)) throw new Error(`${file}: unknown status "${site.status}"`);
    return Object.fromEntries(REQUIRED.map((key) => [key, site[key]]));
  })
  .sort((a, b) => a.id.localeCompare(b.id));

const json = JSON.stringify(sites, null, 2) + "\n";

if (process.argv.includes("--check")) {
  const current = existsSync(outFile) ? readFileSync(outFile, "utf8") : "";
  if (current !== json) {
    console.error("packages/registry/src/sites.json is out of date. Run: pnpm gen");
    process.exit(1);
  }
  console.log(`registry up to date (${sites.length} sites)`);
} else {
  writeFileSync(outFile, json);
  console.log(`wrote ${sites.length} sites to packages/registry/src/sites.json`);
}
