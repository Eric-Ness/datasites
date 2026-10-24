import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
export const ROOT = join(WEB, "..", "..");

const require = createRequire(import.meta.url);
export const astroBin = join(dirname(require.resolve("astro/package.json")), "bin", "astro.mjs");

/** Fixture builds read test/fixtures/registry and write to .fixtures-dist/ instead of dist/. */
export const FIXTURES = {
  registry: "test/fixtures/registry/index.ts",
  sitesJson: join(WEB, "test", "fixtures", "registry", "sites.json"),
  dist: ".fixtures-dist",
};

export function registrySites(fixtures) {
  const file = fixtures ? FIXTURES.sitesJson : join(ROOT, "packages", "registry", "src", "sites.json");
  return JSON.parse(readFileSync(file, "utf8"));
}
