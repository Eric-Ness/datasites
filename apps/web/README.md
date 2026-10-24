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

Right now the coming-soon Worker (`apps/coming-soon`) owns every apex domain. A custom domain can belong to only one Worker, so swap one site at a time:

1. Remove the domain's route from `apps/coming-soon/wrangler.jsonc` and deploy the coming-soon Worker:
   `pnpm --filter @datasites/coming-soon run deploy`
2. Build the site against the production API, without `PUBLIC_API_BASE`:
   `SITE=climatedata pnpm --filter @datasites/web run build`
3. Deploy it:
   `pnpm --filter @datasites/web exec wrangler deploy -c wrangler/climatedata.jsonc`
4. Check `https://climatedata.dev/`, a dataset page and `/status/`.

Between steps 1 and 3 the apex domain serves nothing for a moment, so run them back to back. To roll back, deploy the coming-soon Worker with the route restored. That moves the domain back.

The privacy page still mentions launch-email signups, so keep the D1 signups table until those emails have gone out.

## Layout

- `src/pages/`: the pages every site gets (home, datasets, one page per dataset, docs, API reference, errors, status, sources, privacy, 404, sitemap.xml and robots.txt)
- `src/routes/gallery.astro`: the visualizedata gallery. `astro.config.ts` adds it only for the sites listed in `EXTRA_PAGES` (`src/lib/routes.ts`).
- `src/lib/`: pure helpers used both at build time and in the browser: URLs, snippets, formatting, routes, theme colors and JSON-LD
- `src/client/`: browser scripts: the SVG line chart, the dataset explorers, the status page and the gallery
- `wrangler/`: one Worker config per site
