// Builds every site in the registry into dist/<id>/, then confirms each dist/<id>/index.html exists.
//   node scripts/build-all.mjs              all registry sites -> dist/
//   node scripts/build-all.mjs --fixtures   the fixture registry (all three dataset kinds) -> .fixtures-dist/
// WEB_DIST=<dir> (relative to apps/web, or absolute) and PUBLIC_API_BASE=<url> pass through to each build.
// Prints "BUILT <n> SITES" only when every site built.
import { spawnSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { FIXTURES, WEB, astroBin, registrySites } from "./lib.mjs";

const fixtures = process.argv.includes("--fixtures");
const sites = registrySites(fixtures);
const distRoot = process.env.WEB_DIST ?? (fixtures ? FIXTURES.dist : "dist");
const env = {
  ...process.env,
  ASTRO_TELEMETRY_DISABLED: "1",
  WEB_DIST: distRoot,
  ...(fixtures ? { WEB_REGISTRY: FIXTURES.registry } : {}),
};

if (!sites.length) {
  console.error("FAIL: the registry has no sites");
  process.exit(1);
}

const started = Date.now();
for (const site of sites) {
  // Start clean, so a stale index.html can never pass the check below.
  rmSync(resolve(WEB, distRoot, site.id), { recursive: true, force: true });
  const result = spawnSync(process.execPath, [astroBin, "build"], { cwd: WEB, stdio: "inherit", env: { ...env, SITE: site.id } });
  if (result.status !== 0) {
    console.error(`FAIL: astro build for ${site.id} exited ${result.status}`);
    process.exit(1);
  }
}

const missing = sites.filter((site) => !existsSync(resolve(WEB, distRoot, site.id, "index.html")));
if (missing.length) {
  console.error(`FAIL: no index.html for ${missing.map((s) => s.id).join(", ")}`);
  process.exit(1);
}
const seconds = ((Date.now() - started) / 1000).toFixed(0);
console.log(`BUILT ${sites.length} SITES (${sites.map((s) => s.id).join(", ")}) into ${distRoot} in ${seconds}s`);
