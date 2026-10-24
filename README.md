# datasites

A family of public data-API sites (climatedata.dev, economicdata.dev, electiondata.dev, waterdata.dev and six more) running on one platform.

Each piece has one job:
- **Pipelines** fetch public data on a schedule, validate it, and publish it as static JSON/CSV/Parquet files to Cloudflare R2.
- **The API Worker** serves those files for every site at `api.<domain>`.
- **The coming-soon Worker** holds every domain that doesn't have a real site yet.

- **Plan and research:** [docs/PLAN.md](docs/PLAN.md), [docs/research/](docs/research/)
- **Steps that need your Cloudflare/GitHub accounts:** [docs/SETUP.md](docs/SETUP.md)

## Layout

```
apps/api/            API Worker (Hono): site from Host header, R2 manifest -> version -> content-hashed file
apps/coming-soon/    placeholder pages + launch-email signup (D1, Turnstile) for every domain
packages/registry/   site list generated from contracts/sites/*/site.yaml (pnpm gen)
contracts/           source of truth: one site.yaml per site, one YAML contract per dataset
pipelines/           Python (uv workspace): core engine + one package per domain
.github/workflows/   ci, deploy, ingest-<domain>
```

## Local development

Requirements: Node 22 with pnpm, plus uv for Python.

```powershell
pnpm install
pnpm test                      # Worker tests
cd pipelines; uv sync; uv run pytest -q; cd ..
```

**Run a pipeline against the live source into a local copy of the bucket (`.data/r2`):**
```powershell
cd pipelines
uv run datasites run climatedata --target local
cd ..
```

**Serve the local output through the real API Worker:**
```powershell
Copy-Item apps/api/.dev.vars.example apps/api/.dev.vars
pnpm --filter @datasites/api seed:local     # copies .data/r2 into wrangler's local R2
pnpm dev:api                                # http://localhost:8787/v1/status?site=climatedata
```

**Coming-soon pages and signup:**
```powershell
Copy-Item apps/coming-soon/.dev.vars.example apps/coming-soon/.dev.vars
pnpm --filter @datasites/coming-soon db:migrate:local
pnpm dev:coming-soon                        # http://localhost:8788/?site=electiondata
```

In local development, `?site=<id>` picks the site. `wrangler dev` rewrites every request's hostname to the first route, and `ENV=development` in `.dev.vars` is what allows the override. Production ignores `?site=`.

## How a dataset gets published

`datasites run <site>/<dataset>` does the following:

1. Fetches each source with conditional GETs.
2. Archives the raw file under `raw/`.
3. Checks for changes by hashing the source content, the contract version and the code version. If nothing changed, it stops.
4. Transforms the data into a long table.
5. Validates it against the contract's checks.
6. Uploads the output files under `blobs/sha256/`. Each file is stored once, by content hash.
7. Writes an immutable version document.
8. Points `manifest.json` at the new version with a conditional write.

If validation fails, the version is quarantined, the old one keeps serving, and the command exits with code 2.

Other commands:

```powershell
uv run datasites rollback climatedata/co2-monthly <version> --target production
uv run datasites promote  climatedata/co2-monthly <quarantined-version> --target production
uv run datasites lint     # every contract has a license, attribution and commercial_ok flag
```

## Adding a dataset

1. Write `contracts/sites/<site>/datasets/<id>.yaml`. Copy `co2-monthly.yaml` as a template.
2. Write the pipeline module the contract names. It needs:
   - `RESOURCES`: name → URL.
   - optional `normalize(name, body)`, to strip volatile parts before hashing.
   - `transform(raw) -> polars.DataFrame` with columns `series, location, <schema columns>`.
3. Add tests that use recorded fixtures under `pipelines/tests/fixtures/`.
4. Add the dataset's site to an `ingest-*.yml` workflow schedule.
