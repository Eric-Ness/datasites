# Data-API family: deployment and data-update plan

Status as of 23 Oct 2026. I checked the Cloudflare and GitHub figures against their current docs this week. Sources are listed at the end.

## Decisions at a glance

| Area | Decision |
|---|---|
| Cloudflare plan | **Workers Paid ($5/month) from day one.** The Free plan allows 5 Cron Triggers per account, 10 ms CPU per request and 100k requests/day. Free Workflows also get 10 ms CPU per step, which is too little for ingestion. |
| Websites | One static Astro project per domain, deployed as an assets-only Worker on the apex domain. Not Pages. |
| API | One Worker, `datasites-api`, attached to `api.<domain>` for every site. It picks the site from the Host header. |
| Storage | One private R2 bucket per environment. File bodies are stored as content-addressed blobs, and each dataset has one small mutable manifest. No public bucket access. |
| Edge caching | The Worker uses the Cache API, keyed by the immutable blob hash. Workers Cache stays off for now (see 3.7). |
| Ingestion | Python 3.13 with uv, polars and duckdb. TypeScript only for small hourly feeds that run in Cloudflare Workflows. |
| Schedulers | GitHub Actions cron for daily-or-slower and heavy jobs. Workflows `schedules` for small hourly feeds. No VPS until a job outgrows a GitHub runner. |
| Repo | One **public** monorepo: pnpm + Turborepo for TypeScript, a uv workspace for Python. |
| Publishing | Content-hashed versions. A conditional PUT flips the manifest pointer, so publishes are atomic and rollback takes one write. |
| Monitoring | `/v1/status` per site, a 15-minute freshness cron in the API Worker, and healthchecks.io dead-man switches. |
| D1 | Email signups now. Accounts and API keys later. Data stays in files. |

---

## 1. Repo structure

```
datasites/                          public GitHub repo
├─ apps/
│  ├─ api/                          the single API Worker (Hono, TS)
│  │  ├─ src/ index.ts router.ts middleware/ handlers/ r2.ts cache.ts status.ts
│  │  └─ wrangler.jsonc
│  ├─ ingest-hot/                   TS Worker that hosts hourly Workflows (air, later others)
│  └─ coming-soon/                  one Worker for all placeholder domains + signup form (D1, Turnstile)
├─ sites/                           one thin Astro project per domain
│  ├─ climatedata/   astro.config.mjs  site.yaml  src/content/  public/  wrangler.jsonc
│  ├─ economicdata/
│  └─ airdata/ carbondata/ electiondata/ satellitedata/ visualizedata/ waterdata/ wildfiredata/
├─ packages/
│  ├─ theme/                        Astro integration: layouts + injected routes (catalog, dataset, status, docs)
│  ├─ ui/                           chart components (Observable Plot), code-sample tabs, dataset cards
│  ├─ contracts-ts/                 loads contracts/*.yaml, generates OpenAPI per site, TS validators
│  └─ geo/                          location registry (ISO 3166-1/2, US FIPS) as JSON, read by TS and Python
├─ contracts/                       source of truth, language-neutral YAML
│  └─ sites/<site>/site.yaml, sites/<site>/datasets/<dataset>.yaml
├─ pipelines/                       Python uv workspace
│  ├─ pyproject.toml                [tool.uv.workspace] members = ["core", "climate", "economic", ...]
│  ├─ core/       datasites_core: fetch+retry, change detection, validation engine, R2 publish, manifest, CLI
│  ├─ climate/    datasites_climate: gistemp.py, noaa_gml.py, hadcrut.py, era5_country.py
│  ├─ economic/   datasites_economic: bls.py, bea.py, worldbank.py, treasury.py
│  ├─ air/ wildfire/ carbon/ election/ water/ satellite/
│  └─ tests/fixtures/               recorded upstream responses, so tests run offline
├─ ops/                             runbooks, zone list, scripts (rollback, promote, purge)
├─ .github/workflows/
└─ pnpm-workspace.yaml  turbo.json  .gitattributes (* text=auto eol=lf)
```

**Ingestion language: Python.** The reasons:
- The libraries you need are Python-first or Python-only: xarray with NetCDF/Zarr for ERA5, `cdsapi`, `earthaccess` (NASA Earthdata), `pystac-client`, USGS `dataretrieval`, `sdmx1` (IMF/OECD SDMX) and `wbgapi`. Rewriting NetCDF handling in TypeScript is not realistic.
- polars and duckdb process multi-GB tables on a single 16 GB public runner and write Parquet natively. pandera has a polars backend for schema checks.
- TypeScript stays where it already runs: the Worker, the Astro sites, and the hourly feeds inside Workflows (small JSON in, small JSON out, 128 MB isolate).
- Both languages read the same `contracts/` YAML, so schema and sanity rules are written once. The TS validator implements a subset (types, ranges, row counts), which is enough for hourly feeds.

Windows notes: uv, polars, duckdb and wrangler all run natively. The `.gitattributes` LF rule matters because content hashes of files generated on your laptop must match Linux CI. Request NetCDF rather than GRIB from Copernicus so you avoid the eccodes dependency.

---

## 2. Hosting and deployment

### 2.1 Front-ends: Workers static assets, not Pages

- The Cloudflare Pages docs now open with a callout: "Start new projects with Workers." They describe Workers as Cloudflare's primary platform. Pages is not deprecated, but new features go to Workers.
- `@astrojs/cloudflare` (v14.4.2) dropped Pages deployment in v13. Cloudflare acquired the Astro team in January 2026, so Astro on Workers is the supported path.
- Static asset requests on Workers are free and unlimited. A static Astro build needs no adapter. Paid allows 100,000 files per Worker version, at most 25 MiB each.
- Each site deploys as an assets-only Worker named `web-<site>`. Charts fetch live data from `api.<site>` in the browser, so a data update never needs a site rebuild. Dataset pages carry schema.org `Dataset` JSON-LD, built from the contracts, for Google Dataset Search.
- Never enable Workers Cache on these site Workers. With caching on, static asset requests are billed at the normal request rate.

```jsonc
// sites/climatedata/wrangler.jsonc
{
  "name": "web-climatedata",
  "compatibility_date": "2026-10-01",
  "assets": { "directory": "./dist", "not_found_handling": "404-page" },
  "routes": [{ "pattern": "climatedata.dev", "custom_domain": true }],
  "workers_dev": false,
  "preview_urls": true
}
```

### 2.2 Hostnames in each zone

| Host | Served by | Notes |
|---|---|---|
| `climatedata.dev` | `web-climatedata` | Worker custom domain. Cloudflare creates the DNS record and certificate. |
| `www.climatedata.dev` | Single Redirect rule to the apex (301) | Proxied placeholder `AAAA www 100::` |
| `api.climatedata.dev` | `datasites-api` | Worker custom domain |

The API goes on a subdomain rather than under `/v1` on the apex for three reasons. The website stays a free assets-only Worker. Security settings for scripted clients stay separate from the website's. And you avoid any precedence questions between routes and custom domains. CORS is one header for public GET data.

### 2.3 One API Worker on many custom domains

```jsonc
// apps/api/wrangler.jsonc  (list only launched sites; full list shown)
{
  "name": "datasites-api",
  "main": "src/index.ts",
  "compatibility_date": "2026-10-01",
  "workers_dev": false,
  "preview_urls": true,
  "observability": { "enabled": true, "head_sampling_rate": 0.1 },
  "routes": [
    { "pattern": "api.airdata.dev",       "custom_domain": true },
    { "pattern": "api.carbondata.dev",    "custom_domain": true },
    { "pattern": "api.climatedata.dev",   "custom_domain": true },
    { "pattern": "api.economicdata.dev",  "custom_domain": true },
    { "pattern": "api.electiondata.dev",  "custom_domain": true },
    { "pattern": "api.satellitedata.dev", "custom_domain": true },
    { "pattern": "api.visualizedata.dev", "custom_domain": true },
    { "pattern": "api.waterdata.dev",     "custom_domain": true },
    { "pattern": "api.wildfiredata.dev",  "custom_domain": true }
  ],
  "triggers": { "crons": ["*/15 * * * *"] },
  "r2_buckets": [{ "binding": "DATA", "bucket_name": "datasites-data" }],
  "ratelimits": [
    { "name": "RL_ANON", "namespace_id": "1001", "simple": { "limit": 120, "period": 60 } }
  ],
  "analytics_engine_datasets": [{ "binding": "USAGE", "dataset": "api_usage" }],
  "vars": { "ENV": "production" },
  "env": {
    "staging": {
      "name": "datasites-api-staging",
      "workers_dev": true,
      "routes": [],
      "triggers": { "crons": [] },
      "r2_buckets": [{ "binding": "DATA", "bucket_name": "datasites-data-staging" }],
      "ratelimits": [
        { "name": "RL_ANON", "namespace_id": "1002", "simple": { "limit": 600, "period": 60 } }
      ],
      "analytics_engine_datasets": [{ "binding": "USAGE", "dataset": "api_usage_staging" }],
      "vars": { "ENV": "staging" }
    }
  }
}
```

- A custom domain needs an **Active** zone in your account and no existing CNAME on that hostname. Hostname matching is exact. Limits are 100 custom domains per zone and 1,000 zones per Worker.
- Bindings and vars are not inherited by `env.staging`, so repeat them. `routes: []` stops staging from ever claiming a production hostname.
- The host-to-site map (`api.climatedata.dev` → `climatedata`) is generated from `contracts/sites/*/site.yaml`, so adding a site never means editing a hand-written list.

### 2.4 Environments

| Environment | API | Websites | Data | Trigger |
|---|---|---|---|---|
| PR preview | `wrangler versions upload --preview-alias pr-<n>` gives `pr-<n>-datasites-api.<sub>.workers.dev` | same command for each changed `web-*` | Production bucket, read-only (a Version URL uses the deployed config and bindings) | Every PR |
| Staging | `datasites-api-staging` on workers.dev | none | `datasites-data-staging`, filled by pipelines run with `target=staging` | Push to main, before prod |
| Production | `datasites-api` on `api.*` | `web-*` on the apexes | `datasites-data` | Push to main, after staging smoke tests pass |

Two catches with workers.dev previews. They cannot use custom domains, and the Cache API only works on custom domains. So the Worker accepts a `?site=climatedata` override only when the hostname ends in `.workers.dev`, and previews always read R2 directly. Version URLs are public by default, which is fine for open data. Code rollback is `wrangler rollback`. Data rollback is a manifest flip (4.6). Keep the two separate.

### 2.5 CI/CD with GitHub Actions and wrangler

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | pull_request | `pnpm turbo run lint typecheck test build --filter=...[origin/main]`, `pytest` on recorded fixtures, contract lint (every dataset has license, source, cadence, SLA), OpenAPI lint, preview uploads plus a PR comment with the URLs |
| `deploy.yml` | push to main | Deploy staging, smoke-test it, deploy prod. Only changed Workers, through `cloudflare/wrangler-action@v3` |
| `ingest-<domain>.yml` | schedule + dispatch | One per domain (skeleton in 4.10) |
| `backfill.yml` | dispatch | Year-chunked matrix, then an assemble job (4.7) |
| `data-rollback.yml` | dispatch | Inputs site, dataset, version. Flips the manifest and purges the manifest tag. |
| `janitor.yml` | weekly | Mark-and-sweep of unreferenced blobs, consistency check of manifests against blobs |
| `upstream-canary.yml` | weekly | GETs every source URL and opens a GitHub issue on 404, a redirect, or a changed content-type or schema |

Tokens: `CLOUDFLARE_API_TOKEN` is scoped to Workers Scripts Edit plus Workers Routes Edit on the 10 zones. Separate tokens cover R2 (bucket-scoped Object Read & Write, one for prod and one for staging) and cache purge (Zone Cache Purge only). Cloudflare's Workers Builds could also deploy on push, but keeping CI in GitHub Actions puts path filters, secrets and logs for both code and ingestion in one place.

### 2.6 DNS migration (DirectNIC to Cloudflare)

1. **Add the zones in your existing Cloudflare account.** In the dashboard: Domains, then Onboard a domain, enter the apex, choose Free. Choose to add records manually, not import, so DirectNIC parking records are not copied. You can also use this script:
   ```powershell
   $domains = "airdata.dev","carbondata.dev","climatedata.dev","economicdata.dev","electiondata.dev",
              "satellitedata.dev","visualizedata.dev","waterdata.dev","wildfiredata.dev","votingdata.net"
   foreach ($d in $domains) {
     $body = @{ name = $d; account = @{ id = $env:CF_ACCOUNT_ID }; type = "full" } | ConvertTo-Json
     $r = Invoke-RestMethod -Method Post -Uri "https://api.cloudflare.com/client/v4/zones" `
           -Headers @{ Authorization = "Bearer $env:CF_TOKEN" } -ContentType "application/json" -Body $body
     "{0}`t{1}" -f $d, ($r.result.name_servers -join ", ")
   }
   ```
   It prints each zone's two assigned nameservers. They are usually the same pair across one account, but copy them per zone. Cloudflare says assignments cannot be changed.
2. **Check DNSSEC is off at DirectNIC** for each domain. Changing nameservers while DNSSEC is active can make the domain unreachable. New registrations normally have it off.
3. **Change the nameservers at DirectNIC.** Go to My Services, then Domains, pick the domain, and click Edit next to Name Servers. Replace `ns0/ns1.directnic.com` with the two Cloudflare names exactly as shown, then Submit. Repeat for all 10.
4. **Wait for activation.** It usually takes minutes to a few hours, up to 24 hours. Use "Check nameservers now" on each zone's Overview. To verify: `Resolve-DnsName climatedata.dev -Type NS -Server 1.1.1.1`. Free-plan zones still pending after 28 days are deleted automatically.
5. **HTTPS.** Universal SSL is issued 15 minutes to 24 hours after activation. Because .dev is HSTS-preloaded, browsers will not load anything until then. Check under SSL/TLS, then Edge Certificates. Turn on Always Use HTTPS and set minimum TLS 1.2. Workers have no origin, so the SSL mode setting does not matter.
6. **Re-enable DNSSEC through Cloudflare.** Copy the DS record into DirectNIC if it accepts DS records for .dev/.net. If not, skip it.
7. **Per-zone settings.** Leave Bot Fight Mode off: it challenges scripted clients, and on Free you cannot scope it away from `api.*`. During onboarding, decide on the AI-crawler blocking option. For documentation sites you probably want it to allow crawlers. Enable Email Routing for `hello@<domain>` to your inbox, and add a DMARC `p=reject` record since you won't send mail from these domains yet.
8. **Deploy the Workers** (coming-soon first, see Phase 0). Do not hand-create A or CNAME records for the apex or `api`, because custom-domain creation fails if a CNAME exists. For `www`, add a proxied `AAAA www 100::` and a Single Redirect rule from `www.<d>` to `https://<d>` with the path, as a 301, keeping the query string.
9. **After the 60-day transfer lock,** consider moving the registrations to Cloudflare Registrar, which sells .dev and .net renewals at wholesale cost.

### 2.7 votingdata.net redirect

1. After the zone is Active, add proxied placeholders: `A @ 192.0.2.1`, `AAAA @ 100::`, `AAAA www 100::`.
2. Go to Rules, then Redirect Rules, and create a Single Redirect (Free allows 10 per zone):
   - When: `(http.host eq "votingdata.net") or (http.host eq "www.votingdata.net")`
   - Then, dynamic: `concat("https://electiondata.dev", http.request.uri.path)`, status **302**, preserve query string on.
3. Use **302, not 301.** Browsers cache a 301 more or less indefinitely, and you may later turn this domain into the roll-call-votes sister site. Switch to 301 only if you drop that plan.

---

## 3. API design

### 3.1 URL scheme (each `api.<site>` host)

| Endpoint | Purpose | Browser `max-age` |
|---|---|---|
| `GET /v1` | Index with links | 300 |
| `GET /v1/catalog` | Every dataset on this site: current version, cadence, license, coverage | 300 |
| `GET /v1/{dataset}` | Dataset metadata: schema, series, provenance, attribution, links | 300 |
| `GET /v1/{dataset}/locations` | Valid location codes | 86400 |
| `GET /v1/{dataset}/{series}/{location}[.json\|.csv]` | The data | by cadence (3.4) |
| `GET /v1/{dataset}/download/{name}.parquet` (also `.csv.gz`) | Bulk files. `Range` and `HEAD` supported, so DuckDB and polars can query them remotely. | by cadence |
| `GET /v1/{dataset}/versions` | Version history | 300 |
| `GET /v1/{dataset}@{version}/...` | Any of the above, pinned to a version | 1 year, `immutable` |
| `GET /v1/status` | Freshness per dataset | 60 |
| `GET /v1/openapi.json` | OpenAPI 3.1 for this site | 3600 |

- Reserved slugs are `catalog`, `status`, `openapi.json`, `versions`, `locations` and `download`. CI rejects a dataset id that collides with one.
- Location codes: `world`; ISO 3166-1 alpha-3 in lowercase (`usa`); ISO 3166-2 in lowercase (`us-ca`); `us-county-06037`; monitoring sites with a source prefix (`usgs-01646500`). `packages/geo` defines them for both languages.
- `/v1` is the API contract version. Data versions are separate. A breaking change means `/v2`, with `/v1` kept for 12 months.
- The only query parameters in v1 are `start` and `end` on time series. The Worker slices files under about 5 MB. Larger requests get a 400 problem response that links to the bulk file. Unknown parameters are ignored and never reach a cache key, so cache-busting query strings cost a Worker request but no R2 read.

### 3.2 Formats

- JSON is the default. CSV comes from the `.csv` extension. Bulk Parquet and `.csv.gz` live under `download/`. Formats are chosen by path extension, not the `Accept` header, so URLs are shareable and cache keys stay simple.
- JSON body: `{"meta": {...stable fields...}, "data": [{"date": "1880-01", "value": -0.19}, ...]}`. The `meta` block holds only fields that don't change between runs (dataset, series, location, unit, source name, license). Values that change on every run (version, retrieved_at, source_updated_at) go in headers. That way an unchanged upstream produces byte-identical blobs (4.4), and the Worker streams R2 bodies without parsing them.

### 3.3 Metadata and provenance

Every version document (4.6) and `/v1/{dataset}` carries:
- `source`: name, publisher, URL, `license` (an SPDX id or `LicenseRef-*`), the exact `attribution` text, and `commercial_ok` (true, false or unknown).
- `retrieved_at`: when we fetched. `source_updated_at`: when the source changed. `published_at`: when we flipped the pointer.
- `version`, `contract_version`, `transform_version` (package version plus git SHA), and a link to the GitHub run that built it.
- `raw`: the R2 key and sha256 of the archived raw input, or the request parameters plus checksum for sources too large to archive.
- `coverage` (time range and spatial extent) and the `validation` result with any warnings.

### 3.4 Caching headers, ETags, CORS

```
HTTP/2 200
content-type: application/json; charset=utf-8
cache-control: public, max-age=1800, stale-while-revalidate=86400, stale-if-error=604800
etag: "3f9a1c2e4b7d…"                        (the blob's sha256: strong, stable across re-uploads)
last-modified: Fri, 23 Oct 2026 14:26:09 GMT  (published_at)
x-data-version: 2026-10-23.3f9a1c2e4b
x-source-updated: 2026-10-10T00:00:00Z
link: <https://api.climatedata.dev/v1/global-temperature>; rel="describedby", <https://climatedata.dev/license#gistemp>; rel="license"
access-control-allow-origin: *
access-control-expose-headers: ETag, Last-Modified, X-Data-Version, X-Source-Updated, Link, RateLimit, RateLimit-Policy
```

- `max-age` for "latest" URLs: 300 s for hourly datasets, 1800 s for daily, 3600 s for monthly and slower. The ceiling is one hour, so a rollback reaches browsers within an hour. Pinned `@version` URLs get `max-age=31536000, immutable`.
- `If-None-Match` is answered with a 304 from the cached version document, without touching R2.
- CORS: `*` for GET, HEAD and OPTIONS. Preflight allows `Authorization` and `X-API-Key`. No cookies, ever.

### 3.5 Errors

Use RFC 9457 `application/problem+json` everywhere:
```json
{ "type": "https://climatedata.dev/docs/errors#unknown-location", "title": "Unknown location",
  "status": 404, "detail": "Location 'usaa' is not available for global-temperature/land-ocean.",
  "instance": "/v1/global-temperature/land-ocean/usaa.json", "request_id": "8d3f0c2a9e1b4f77-IAD",
  "hint": "GET /v1/global-temperature/locations" }
```
`request_id` is the `cf-ray` value. Statuses: 400, 401, 403, 404, 405, 429 (with `Retry-After`), 500, and 503 for a dataset that is blocked with no prior version.

### 3.6 OpenAPI per site

- `packages/contracts-ts` generates `openapi/<site>.json` (3.1) from the contracts at build time. The Worker bundles it and serves it at `/v1/openapi.json`. The paths are generic. Series are enums. Locations are documented through the `/locations` endpoint rather than a huge enum.
- CI lints the specs (Redocly CLI). A weekly Schemathesis run against staging checks that responses match the spec.
- The docs site renders the spec with Starlight plus `starlight-openapi`. Every dataset page shows curl, Python (polars/duckdb), JS and R snippets generated from the same contract.

### 3.7 Inside the Worker, and why Workers Cache stays off

Request path: resolve site from Host, run the auth hook (anonymous at launch), check the rate limit, then resolve the data. Resolution goes manifest (isolate memory 30 s, Cache API 60 s, then R2), then version document (immutable, cached), then blob hash, then the body from the Cache API or R2. The Worker adds headers, streams the body, and queues metering in `ctx.waitUntil`.

Workers Cache launched in July 2026 and puts a tiered cache in front of a Worker. It is a poor fit here for now, for four reasons:
- Its cache key is path, entrypoint, `ctx.props` and Worker version, **not hostname**. With ten sites on one Worker sharing `/v1/...` paths, a response cached through one site would be served on the others.
- Requests carrying an `Authorization` header bypass it automatically. That covers exactly the keyed traffic you will add later.
- Cache hits are still billed as requests. Hits only save CPU, and a static-first API spends very little CPU anyway.
- It makes static asset requests billable on any Worker where it is enabled.

The Cache API has none of these problems. It is per data center with no tiering, but every entry is an immutable blob, so each location misses at most once per blob. Revisit Workers Cache later only for CPU-heavy endpoints, such as on-the-fly slicing or chart rendering. In that case, use a gateway entrypoint (uncached: auth, metering) that calls a cached entrypoint with the site in the path or in `ctx.props`.

### 3.8 Hooks for keys and paid tiers (built now, switched on later)

- The middleware chain exists from day one: `resolveSite → authenticate → rateLimit(plan) → handler → meter`. At launch `authenticate` returns `{plan: "anonymous"}`, so adding keys is configuration, not a rewrite.
- Keys look like `dk_live_<32 random base32 chars>` and arrive in `Authorization: Bearer` or `X-API-Key`. KV stores only a SHA-256 of the key, mapped to `{account, plan, status}`. D1 is the source of truth for accounts, keys and plans. Cache key lookups in isolate memory or the Cache API for about 5 minutes so you don't pay a KV read on every request.
- Burst limits use the Workers rate-limit binding, with one binding per plan. Windows can only be 10 or 60 seconds, counts are per Cloudflare location and approximate, and it suits key-based limiting. For anonymous traffic, key on IP and add the Free plan's one WAF rate-limiting rule (IP, 10 s) as a backstop.
- Exact monthly quotas and billing counts: one Durable Object per account, incremented in `waitUntil`, with an "over quota" flag cached in the Worker. Analytics Engine handles dashboards: 10M data points/month included, not currently billed. Don't bill from it, because it samples at high volume.
- Billing: a nightly cron sends each account's daily usage to Stripe Billing Meters. Stripe webhooks hit a `/billing/webhook` route on the same Worker. A marketplace such as RapidAPI becomes one more auth provider, validating its proxy-secret header and mapping to a `marketplace` plan.

---

## 4. Data update mechanism

### 4.1 One scheduler per workload type

| Workload | Examples | Scheduler | Why |
|---|---|---|---|
| Hourly, small API payloads | OpenAQ v3 "latest" | **Cloudflare Workflows with `schedules`** (TS, `apps/ingest-hot`) | Runs on time with per-step retries, uses no GitHub minutes, and writes to R2 through a binding. Cron schedules on Workflows shipped 2 June 2026. |
| Hourly, bulk files | NASA FIRMS active-fire CSVs | GitHub Actions cron at an odd minute (`:23`), Python | The CSVs are too large to parse comfortably in a 128 MB isolate. NRT fire data arrives hours after overpass anyway, so GitHub's scheduling delay doesn't matter. |
| Daily, weekly, monthly API pulls | BLS, BEA, World Bank, Treasury, NOAA GML, GISTEMP, HadCRUT, USGS daily values | **GitHub Actions cron**, Python | Python libraries are there, public-repo minutes are free, jobs get 6 hours and a 4 vCPU / 16 GB runner |
| Heavy monthly or annual | ERA5 country aggregates, satellite-derived statistics | GitHub Actions, year-chunked matrix for backfills | Fits in 16 GB RAM and 14 GB SSD |
| Irregular releases | IMF WEO (April/October), certified election results | `workflow_dispatch`, plus a temporary cron during release windows | Release dates move |
| Freshness monitor | all datasets | `scheduled()` cron on the API Worker, every 15 min | Independent of GitHub |
| Escalation only | jobs over 6 h, over 14 GB disk or over 16 GB RAM | small VPS registered as a **self-hosted GitHub runner** | Keeps one orchestration system. GitHub postponed its planned self-hosted runner fee in Dec 2025. |

Hourly Workflow config, in the same file as the Worker:
```jsonc
"workflows": [
  { "name": "air-openaq-latest", "binding": "OPENAQ_LATEST", "class_name": "OpenAQLatest",
    "schedules": ["7 * * * *"] }
]
```
A Workflow step's return value is capped at 1 MiB. The fetch step therefore writes raw data to R2 and returns only the key and hash.

If you would rather run a single language, the fallback is to run the hourly feeds on GitHub Actions at `:07` and `:37` and accept the occasional gap. The rest of the design stays the same.

### 4.2 The dataset contract (one YAML per dataset)

```yaml
# contracts/sites/climatedata/datasets/global-temperature.yaml
id: global-temperature
version: 3                       # bump on schema change; forces a rebuild
title: Global surface temperature anomaly
check_every: "23 14 * * *"       # how often we look
cadence: P1M                     # how often upstream should change
sla: P40D                        # "stale-upstream" if no source change in this window
source:
  name: NASA GISS Surface Temperature Analysis v4
  url: https://data.giss.nasa.gov/gistemp/
  license: LicenseRef-US-Government-Work
  commercial_ok: true
  attribution: "GISTEMP Team, 2026: GISS Surface Temperature Analysis (GISTEMP), version 4. NASA GISS."
schema:
  - { name: date, type: month }
  - { name: value, type: float, unit: degC, baseline: "1951-1980" }
checks:
  rows_min_ratio: 0.995          # vs previous version (block)
  ranges: { value: [-3.0, 3.0] } # physical bounds (block)
  max_null_fraction: { value: 0.01 }
  max_date_not_older_than_previous: true
  revisions: { max_changed_fraction: 0.05, tolerance: 0.03, severity: warn }
outputs:
  series: [land-ocean, land, ocean]
  locations: [world, nh, sh]
  formats: [json, csv]
  bulk: [parquet]
```

### 4.3 Pipeline stages and idempotency

`fetch → archive raw → change check → transform → validate → upload blobs → write version doc → flip manifest → update catalog and state`

- **Deterministic outputs.** Rows are sorted, floats use fixed formatting, line endings are LF, keys are sorted, and data files contain no wall-clock timestamps. The same input plus the same code produces byte-identical files and the same hashes.
- **Content-addressed blobs** at `blobs/sha256/<hash>`. Uploading an existing blob changes nothing, so retries are free. Unchanged files are never rewritten, which keeps R2 Class A operations down.
- **Version id** is `{YYYY-MM-DD}.{hash10}`, where the hash covers the sorted file map. A re-run on the same day produces the same id.
- **The version document is the commit marker.** It is written after all blobs. A crash before that point leaves orphan blobs, which the janitor sweeps.
- **The pointer flip is a conditional PUT.** It sends `If-Match` with the manifest ETag read at the start, or `If-None-Match: *` for the first publish. R2 evaluates conditional PutObject atomically and is strongly consistent, so two overlapping runs cannot both flip, and readers see the new pointer immediately.
- **Overlap guards.** GitHub uses a `concurrency` group per dataset with `cancel-in-progress: false`. Workflows start one instance per schedule tick, and the conditional flip covers any remaining race.
- **State file** per dataset at `ops/state/<site>/<dataset>.json`. It holds the fingerprint, upstream ETag and Last-Modified, `checked_at`, `last_success`, `consecutive_failures` and `last_error`. Every run writes it, including no-op runs.

Publish core (Python, simplified):
```python
def publish(ds, outputs, fingerprint):
    cur, etag = r2.get_json_with_etag(ds.manifest_key)            # None, None on first run
    files = {path: r2.put_blob(body) for path, body in outputs}   # skip if blob exists
    vid = f"{today_utc()}.{tree_hash(files)[:10]}"
    if cur and cur["current"] == vid:
        return state.touch(ds, checked=True)                       # nothing changed
    report = validate(ds, outputs, previous=cur)
    r2.put_json(ds.version_key(vid), version_doc(ds, vid, files, report))  # commit marker
    if report.blocking:
        return quarantine(ds, vid, report)                         # alert; pointer untouched
    new = advance(cur, vid, keep=10, snapshot_monthly=True)
    r2.put_json(ds.manifest_key, new, if_match=etag, if_none_match="*" if etag is None else None)
    rebuild_catalog(ds.site); state.record(ds, published=vid, fingerprint=fingerprint)
```

### 4.4 Change detection (stop as early as possible)

1. **Upstream signals first.** Use release calendars (BLS publishes one), `last_updated` fields (World Bank, STAC `updated`, BEA table metadata), and conditional GETs with the stored `ETag` / `Last-Modified`. A 304 means record `checked_at` and stop.
2. **Raw hash.** Take the sha256 of the normalized raw payload, with volatile fields such as "generated at" stripped. Same hash, stop.
3. **Fingerprint includes code.** `fingerprint = sha256(raw_hash + contract.version + transform_version)`. A bug fix or schema change rebuilds even when the source didn't change. Without this, fixes never get published.
4. **Output hash.** If the new version id equals the current one, don't publish. This catches upstreams that re-issue identical data with a new timestamp.

Each layer cuts load on the source and keeps R2 writes down. A no-change run writes only the state file.

### 4.5 Validation gates

| Check | Default | Severity |
|---|---|---|
| Schema: columns, types, units, non-null keys (pandera on polars; ajv for hot feeds) | exact | block |
| Row count vs previous version | not below 99.5% (append-only series) | block |
| Coverage: newest date not older than previous version's; no new internal gaps | exact | block |
| Physical ranges (PM2.5 0 to 2,000 µg/m³, CO2 250 to 600 ppm, anomaly ±3 °C, unemployment 0 to 50%) | per contract | block |
| Null fraction per column | per contract | block |
| Revisions of historical values | over 5% of rows changed by more than tolerance | warn (BEA/BLS revise legitimately) |
| Jump detection on the newest point | more than 6 robust z-scores from the trailing 24 points | warn |
| Location set change | any location disappeared | warn, or block for country-level datasets |
| File size vs previous | outside 0.5x to 3x | warn |

A blocked version is written but not promoted. The quarantine alert links to the run summary, which includes a diff table, and the version can be inspected at `/v1/{dataset}@{version}` on staging. A `promote` dispatch flips the pointer after you review it. Backfills run the same checks with a `backfill` profile that allows row counts to grow.

### 4.6 Storage layout, atomic publish, snapshots, rollback

```
datasites-data/
  blobs/sha256/3f/3f9a1c2e…                         immutable file bodies
  sites/climatedata/
    catalog.json                                     rebuilt on every publish to this site
    datasets/global-temperature/
      manifest.json                                  the only mutable object per dataset
      versions/2026-10-23.3f9a1c2e4b.json            immutable: provenance, validation, file → blob map
  raw/nasa-gistemp/2026-10-23T142140Z.3b0c….csv      raw archive, deduped by hash
  ops/state/climatedata/global-temperature.json      pipeline state
  ops/status/climatedata.json                        written by the freshness cron
  staging/<run_id>/…                                 backfill scratch; lifecycle rule deletes after 7 days
```

`manifest.json`:
```json
{
  "site": "climatedata",
  "dataset": "global-temperature",
  "current": "2026-10-23.3f9a1c2e4b",
  "published_at": "2026-10-23T14:26:09Z",
  "previous": ["2026-10-09.a81d44f0c2", "2026-09-12.77c0e1b2d9"],
  "snapshots": { "2026-09": "2026-09-12.77c0e1b2d9", "2026-08": "2026-08-11.0be41f77aa" },
  "quarantined": [],
  "fingerprint": "sha256:5be1…",
  "rev": 41
}
```

Version document (trimmed):
```json
{
  "version": "2026-10-23.3f9a1c2e4b",
  "contract_version": 3,
  "transform_version": "datasites-climate 0.4.2 (9c1e2ab)",
  "source": { "name": "NASA GISS GISTEMP v4", "url": "https://data.giss.nasa.gov/gistemp/",
              "license": "LicenseRef-US-Government-Work", "attribution": "GISTEMP Team, NASA GISS",
              "retrieved_at": "2026-10-23T14:21:40Z", "source_updated_at": "2026-10-10T00:00:00Z",
              "raw": { "key": "raw/nasa-gistemp/2026-10-23T142140Z.3b0c.csv", "sha256": "3b0c…" } },
  "coverage": { "start": "1880-01", "end": "2026-09" },
  "validation": { "status": "pass", "warnings": ["3 historical months revised by more than 0.03 C"] },
  "run": "https://github.com/<you>/datasites/actions/runs/123456789",
  "files": {
    "land-ocean/world.json": { "blob": "3f9a1c…", "bytes": 48213, "rows": 1761, "type": "application/json" },
    "land-ocean/world.csv":  { "blob": "a03e9d…", "bytes": 21877, "rows": 1761, "type": "text/csv" },
    "download/global-temperature.parquet": { "blob": "c4410b…", "bytes": 90112, "type": "application/vnd.apache.parquet" }
  }
}
```
For datasets with tens of thousands of files, such as county-level elections, shard the file map per series once it passes about 1 MB.

- **Rollback** is one conditional PUT that points `current` back at a version in `previous`, followed by a purge of that dataset's manifest tag. It takes under a minute through `data-rollback.yml`.
- **Snapshots** are free: the first version of each month is recorded in `snapshots` and its blobs are kept forever. Users can cite `@2026-09-12.77c0e1b2d9`, which matters to researchers.
- **Retention.** The janitor keeps blobs referenced by current, the last 10 previous versions, snapshots, and anything from the last 90 days. DeleteObject is free in R2.
- **Raw archive.** Keep every raw payload under about 100 MB forever, deduplicated by hash, in Standard storage. Infrequent Access has no free tier, a 30-day minimum and retrieval fees. For ERA5 and imagery, record the request parameters and checksums instead. Copy `raw/` monthly to a second provider (for example Backblaze B2). It is the one dataset you cannot regenerate if a federal source disappears.

### 4.7 Cache purge and backfills

- **Routine publishes need no purge.** Blobs and version documents are immutable. The manifest is cached for at most 60 s at the edge and 30 s in isolate memory. Browser `max-age` on "latest" URLs is capped at 1 hour.
- **Emergencies.** Manifest entries in the Cache API carry `Cache-Tag: m-<site>-<dataset>`. The rollback workflow purges that tag on the site's zone. Purge by tag, prefix and hostname has been available on every plan, including Free, since April 2025. Stale blob entries can stay, because nothing references them after the flip.
- **Backfills.** `backfill.yml` takes `dataset`, `from` and `to`. A matrix of up to 256 jobs, chunked by year, writes partial Parquet to `staging/<run_id>/`. One assemble job then builds a normal version, validates it and publishes it. Use `datasites rebuild <dataset> --from-raw` to re-transform from the raw archive without calling the upstream. Backfills never bypass validation or the publish path.

### 4.8 Monitoring and alerting

There are three independent layers.
1. **Dead-man switches.** Each GitHub workflow and each Workflow pings healthchecks.io at start and with its exit status. That catches failures as well as GitHub silently not running a job: scheduled runs can be delayed or dropped at the top of the hour, and public-repo schedules auto-disable after 60 days without activity. The free plan has 20 checks, so use one per workflow file, not one per dataset.
2. **Freshness cron.** Every 15 minutes the API Worker reads `ops/state/*` and writes `ops/status/<site>.json`. It pings one "freshness" check: success if every dataset is fine, `/fail` with the list of problems otherwise.
3. **HTTP check.** A free external uptime monitor polls `https://api.<site>/v1/status` for each launched site. CI also smoke-tests after every deploy.

Status values: `ok`; `late` (no successful check within twice the check interval, our problem); `stale-upstream` (we check fine, but the source hasn't changed within the SLA, their problem, alert once a day); `failing` (3 or more consecutive failures); `blocked` (validation held back the newest version, previous still served); `discontinued` (upstream retired, final version still served).

```json
{ "site": "climatedata", "generated_at": "2026-10-23T15:00:02Z", "overall": "degraded",
  "datasets": [
    { "id": "global-temperature", "status": "ok", "version": "2026-10-23.3f9a1c2e4b",
      "checked_at": "2026-10-23T14:23:10Z", "source_updated_at": "2026-10-10T00:00:00Z", "sla": "P40D" },
    { "id": "co2-mauna-loa", "status": "failing", "consecutive_failures": 3,
      "last_error": "HTTP 503 from gml.noaa.gov", "serving": "2026-10-06.91aa02c7d1" } ] }
```
Alerts go from healthchecks.io to email and phone push (ntfy or Pushover). The site's `/status` page renders the same JSON. Also keep Cloudflare's budget alert: eligible pay-as-you-go accounts get a default $10 threshold. It is evaluated daily and is informational only.

### 4.9 Secrets

| Secret | Used by | Stored in | Notes |
|---|---|---|---|
| R2 keys (prod / staging) | Python pipelines | GitHub Environments `production` / `staging` | Bucket-scoped Object Read & Write. Environments restricted to `main`, no required reviewers (reviewers would block scheduled runs). |
| `FRED_API_KEY`, `BLS_API_KEY`, `BEA_API_KEY`, `EIA_API_KEY`, `NOAA_CDO_TOKEN`, Copernicus CDS token, `FIRMS_MAP_KEY` | Python | GitHub Environment | One key per source. Rotate yearly. |
| `EARTHDATA_USERNAME` / `PASSWORD` | Python (`earthaccess`) | GitHub Environment | Earthdata user tokens expire after 60 days and you can hold at most 2. Let earthaccess fetch or create one at runtime rather than storing a token. |
| `OPENAQ_API_KEY` | `ingest-hot` Workflows | `wrangler secret put` | A key is needed for the full rate limit |
| `CLOUDFLARE_API_TOKEN`, purge token | deploy / rollback | Repo secret / production env | Narrow scopes (2.5) |
| healthchecks ping URLs | Actions, Workers | Repo secrets, wrangler secrets | Anyone holding the URL can ping it, so treat it as a secret |
| Stripe keys (later) | API Worker | wrangler secret | Webhook signing secret too |

Local development uses `pipelines/.env` and `apps/*/.dev.vars`, both gitignored. In the public repo: turn on secret-scanning push protection (free for public repos), never use `pull_request_target` with checkout of PR code, and require approval before workflows run for first-time contributors. Fork PRs never receive secrets.

### 4.10 GitHub Actions cron skeleton

```yaml
# .github/workflows/ingest-economic.yml
name: ingest-economic
on:
  schedule:
    - cron: "47 8 * * 1-5"            # 08:47 New York, after the 08:30 BLS/BEA releases
      timezone: "America/New_York"    # IANA timezones are now supported; drop this line to use UTC
    - cron: "23 3 * * *"              # nightly sweep (UTC)
  workflow_dispatch:
    inputs:
      dataset: { description: "dataset id or all", default: "all" }
      mode:    { type: choice, options: [incremental, backfill], default: incremental }
      target:  { type: choice, options: [production, staging], default: production }

permissions: { contents: read }

concurrency:
  group: ingest-economic-${{ inputs.dataset || 'all' }}
  cancel-in-progress: false

jobs:
  ingest:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    environment: ${{ inputs.target || 'production' }}
    strategy:
      fail-fast: false
      max-parallel: 4
      matrix:
        dataset: [bls-cpi, bls-unemployment, bls-payrolls, bea-gdp, worldbank-wdi, treasury-yields]
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with: { enable-cache: true }
      - name: Run pipeline
        if: ${{ !inputs.dataset || inputs.dataset == 'all' || inputs.dataset == matrix.dataset }}
        working-directory: pipelines
        env:
          R2_ENDPOINT: ${{ vars.R2_ENDPOINT }}
          R2_BUCKET: ${{ vars.R2_BUCKET }}
          R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
          R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
          BLS_API_KEY: ${{ secrets.BLS_API_KEY }}
          BEA_API_KEY: ${{ secrets.BEA_API_KEY }}
        run: >
          uv run --frozen --package datasites-economic
          datasites run economic/${{ matrix.dataset }}
          --mode ${{ inputs.mode || 'incremental' }} --summary "$GITHUB_STEP_SUMMARY"

  report:
    needs: ingest
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS -m 10 --retry 3 "${{ secrets.HC_INGEST_ECONOMIC }}/${{ needs.ingest.result == 'success' && '0' || '1' }}"
```
Schedule at odd minutes. GitHub says load peaks at the top of the hour, and queued scheduled jobs can be dropped then.

---

## 5. Limits and costs (verified Oct 2026)

### 5.1 Cloudflare

| Product | Free | Paid ($5/month account minimum) |
|---|---|---|
| Workers requests | 100,000/day (then Error 1027) | 10M/month included, then $0.30 per million |
| Workers CPU | 10 ms per invocation | 30M CPU-ms/month included, then $0.02 per million ms. Up to 5 min per request (default 30 s). |
| Cron Trigger CPU | 10 ms | 30 s if the interval is under 1 h; 15 min if 1 h or more |
| Cron Triggers per account | 5 | 250 |
| Workers per account / memory | 100 / 128 MB | 500 / 128 MB |
| Subrequests per invocation | 50 | 10,000 |
| Static assets | Free, unlimited requests; 20,000 files per version; 25 MiB per file | Free, unlimited requests; 100,000 files per version; 25 MiB per file |
| Workflows | 3,000 steps/day, 10 ms CPU per step, 100 concurrent instances | 500k steps/month (then $0.80 per 100k), 30 s CPU per step (up to 5 min), 1 GB-month storage (then $0.20/GB), 50,000 concurrent. Step-billing start date is set in the Workflows changelog. |
| R2 storage | 10 GB-month free (Standard only) | $0.015/GB-month Standard; $0.01 IA (30-day minimum, $0.01/GB retrieval) |
| R2 Class A (writes, lists, copies) | 1M/month free | $4.50 per million |
| R2 Class B (reads, HEAD) | 10M/month free | $0.36 per million |
| R2 egress, deletes | Free | Free |
| D1 | 5M rows read/day, 100k written/day, 5 GB total, 500 MB per database | 25B reads and 50M writes/month included, 5 GB included then $0.75/GB-month, 10 GB per database |
| KV | 100k reads/day, 1k writes/day, 1 GB | 10M reads/month (then $0.50/M), 1M writes (then $5/M), 1 GB |
| Analytics Engine | 100k points/day | 10M points/month included, then $0.25/M (billing not active yet) |
| Rate-limit binding | 10 s or 60 s windows, per location, approximate | same |
| Zone rules (Free) | 10 Single Redirects per zone; 1 WAF rate-limiting rule (IP, 10 s); purge by tag, prefix, host or URL | n/a |

### 5.2 GitHub Actions

| Item | Public repo | Private repo |
|---|---|---|
| Standard hosted runner minutes | Free, unlimited | 2,000/month (Free plan), 3,000 (Pro), then $0.006/min for Linux 2-core |
| `ubuntu-latest` hardware | 4 vCPU, 16 GB RAM, 14 GB SSD | 2 vCPU, 8 GB RAM, 14 GB SSD |
| Job and run limits | 6 h per job, 35 days per run, 256 matrix jobs, 20 concurrent jobs (Free) | same |
| Schedule behavior | Minimum every 5 min; default branch only; may be delayed or dropped under load; IANA timezone optional | same, except the 60-day auto-disable applies to public repos only |
| Cache | 10 GB per repo | 10 GB per repo |
| 2026 pricing changes | Hosted-runner prices cut by up to 39% on 1 Jan 2026. The planned $0.002/min self-hosted fee was postponed in Dec 2025. | same |

This is why the repo should be public. Private, the daily pipelines plus PR CI would sit near 2,000 minutes, and one hourly GitHub job (about 1,500 minutes a month) would push you past the free allowance.

### 5.3 Monthly cost estimates

| Line | Launch (2 sites, under 1M requests) | Moderate (10 sites, 30M requests, 3 ms average CPU, 40 GB, 2.5M Class A, 15M Class B) | Unthrottled spike (300M requests) |
|---|---|---|---|
| Workers Paid base | $5.00 | $5.00 | $5.00 |
| Extra requests | $0 | 20M × $0.30 = $6.00 | 290M × $0.30 = $87.00 |
| Extra CPU | $0 | 60M ms × $0.02 = $1.20 | about $17 |
| R2 storage | $0 | 30 GB × $0.015 = $0.45 | $0.45 |
| R2 Class A | $0 | 1.5M × $4.50 = $6.75 | $6.75 |
| R2 Class B | $0 | 5M × $0.36 = $1.80 | about $5 |
| Workflows, D1, KV, Analytics Engine, Actions, healthchecks, Email Routing | $0 | $0 (inside included amounts if key lookups are cached) | $0 to $10 |
| **Total infrastructure** | **about $5** | **about $21** | **about $120 or more** |

Domain renewals are separate: roughly $10 to $15 a month amortized for nine .dev and one .net. Moving to Cloudflare Registrar at wholesale price after 60 days cuts that.

### 5.4 Cost traps to design out now

- **Per-location files on hourly feeds.** 10,000 station files × 720 runs is 7.2M Class A operations, about $28/month. Publish hourly snapshots bundled per country, and compact history per location nightly.
- **Workers Cache** on any Worker that serves static assets makes those requests billable.
- **A KV read on every keyed request.** Cache key lookups in the isolate or the Cache API.
- **R2 Infrequent Access** for hot or small objects: no free tier, 30-day minimum, retrieval fees.
- **A private repo with hourly GitHub jobs.**
- **Anonymous unlimited access.** Requests are the only unbounded meter. Rate limits, the WAF rule and the budget alert are what stop the right-hand column of 5.3.

---

## 6. Phased rollout

**Coming-soon pages: all nine .dev domains, from day one,** served by one `coming-soon` Worker. It is static HTML with per-host copy, a Turnstile-protected email form, a D1 `signups` table, and a privacy page. climatedata and economicdata switch to real sites at the end of Phase 1. **electiondata.dev comes first: get it live before the 3 November 2026 midterms**, when interest peaks. visualizedata.dev's page should explain that it will be the chart and embed layer for the whole family. votingdata.net gets the 302 redirect, not a placeholder page. To move a domain from coming-soon to its site, remove it from the coming-soon config and deploy, then deploy the site Worker. Wrangler won't silently take over a domain attached to another Worker.

| Phase | Window | Milestones | Definition of done |
|---|---|---|---|
| **0: Shared infrastructure** | 24 Oct to 8 Nov 2026 | **0.1 (by 27 Oct):** 10 zones Active, Universal SSL issued. **0.2 (by 30 Oct):** coming-soon live on 9 apexes, votingdata.net redirect, Email Routing, DMARC. **0.3 (by 8 Nov):** monorepo, CI with previews, deploy on main, Workers Paid, R2 prod and staging buckets, API skeleton on `api.climatedata.dev` and `api.economicdata.dev` (middleware chain, problem+json, CORS, `/v1/status`, `/v1/catalog`), `datasites_core` (fetch, hash, validate, publish), one dataset (NOAA GML CO2 monthly) published end to end, healthchecks wired, budget alert set | Every domain loads over HTTPS. A signup lands in D1. A PR shows preview URLs and merging deploys. `curl https://api.climatedata.dev/v1/status` returns one `ok` dataset. A deliberately failed run alerts your phone within 15 minutes. |
| **1: MVP, climatedata + economicdata** | 9 Nov to 20 Dec 2026, then a 30-day soak to 20 Jan 2027 | **climatedata** (5): NASA GISTEMP v4 (global, hemispheric, zonal); NOAA GML CO2 and CH4; Met Office HadCRUT5; NOAA NCEI Climate at a Glance (US national and state); ERA5 country-level monthly temperature and precipitation, 1940 to present. **economicdata** (6): BLS CPI-U; BLS unemployment (CPS + state LAUS); BLS CES payrolls; BEA GDP (national + state); World Bank WDI core indicators; US Treasury daily par yields. Week 2: 3 datasets on staging. Week 4: sites with catalog, dataset pages, charts, docs and API reference. Week 6: production launch and coming-soon swap. | 11 datasets live with JSON/CSV per series and location plus Parquet bulk files. OpenAPI passes lint and Schemathesis. Every dataset has license, attribution and provenance, and CI fails without them. A no-change run writes zero blobs. An injected bad input is blocked and alerts. A rollback drill finishes in under 5 minutes. A backfill rebuilds from the raw archive. 30 days with no manual fixes and at least 99% of freshness checks `ok`. Bill at or under $6. |
| **2: airdata + wildfiredata** | Jan to Feb 2027 | Workflows hot path (OpenAQ v3 latest, hourly), nightly Python compaction into per-location history, FIRMS active fires aggregated by country and day (GitHub hourly), US incident data. Per-provider license filter for OpenAQ. | p95 age of "latest" air data under 90 minutes over 2 weeks. Hourly Class A below 150k/month per feed. Status page shows hourly datasets. Ready before the northern-hemisphere fire season. |
| **3: electiondata + carbondata, free API keys** | Mar to Apr 2027 | Elections: MEDSL county-level presidential and congressional history; 2026 certified results as states and MEDSL/OpenElections release them, each row labeled `certified` or `unofficial`. No election-night live results. Carbon: Global Carbon Budget, OWID CO2, EDGAR, EPA GHGRP facility data with a program-status banner. Free key signup (D1 accounts, hashed keys in KV, per-plan rate-limit bindings). | Every election row traceable to a source document. Keys optional, with higher limits for keyed clients. Usage dashboard per key from Analytics Engine. |
| **4: waterdata + satellitedata** | May to Jun 2027 | Water: build only on the new USGS Water Data APIs (legacy WaterServices shuts down 22 Feb 2027). Satellites: CelesTrak SATCAT and OMM in JSON/CSV with catalog numbers wider than 5 digits; a cached, rate-limited, normalized STAC search proxy over Earth Search, Planetary Computer and NASA CMR-STAC. No imagery hosting. | STAC proxy p95 under 800 ms on cache hits. CelesTrak polled no more than once per hour per group. |
| **5: visualizedata + paid tiers** | H2 2027, when a trigger hits | Embeddable charts and SVG chart endpoints for every dataset in the family. Paid plans through Stripe Billing Meters; marketplace listing optional. Triggers: over 5M requests/month, 3 or more inbound commercial requests, or one key above 30% of traffic. | Checkout to working key in under 2 minutes. Metered usage reconciles with Durable Object counts within 1%. Paid tiers include only datasets with `commercial_ok: true`. |
| votingdata.net decision | mid-2027 | Launch roll-call votes (House Clerk and Senate XML, congress.gov API, Voteview) or keep the 302 | Decide and, if launching, switch to its own site Worker |

---

## 7. Risks

| Risk | Concrete cases | Mitigation |
|---|---|---|
| **Upstream dataset disappears** | EPA removed EJScreen on 5 Feb 2025. NOAA retired the Billion-Dollar Disasters database in May 2025 (no updates after 2024). EPA proposed in Sept 2025 to end GHGRP reporting for 46 source categories; still pending, with deadlines pushed. | Permanent raw archive plus an off-Cloudflare copy. A `discontinued` status keeps serving the final version. Contracts list fallback sources (GISTEMP, HadCRUT5, ERA5; BLS directly rather than through FRED). Track community mirrors such as Public Environmental Data Partners. The weekly upstream canary opens an issue. |
| **Upstream API or format change** | USGS WaterServices is degraded from Nov 2026, has outages in Jan 2027 and is decommissioned 22 Feb 2027. CelesTrak's 5-digit catalog numbers ran out on 11 Jul 2026, which breaks the TLE format. OpenAQ now requires keys (v3). | Contract validation blocks the publish and the old version keeps serving. Build waterdata on the new USGS APIs only. Use OMM/CSV, and store NORAD ids as integers wider than 5 digits. Recorded fixtures make format drift show up as failing tests. |
| **Licensing and attribution** | FRED: third-party copyrighted series need the owner's permission; a mandatory "uses the FRED® API but is not endorsed" notice; no "FRED" in hostnames; no replicating FRED's core experience. Berkeley Earth is CC BY-NC 4.0, so commercial use needs a license. Copernicus requires its attribution text. OpenAQ licenses vary by provider. CelesTrak data comes from Space-Track, whose user agreement limits redistribution. | Mandatory `license`, `attribution` and `commercial_ok` in every contract, enforced by CI. Prefer primary public-domain sources (BLS, BEA, Census) over FRED. Attribution goes in headers, metadata and the site's license page. Paid tiers exclude anything not `commercial_ok`. Get a short legal review before Phase 5. |
| **Upstream rate limits** | FIRMS: 5,000 transactions per 10 minutes per MAP_KEY. BLS v2: 500 queries/day, 50 series per query, 20 years per query, 50 requests per 10 s. CelesTrak: no more than once per hour. FRED: limits unstated and adjustable. Earthdata tokens: 60-day life. | Change detection and conditional GETs remove most calls. A per-source token bucket and backoff on 429/5xx live in `datasites_core`. Send a descriptive User-Agent with a contact email. Batch BLS series 50 per query. Mint Earthdata tokens at runtime. |
| **Cost blowup** | An unthrottled 300M-request month costs about $120. Per-station hourly files cost about $28 a month in Class A. Workers Cache makes assets billable. A private repo burns Actions minutes. | Anonymous rate limits plus the WAF backstop. Push heavy users toward free keys and bulk Parquet. Bundle hourly outputs. Keep the budget alert. Watch the dashboard after any launch announcement. |
| **Abuse** | Scrapers walking every series, cache-busting query strings, hotlinked bulk files, signup spam | Bulk downloads make scraping pointless. Unknown query parameters are ignored, so a cache-busting request costs one Worker request and no R2 read. Per-key and per-IP limits. Turnstile on signup, with double opt-in when you first email the list. WAF custom rules to block abusive ASNs. |
| **GitHub scheduler unreliability** | Delayed or dropped runs at the top of the hour; public-repo schedules disabled after 60 days of inactivity | Odd-minute crons, a nightly sweep cron, healthchecks dead-man switches, and hourly feeds on Workflows |
| **Election-data sensitivity** | Misreads of unofficial or partial results | Certified results only, with explicit status, source document links and timestamps. No live election-night feed. |
| **Solo operator** | Vacations and illness | Everything as code, runbooks in `ops/`, quarterly rollback and restore drills, alerts that tell you what to do |
| **Cloudflare lock-in** | Account or billing problems | R2 speaks S3, pipelines are plain Python, and the raw archive is backed up elsewhere. The API Worker is the only Cloudflare-specific code. |

---

## What I could not confirm

- Whether Workflows `schedules` count toward the 250 Cron Trigger limit, and which timezone they run in. The changelog doesn't say.
- The exact YAML shape of GitHub's new `timezone` key. GitHub's docs confirm IANA timezone support. The `timezone:` key next to `cron:` comes from third-party guides.
- Whether DirectNIC accepts DS records for .dev/.net (needed only for DNSSEC), and whether it offers bulk nameserver edits.
- When Workflows step and storage billing begins. The pricing page points to a changelog date.

## Sources

- Cloudflare Workers pricing (updated 2 Oct 2026): https://developers.cloudflare.com/workers/platform/pricing/
- Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- R2 pricing: https://developers.cloudflare.com/r2/pricing/
- R2 S3 API compatibility (conditional PutObject): https://developers.cloudflare.com/r2/api/s3/api/
- R2 consistency: https://developers.cloudflare.com/r2/reference/consistency/
- D1 limits: https://developers.cloudflare.com/d1/platform/limits/
- Workflows limits: https://developers.cloudflare.com/workflows/reference/limits/
- Trigger Workflows: https://developers.cloudflare.com/workflows/build/trigger-workflows/
- Cron schedules for Workflows (2 Jun 2026): https://developers.cloudflare.com/changelog/post/2026-06-02-cron-workflows/
- Workers Cache docs: https://developers.cloudflare.com/workers/cache/ and launch post: https://blog.cloudflare.com/workers-cache/
- Cache API: https://developers.cloudflare.com/workers/runtime-apis/cache/
- Purge for all plans (Apr 2025): https://developers.cloudflare.com/changelog/post/2025-04-01-purge-for-all/
- Custom domains: https://developers.cloudflare.com/workers/configuration/routing/custom-domains/
- Version/preview URLs: https://developers.cloudflare.com/workers/configuration/previews/
- Rate-limit binding: https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/
- Analytics Engine pricing: https://developers.cloudflare.com/analytics/analytics-engine/pricing/
- Pages overview ("Start new projects with Workers"): https://developers.cloudflare.com/pages/
- Migrate from Pages: https://developers.cloudflare.com/workers/static-assets/migrate-from-pages/
- Astro Cloudflare adapter: https://docs.astro.build/en/guides/integrations-guide/cloudflare/
- Cloudflare acquires Astro (16 Jan 2026): https://www.cloudflare.com/press/press-releases/2026/cloudflare-acquires-astro-to-accelerate-the-future-of-high-performance-web-development/
- Redirect availability: https://developers.cloudflare.com/rules/url-forwarding/
- WAF rate-limiting rules: https://developers.cloudflare.com/waf/rate-limiting-rules/
- Full DNS setup: https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/
- Zone status (28-day deletion): https://developers.cloudflare.com/dns/zone-setups/reference/domain-status/
- Universal SSL: https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/
- Budget alerts: https://developers.cloudflare.com/billing/manage/budget-alerts/
- Cloudflare Registrar .dev: https://www.cloudflare.com/pg-registrar-domain-registration-dev/
- DirectNIC nameservers: https://directnic.com/knowledge/article/33
- GitHub Actions limits: https://docs.github.com/en/actions/reference/limits
- GitHub Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- GitHub hosted runners: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- GitHub schedule event: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- GitHub Actions pricing update / self-hosted postponement: https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/ and https://resources.github.com/actions/2026-pricing-changes-for-github-actions/
- healthchecks.io pricing: https://healthchecks.io/pricing/
- FRED API terms: https://fred.stlouisfed.org/docs/api/terms_of_use.html
- BLS API FAQ: https://www.bls.gov/developers/api_faqs.htm
- NASA FIRMS limits (2025 Q&A): https://appliedsciences.nasa.gov/sites/default/files/2025-05/Part%203%20FIRMS%202025%20Q&A%20Session.docx.pdf
- Earthdata user tokens: https://sit.urs.earthdata.nasa.gov/documentation/for_users/user_token
- OpenAQ API keys: https://docs.openaq.org/using-the-api/api-key
- CelesTrak usage policy: https://celestrak.org/usage-policy.php
- USGS WaterServices decommission: https://waterdata.usgs.gov/blog/api-waterservices-decom/
- NOAA Billion-Dollar Disasters retirement: https://www.smartcitiesdive.com/news/noaa-stops-tracking-climate-disasters-billion-dollar-database/747652/
- EJScreen removal: https://en.wikipedia.org/wiki/EJScreen and https://eelp.law.harvard.edu/tracker/epa-added-environmental-health-indicators-to-ejscreen/
- EPA GHGRP deadline and proposal: https://www.epa.gov/system/files/documents/2026-02/fact_sheet_ghgrp_reporting_deadline_february2026.pdf
- Berkeley Earth data license: https://berkeleyearth.org/data/
- 2026 midterms date: https://www.statista.com/study/214550/united-states-midterm-elections-2026/
