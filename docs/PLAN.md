# Data Sites Plan

Prepared 2026-10-23 for the ten data domains. Research agents checked the source facts on 2026-10-23. Re-check anything you depend on before building on it.

The full research is in three files:
- [research/01-environmental-sources.md](research/01-environmental-sources.md): climate, water, air, carbon, wildfire
- [research/02-economic-election-satellite-visualize.md](research/02-economic-election-satellite-visualize.md): economic, election, roll-call votes, satellite, visualization
- [research/03-deployment-and-pipeline.md](research/03-deployment-and-pipeline.md): hosting, API design, update pipeline, limits and costs

---

## 1. The plan in brief

- **One platform runs all ten sites:**
  - One public GitHub monorepo.
  - One static Astro website per domain.
  - One Cloudflare API Worker serving every site at `api.<domain>`.
  - One R2 storage bucket.
  - Python pipelines that rebuild the data on a schedule.
  
  Expect about $5/month at launch and about $21/month at moderate traffic.
- **Every domain goes live within a week** with a coming-soon page and an email signup. votingdata.net redirects to electiondata.dev.
- **Sites launch in deadline order, not alphabetically:**
  1. climatedata first, to prove the pipeline.
  2. electiondata, while certified 2026 results are fresh, and waterdata, before USGS shuts down its old API on Feb 22, 2027.
  3. carbondata and economicdata.
  4. wildfiredata and airdata, before fire season.
  5. satellitedata and visualizedata.
- **Every dataset records its license, its attribution text and whether commercial use is allowed.** The build fails if any of these is missing. Paid tiers will include only data that may be sold.
- **A bad update never replaces good data.** Each run is checked before it is published. If the checks fail, the site keeps serving the last good version and you get an alert.

---

## 2. What each site ships first

| Site | First product | Main sources | Size | Updates | Why it's worth building |
|---|---|---|---|---|---|
| **climatedata.dev** | **US county climate report card.** Monthly temperature and precipitation for about 3,100 counties since 1895, each value with its 1991–2020 normal, its anomaly and its rank ("3rd warmest September on record"). Also global headline series and ERA5 monthly values for about 200 world capitals. | NOAA nClimDiv, NASA GISTEMP, Met Office HadCRUT5, NOAA GML CO2/CH4, ERA5 | ~150 MB | Monthly (around the 6th) | climate.gov went dark in June 2025, and nothing replaced its county data. The inputs are public domain or CC BY. Every county gets its own page that can rank in search. |
| **economicdata.dev** | **US Regional Economic Panel.** Every county, metro and state in one table: monthly unemployment, quarterly jobs and wages by industry, and annual income, GDP, population and poverty. | BLS (LAUS, QCEW), BEA, Census | 200–500 MB (Parquet) | Monthly, quarterly, annual | FRED splits this data into thousands of separate series, and its terms forbid copying it. Every input here is public domain. |
| **electiondata.dev** | **Certified 2026 general-election results by county** for every federal and statewide race, joined to county history for 2000–2024. | State certified canvass reports, MIT Election Lab (CC0), EAC EAVS, Census citizen voting-age population | Under 10 MB, plus tens of MB of history | As states certify (mid-Nov to mid-Dec), then corrections | The free, clean national files have lagged about 18 months behind each election. |
| **votingdata.net** | **302 redirect to electiondata.dev for now.** The later opportunity is a congressional roll-call vote API covering 1789 to today. | House Clerk XML, Senate XML, Congress.gov API, Voteview, congress-legislators (CC0) | ~100 MB | Hourly while Congress is in session | ProPublica's and GovTrack's vote APIs, which developers relied on, are both shut down. |
| **waterdata.dev** | **National streamflow conditions.** Daily flow at every active USGS stream gage, with "% of normal" for the date, flood status and drought category. | USGS Water Data API (the new one), NWS NWPS, US Drought Monitor | 2–5 MB a day; 1.5–3 GB of history | Daily | USGS shuts down its legacy WaterServices API on **Feb 22, 2027**. Thousands of scripts and apps need a replacement. |
| **carbondata.dev** | **Open emission-factor API.** Versioned Scope 1, 2 and 3 emission factors in consistent units, with permanent URLs that auditors can cite. | EPA GHG Emission Factors Hub (Cornerstone 2026 release), eGRID2024 (Cornerstone), Ember, UK DESNZ, USEEIO v1.4 | Under 100 MB | Annual, plus Ember monthly | EPA stopped maintaining these factor sets. California SB 253 reporting began this year (first Scope 1/2 deadline Nov 10, 2026). Companies already pay for this data (Climatiq is the comparison). |
| **wildfiredata.dev** | **Unified active-fire feed.** Satellite hotspots grouped into fires and linked to the official incident (name, acres, containment, perimeter), with a smoke overlay. It also powers FireWatch. | NASA FIRMS (VIIRS), NIFC WFIGS, Canada's CWFIS, NOAA HMS smoke | 1–3 GB a year | Every 15–30 min | FIRMS has satellite detections with no incident details, and WFIGS has incidents with no satellite data. Insurers, utilities and app builders want both together. |
| **airdata.dev** | **County AQI history plus live AQI.** Validated EPA data from 1980 to about six months ago, joined to preliminary AirNow data since, with each row flagged validated or preliminary. | EPA AQS, EPA AirNow | 50–100 MB | Hourly, plus backfills | EPA splits this across two separate systems. Both sources are public domain. |
| **satellitedata.dev** | **Unified satellite catalog.** One record per object (owner, operator, orbit and status) that handles the new 6-digit catalog numbers, plus a daily changelog. | Space-Track (redistribution allowed with citation), GCAT (CC BY), CelesTrak as a backup | Under 200 MB | Orbits hourly, catalog daily | Five-digit catalog numbers ran out in July 2026, which broke tools built on the old TLE format. The best-known catalog of who owns what (UCS) has been frozen since May 2023. |
| **visualizedata.dev** | **Shared geography pack and family catalog.** World and US boundaries with ID crosswalks, followed by the chart gallery and embeddable charts. | Natural Earth (public domain), Census cartographic boundary files, geoBoundaries gbOpen (CC BY) | 0.5–1 GB | Yearly | Every other site needs matching map boundaries. |

---

## 3. Changes in the data world that shape this plan

- **US federal funding runs out Dec 11, 2026.** A lapse would delay BLS, BEA and Census releases again. October 2025 CPI was never published. Flag gaps in the data rather than filling them quietly.
- **The legacy USGS WaterServices API shuts down Feb 22, 2027.** Service degrades from November and has planned outages in January. Build only on api.waterdata.usgs.gov.
- **EPA's climate data has stalled.** GHGRP facility data stops at reporting year 2023. eGRID and the emission-factor files are now kept up by Cornerstone, a nonprofit group, rather than EPA. Label those files "EPA method, not EPA-official."
- **Gone or retired:** NOAA's Billion-Dollar Disasters database, EPA EJScreen, climate.gov, the National Climate Assessment site, ProPublica's Congress API, GovTrack's API, and Google Civic's Representatives endpoint.
- **AirNow retired six API endpoints on Sep 30, 2026.**
- **The Census API requires a key on every call** (since May 12, 2026).
- **IMF replaced its data portal** in Nov 2025, and the old series codes don't map to the new ones.
- **CelesTrak allows one download per data update.** It blocks repeat requests, and TLE can't carry the new 6-digit catalog numbers.
- **MODIS fire data ends in 2027,** so plan on VIIRS only.
- **Ten states redrew their congressional maps** after 2024: AL, CA, FL, LA, MO, NC, OH, TN, TX and UT. Compare 2024 and 2026 by district only with the new boundary files.

---

## 4. Licensing rules

| Use freely (cite the source) | Use with conditions | Ask the owner first | Avoid |
|---|---|---|---|
| US federal data: NOAA, NASA, USGS, EPA, BLS, BEA, Census, FEC committee totals | **Eurostat:** remove non-EU countries before commercial use | **IMF:** permission needed for commercial use | **FRED as a source:** its terms forbid replicating it, so use it only for spot checks |
| MIT Election Lab (CC0), congress-legislators (CC0) | **ECB:** don't modify its statistics; publish derived values as separate fields | **Voteview** (no license stated) | **OpenAQ API:** non-commercial unless you pay |
| Copernicus/ECMWF, including ERA5 (CC BY 4.0 since Jul 2025) | **AirNow, EEA:** don't alter values; publish the raw value next to anything derived | **OpenElections** (license unclear) | **Berkeley Earth** (CC BY-NC) |
| World Bank, OECD from 2024 on, Climate TRACE, Global Carbon Budget, Ember, GCAT (all CC BY 4.0) | **Space-Track:** cite it, and only basic catalog and orbit data may be redistributed | **Launch Library 2, UCS** (no license stated) | **EDGAR's IEA-based CO2 series** (non-commercial, no derivatives) |
| UK DESNZ factors, HadCRUT5 (OGL v3) | **Copernicus Sentinel:** include the required credit line | **BIS:** you can't charge extra for its data | **GADM boundaries** (non-commercial), **UF Election Lab** (non-commercial) |
| Natural Earth (public domain), geoBoundaries gbOpen (CC BY) | **SatNOGS, Overture:** share-alike licenses, so keep them as separate datasets | | **Open-Meteo free API, PRISM 800 m, PurpleAir, GRDC portal** |
| | **OWID:** respect its "non-redistributable" responses | | **Individual voter files, and FEC individual donor names** (state laws and federal law) |

---

## 5. How the sites are deployed

### Hosting layout

| Piece | Where it runs | Notes |
|---|---|---|
| Websites | One static Astro project per domain, deployed as an assets-only Cloudflare Worker on the bare domain (e.g. `climatedata.dev`) | Static file requests are free and unlimited. Cloudflare now recommends Workers over Pages for new projects. Charts load data from the API in the browser, so a data update never requires rebuilding a site. |
| API | One Worker, `datasites-api`, on `api.<domain>` for all nine .dev sites | It picks the site from the hostname. A staging copy runs on workers.dev. |
| Data | Private R2 buckets for production and staging | Files are stored by content hash. Each dataset has one small `manifest.json` that points at its current version. |
| Pipelines | Python 3.13 (uv, polars, duckdb) on GitHub Actions; small hourly feeds on Cloudflare Workflows | Python because the key libraries (xarray, cdsapi, earthaccess, pystac-client, sdmx1) are Python-only. |
| Signups now, accounts and API keys later | Cloudflare D1 | Data stays in files, not in the database. |
| `www.<domain>` | Redirect rule to the bare domain | |
| votingdata.net | Redirect rule, **302** to electiondata.dev | 302 rather than 301, because browsers keep 301s cached long after you change them. |

Use **Workers Paid ($5/month) from day one.** The free plan allows 10 ms of CPU per request and only 5 scheduled jobs per account, which is too little.

### Repo layout (short version)

```
datasites/                 public GitHub repo
├─ apps/api/               the single API Worker (Hono, TypeScript)
├─ apps/coming-soon/       one Worker for every placeholder domain, plus the signup form
├─ apps/ingest-hot/        hourly feeds on Cloudflare Workflows
├─ sites/<domain>/         one thin Astro project per site
├─ packages/               shared theme, chart components, geography codes, contract tools
├─ contracts/              one YAML file per dataset: the source of truth
├─ pipelines/              Python: core library + one package per domain
└─ .github/workflows/      CI, deploy, ingest-<domain>, backfill, rollback, janitor, canary
```

### API shape (example: climatedata)

```
https://api.climatedata.dev/v1/catalog                                   every dataset on the site
https://api.climatedata.dev/v1/county-climate                            metadata, license, provenance
https://api.climatedata.dev/v1/county-climate/tavg/us-county-06037.json  one series, one place (.csv also)
https://api.climatedata.dev/v1/county-climate/download/county-climate.parquet   bulk file
https://api.climatedata.dev/v1/county-climate@2026-10-23.3f9a1c2e4b/...  pinned version (cache forever)
https://api.climatedata.dev/v1/status                                    freshness of every dataset
https://api.climatedata.dev/v1/openapi.json                              API spec
```

Errors use the standard `application/problem+json` format. CORS is open for GET requests. The request pipeline (`resolve site → authenticate → rate-limit → handler → meter`) exists from day one, with anonymous access at launch. Adding API keys later is a configuration change, not a rewrite.

### Moving DNS from DirectNIC to Cloudflare (you do this)

1. In Cloudflare, add each of the 10 domains (Free plan per zone). Add DNS records by hand rather than importing DirectNIC's parking records. [research/03](research/03-deployment-and-pipeline.md#26-dns-migration-directnic-to-cloudflare) has a PowerShell script that adds all ten at once.
2. Confirm DNSSEC is off for each domain at DirectNIC.
3. At DirectNIC, open each domain and replace the nameservers with the two Cloudflare gives you.
4. Wait for each zone to show **Active**. This takes minutes to 24 hours. Cloudflare deletes zones still pending after 28 days.
5. Wait for the HTTPS certificate, which takes 15 minutes to 24 hours. .dev sites won't load in any browser until it exists. Turn on "Always Use HTTPS".
6. Leave Bot Fight Mode **off**, because it blocks API clients. Turn on Email Routing for `hello@<domain>`.
7. After the 60-day transfer lock, consider moving the domains to Cloudflare Registrar, which renews at cost.

---

## 6. How the data stays current

### One contract file per dataset

Each dataset has a YAML file in `contracts/` that defines:
- the source, license, attribution and commercial-use flag;
- how often to check for new data, and how often the source should change;
- the schema (columns, types, units);
- the quality checks.

The pipelines, the API docs, the OpenAPI spec and the status page are all generated from it.

```yaml
id: county-climate
check_every: "23 14 * * *"     # look daily at 14:23 UTC
cadence: P1M                   # source should change monthly
sla: P40D                      # flag "stale upstream" after 40 days with no change
source: { name: NOAA nClimDiv, license: LicenseRef-US-Government-Work, commercial_ok: true, attribution: "..." }
checks: { rows_min_ratio: 0.995, ranges: { tavg_f: [-60, 130] }, max_null_fraction: { tavg_f: 0.01 } }
```

### What runs where

| Workload | Examples | Scheduler |
|---|---|---|
| Hourly, small JSON | AirNow latest | Cloudflare Workflows (cron schedule) |
| Hourly, bulk files | NASA FIRMS fire CSVs | GitHub Actions at an odd minute (e.g. `:23`) |
| Daily to monthly | NOAA, BLS, BEA, USGS daily, World Bank | GitHub Actions cron (free on a public repo, 6-hour jobs, 16 GB runners) |
| Heavy or annual | ERA5 extracts, backfills | GitHub Actions, split by year across parallel jobs |
| Irregular releases | Certified election results, IMF outlook | Manual run, plus a temporary cron during release windows |
| Freshness monitor | All datasets | The API Worker, every 15 minutes |

### What one pipeline run does

`fetch → archive raw → change check → transform → validate → upload files → write version record → flip manifest`

1. **Fetch,** using the stored ETag and Last-Modified values. If the source says nothing changed, stop.
2. **Archive** the raw download, deduplicated by hash. It's the one thing you can't recreate if a federal source disappears. Copy the archive monthly to a second provider such as Backblaze B2.
3. **Change check:** hash the raw data together with the contract and code versions. If the hash matches the last run, stop. Including the code version means a bug fix republishes even when the source didn't change.
4. **Transform** into deterministic files (sorted rows, fixed number formatting). The same input always produces byte-identical output.
5. **Validate:** schema, row counts compared with the last version, physical value ranges, null rates and sudden jumps. A failure **blocks** the publish. The previous version keeps serving, the bad one is quarantined for review, and you get an alert.
6. **Upload** each file under its content hash. Files that already exist are skipped, which keeps storage writes and costs down.
7. **Write the version record:** provenance, validation results and the list of files.
8. **Flip the manifest** with a conditional write, so two overlapping runs can't both publish. Readers see the new version immediately.

### Safety nets

- **Rollback** is one write that points the manifest back at an earlier version. A `data-rollback` workflow does it in under a minute.
- **Snapshots:** the first version of each month is kept forever, so researchers can cite an exact version.
- **No cache purging on routine updates.** Data files never change once written; only the small manifest does, and it's cached for at most 60 seconds.
- **A weekly canary** requests every source URL and opens a GitHub issue if one disappears, redirects, or changes format.

### Monitoring

1. **Dead-man switches (healthchecks.io).** Every workflow pings when it starts and finishes. This catches GitHub skipping a scheduled run, which it sometimes does, and schedules disabled after 60 days without activity.
2. **The freshness check** runs every 15 minutes and marks each dataset `ok`, `late`, `stale-upstream`, `failing`, `blocked` or `discontinued`.
3. **The status endpoint** `https://api.<site>/v1/status` is polled by an external uptime monitor and shown on each site's status page.

Alerts go to email and phone push.

---

## 7. Rollout schedule

The dates assume part-time solo work. If you fall behind, push the whole phase back rather than skipping the checks.

| Phase | Dates | What ships | Why this order | Done when |
|---|---|---|---|---|
| **0. Foundation** | Oct 24 – Nov 8, 2026 | All 10 domains on Cloudflare with HTTPS. Coming-soon pages and signup on the nine .dev sites, with **electiondata.dev live before Nov 3**. votingdata.net redirect. Monorepo, CI with preview links, R2 buckets, API skeleton, pipeline core library, shared geography codes. One dataset (NOAA monthly CO2) running end to end. | Everything else depends on it. | Every domain loads over HTTPS. A signup reaches the database. `api.climatedata.dev/v1/status` reports `ok`. A deliberately broken run alerts your phone within 15 minutes. |
| **1. climatedata.dev** | Nov 9 – Dec 20 | County climate report card, global headline series and ERA5 capitals. Site with catalog, charts, docs and API reference. **At the same time:** collect certified election results as each state certifies (about Nov 13 – Dec 11). | Clean public-domain inputs and a monthly update test the pipeline before the deadline-driven sites. | A no-change run writes nothing. A bad input is blocked and alerts. A rollback drill takes under 5 minutes. |
| **2. electiondata.dev + waterdata.dev** | Dec 21, 2026 – Feb 15, 2027 | Certified 2026 county results for federal and statewide races, plus 2000–2024 history. National streamflow conditions on the new USGS API. | Election data is most useful while it's fresh. USGS shuts off the old API Feb 22. | Every election row links to its source document and is labeled certified or unofficial. waterdata is live before Feb 22. |
| **3. carbondata.dev + economicdata.dev, free API keys** | Feb 16 – Apr 15 | Emission-factor API, US Regional Economic Panel, and free key signup. | carbondata has the clearest paying audience. economicdata has no deadline, and a Dec 11 funding lapse would disrupt its sources. | Keys are optional, keyed clients get higher limits, and usage per key is visible. |
| **4. wildfiredata.dev + airdata.dev** | Apr 16 – Jun 15 | Unified active-fire feed (shared with FireWatch) and county AQI. | Ready before the western fire and smoke season. | The "latest" data is under 90 minutes old (p95) over two weeks. |
| **5. satellitedata.dev + visualizedata.dev** | Jun 16 – Aug 31 | Satellite catalog, then the chart gallery and embeds using the other sites' data. | No deadline. The gallery needs the other sites' data first. | Every chart embed carries source attribution. |
| **6. Paid tiers; votingdata.net decision** | H2 2027 | Stripe usage-based plans, switched on when demand shows: over 5M requests a month, 3 or more commercial inquiries, or one key using over 30% of traffic. Decide whether votingdata.net becomes the roll-call API. | Charge once there's proof people want it. | Checkout to a working key in under 2 minutes. Paid plans include only commercial-OK datasets. |

**Election data rules:** certified results only, with no live election-night feed. Every row is labeled with its status and links to its source. Publish aggregate counts only, never voter files.

---

## 8. Costs

| Item | Launch | Moderate (10 sites, ~30M requests/month) |
|---|---|---|
| Cloudflare Workers Paid | $5.00 | $5.00 |
| Extra requests, CPU, R2 storage and operations | $0 | ~$16 |
| GitHub Actions, healthchecks.io, Email Routing, D1 | $0 (free tiers; requires a public repo) | $0 |
| **Infrastructure total** | **~$5/month** | **~$21/month** |
| Domain renewals | ~$208/year (~$17/month) from year two | Lower after moving to Cloudflare Registrar |

A month with 300M requests and no rate limits would cost about $120. The rate limits and Cloudflare's budget alert exist to prevent that.

---

## 9. Decisions for you

1. **Build order.** I recommend the deadline order in section 7. The alternative is the original climatedata + economicdata first, but it misses the election and USGS windows.
2. **Public or private repo. Decided 2026-10-24: private** (`Eric-Ness/datasites`). The consequences:
   - Actions minutes are capped: about 2,000 a month on GitHub Free, 3,000 on Pro.
   - Runners are smaller: 2 CPUs and 8 GB.
   - **Hourly feeds must run on Cloudflare Workflows**, not GitHub Actions.
   - GitHub Actions keeps the daily and slower jobs, which cost about a minute each when nothing has changed.
3. **Workers Paid at $5/month now.** I recommend yes.
4. **Election-night live results.** I recommend skipping them. It means scraping hundreds of county sites under deadline pressure and risking spreading wrong numbers, and AP and Decision Desk HQ already own that market.
5. **votingdata.net.** I recommend the redirect now. The roll-call API is a real gap, though, so it could move earlier if it interests you more than satellitedata.
6. **Project folder.** Where the monorepo lives, for example `C:\Users\ericn\source\repos\datasites`, next to your other projects.

---

## 10. Accounts and keys you'll need

| Account | Needed for | Phase |
|---|---|---|
| Cloudflare Workers Paid, and narrowly scoped API tokens | Everything | 0 |
| GitHub repo (public), healthchecks.io | CI, schedules, alerts | 0 |
| Copernicus Climate Data Store account | ERA5 | 1 |
| api.data.gov key | USGS Water Data, Congress.gov, FEC | 2 |
| Census API key | Turnout denominators, population, income | 2–3 |
| BLS v2 key, BEA UserID, Ember API key | economicdata, carbondata | 3 |
| NASA FIRMS MAP_KEY, NASA Earthdata Login | wildfiredata | 4 |
| AirNow API key, AQS API key | airdata | 4 |
| Space-Track account (one per person, never shared) | satellitedata | 5 |
| Stripe | Paid tiers | 6 |

Permission emails to send before charging for that data: IMF, the Voteview authors, The Space Devs (Launch Library 2), and Derek Willis (OpenElections).

---

## 11. Changes from the agent reports

I combined three agents' reports. Where they disagreed, this plan takes these positions:
- **Build order** follows the election and USGS deadlines rather than the deployment agent's climate + economic first and elections in spring 2027.
- **airdata uses AirNow and AQS, not OpenAQ.** OpenAQ's API is non-commercial unless you pay.
- **satellitedata treats Space-Track as the source of record.** Its redistribution terms are explicit; CelesTrak states none, so it's the backup.
- **FRED is for spot checks only.** The deployment agent listed a FRED key, but FRED's terms forbid replicating it.
- **economicdata's first product is the Regional Panel,** not headline national series that FRED already serves.

## 12. Not verified yet

- OECD's exact rate limits (two OECD pages disagree) and FRED's per-minute limit.
- License terms for Voteview, OpenElections, VEST/Redistricting Data Hub, Launch Library 2 and CelesTrak.
- Whether senate.gov's vote XML can be fetched by a script. It returned 403 to the agent's automated request.
- Whether the ECB API is reachable. It returned 503 twice on Oct 23.
- Whether DirectNIC accepts DNSSEC DS records for .dev and .net.
- Whether Cloudflare Workflows schedules count toward the 250 cron-trigger limit.
