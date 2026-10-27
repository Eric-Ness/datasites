// One Astro project, nine static sites. SITE=<id> picks the site; the output goes to dist/<id>/.
//   SITE=waterdata astro build                         -> dist/waterdata/
//   PUBLIC_API_BASE=http://localhost:8787 SITE=...      -> pages fetch from the local API (?site=<id> is added)
//   WEB_REGISTRY=test/fixtures/registry/index.ts       -> build from a fixture registry instead (tests only)
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "astro/config";
import type { AstroIntegration } from "astro";
import { datasets as registryDatasets, sites as registrySites, type Dataset, type Site } from "@datasites/registry";
import { EXTRA_PAGES, INDICATOR_ROUTES, indicatorPages } from "./src/lib/routes";
import { apiBaseFor } from "./src/lib/urls";

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

// The pages import @datasites/registry; a fixture build aliases it to WEB_REGISTRY. The config itself
// only needs the site list, which sits next to the fixture module as sites.json.
const registryOverride = process.env.WEB_REGISTRY ? here(`./${process.env.WEB_REGISTRY.replace(/\\/g, "/").replace(/^\.\//, "")}`) : null;
const sites: readonly Site[] = registryOverride
  ? (JSON.parse(readFileSync(join(dirname(registryOverride), "sites.json"), "utf8")) as Site[])
  : registrySites;
const datasets: readonly Dataset[] = registryOverride
  ? (JSON.parse(readFileSync(join(dirname(registryOverride), "datasets.json"), "utf8")) as Dataset[])
  : registryDatasets;

const siteId = process.env.SITE?.trim() || "climatedata";
const site = sites.find((s) => s.id === siteId);
if (!site) {
  throw new Error(`SITE="${siteId}" is not in the registry. Use one of: ${sites.map((s) => s.id).join(", ")}`);
}

const apiBase = apiBaseFor(site.domain, process.env.PUBLIC_API_BASE);
// WEB_DIST: where the sites go (default dist/), relative to apps/web or absolute.
const distRoot = resolve(here("./"), process.env.WEB_DIST ?? "dist");

/** Adds the pages only some sites have (the visualizedata gallery, indicator pages). */
function siteRoutes(id: string): AstroIntegration {
  return {
    name: "datasites-site-routes",
    hooks: {
      "astro:config:setup": ({ injectRoute }) => {
        for (const page of EXTRA_PAGES[id] ?? []) {
          injectRoute({ pattern: page.path.replace(/\/$/, ""), entrypoint: page.entrypoint });
        }
        if (indicatorPages({ id }, datasets).length) for (const route of INDICATOR_ROUTES) injectRoute(route);
      },
    },
  };
}

console.log(`[web] site ${site.id} (${site.domain}), API base ${apiBase}`);

export default defineConfig({
  site: `https://${site.domain}`,
  output: "static",
  outDir: join(distRoot, site.id),
  trailingSlash: "always",
  build: { format: "directory" },
  compressHTML: true,
  prefetch: false,
  devToolbar: { enabled: false },
  integrations: [siteRoutes(site.id)],
  vite: {
    define: {
      __SITE_ID__: JSON.stringify(site.id),
      __API_OVERRIDE__: JSON.stringify(process.env.PUBLIC_API_BASE?.trim() ?? ""),
    },
    resolve: {
      alias: registryOverride ? [{ find: /^@datasites\/registry$/, replacement: registryOverride }] : [],
    },
  },
});
