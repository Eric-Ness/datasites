# @datasites/web

One Astro project that builds the static website of every site in the registry (climatedata.dev, waterdata.dev and the rest). Pages come from `@datasites/registry` at build time. Charts, previews and the status page fetch from the API in the browser, so a data update never needs a rebuild. A new dataset only needs a rebuild, with no code change.

## Commands

Run these from the repo root.

| Command | What it does |
|---|---|
| `SITE=waterdata pnpm --filter @datasites/web run build` | Builds one site into `apps/web/dist/waterdata/`. Without `SITE`, it builds climatedata. |
| `pnpm --filter @datasites/web run build:all` | Builds every registry site and prints `BUILT <n> SITES` |
| `SITE=waterdata pnpm --filter @datasites/web run dev` | Starts a dev server on http://localhost:4321 |
| `pnpm --filter @datasites/web run typecheck` | Runs `astro check` |
| `pnpm --filter @datasites/web run test` | Runs the vitest unit tests |
| `node scripts/verify/web.mjs` | Checks every built site: pages, links, metadata, sitemap and Worker configs. Prints `WEB OK` |

`PUBLIC_API_BASE` changes where the pages fetch data from. It defaults to `https://api.<domain>`. On a local or workers.dev base, every request gets `?site=<id>`:

```sh
PUBLIC_API_BASE=http://127.0.0.1:8787 SITE=climatedata pnpm --filter @datasites/web run build
```

Fixture builds use `test/fixtures/registry`, which has series, table and files examples, and write to `.fixtures-dist/`:

```sh
cd apps/web && node scripts/build-all.mjs --fixtures
node scripts/verify/web.mjs --registry apps/web/test/fixtures/registry --dist apps/web/.fixtures-dist --no-wrangler
```

Astro is pinned to 7.4.1 because 7.4.2 refuses to run on Node versions older than 22.23.4.

## Deploying a site

Each site is an assets-only Worker named `web-<id>`, configured in `wrangler/<id>.jsonc`. It has no script. It serves `dist/<id>` on the apex domain, returns `404.html` for missing paths, and redirects `/docs` to `/docs/`.

The **deploy** workflow (`.github/workflows/deploy.yml`) builds and deploys every site listed in its `WEB_SITES`, against the production API. To launch a site, add it there together with its `api.<domain>` route; docs/SETUP.md section 5 has the steps. By hand, the same thing for one site is:

1. Build the site against the production API, without `PUBLIC_API_BASE`:
   `SITE=climatedata pnpm --filter @datasites/web run build`
2. Deploy it:
   `pnpm --filter @datasites/web exec wrangler deploy -c wrangler/climatedata.jsonc`
3. Check `https://climatedata.dev/`, a dataset page and `/status/`.

A custom domain can belong to only one Worker. If the coming-soon Worker (`apps/coming-soon`) holds a domain, remove the domain from its routes and redeploy it first, then deploy the site straight after, because the domain serves nothing in between.

The privacy page explains launch-email signups for any site that used the coming-soon placeholder; keep the D1 signups table until those emails have gone out.

## Layout

- `src/pages/`: the pages every site gets (home, datasets, one page per dataset, docs, API reference, errors, status, sources, privacy, 404, sitemap.xml and robots.txt)
- `src/routes/gallery.astro`: the visualizedata gallery. `astro.config.ts` adds it only for the sites listed in `EXTRA_PAGES` (`src/lib/routes.ts`).
- `src/lib/`: pure helpers used both at build time and in the browser: URLs, snippets, formatting, routes, theme colors and JSON-LD
- `src/client/`: browser scripts: the SVG line chart, the dataset explorers, the status page and the gallery
- `wrangler/`: one Worker config per site
