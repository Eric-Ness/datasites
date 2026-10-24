# Writing a pipeline

A dataset is two things:
- a **contract**: `contracts/sites/<site>/datasets/<id>.yaml`;
- a **pipeline module** in `pipelines/<domain>/src/datasites_<domain>/`.

The engine (`datasites_core`) does everything else:
- fetching with conditional GETs;
- archiving the raw files;
- change detection;
- validation;
- deterministic output files;
- uploads;
- the atomic manifest flip;
- the site catalog;
- state for the freshness monitor.

The worked example is `climatedata/co2-monthly` (`pipelines/climate/src/datasites_climate/noaa_gml_co2.py`).

## Contract reference

```yaml
id: county-climate            # kebab-case, equals the file name
version: 1                    # bump on any schema change (forces a rebuild)
kind: series                  # series (default) | table | files
title: County climate
description: >-
  One or two plain sentences: what it covers and from when.
pipeline: datasites_climate.nclimdiv_county    # module that implements it
check_every: P1D              # how often the runner looks (ISO 8601). Not due => skipped
cadence: P1M                  # how often the source should change
sla: P45D                     # status turns "stale-upstream" if the source hasn't changed in this long
requires_env: [BEA_API_KEY]   # optional: secrets it needs; without them the run reports "needs-credentials"
raw_archive_max_mb: 100       # optional: raw downloads above this aren't archived (they're refetched if needed)
source:
  id: noaa-ncei-nclimdiv      # kebab-case, used as the raw-archive folder
  name: NOAA NCEI Climate Divisional Database (nClimDiv)
  publisher: NOAA NCEI
  url: https://www.ncei.noaa.gov/...   # the page that documents the data and its terms
  license: LicenseRef-US-Government-Work   # SPDX id or LicenseRef-*
  commercial_ok: true         # true | false | null (unknown). Must match the source's actual terms.
  attribution: >-             # the exact credit text the source asks for
    ...
schema:                       # output columns, in order
  - { name: date, type: month }                    # series: the first column must be date
  - { name: value, type: float, unit: degF, description: Monthly mean }
  - { name: anomaly, type: float, unit: degF, nullable: true }
checks:                       # all optional
  rows_min_ratio: 0.995       # block if rows drop below 99.5% of the previous version
  min_rows: 1000
  ranges: { value: [-80, 130] }
  max_null_fraction: { value: 0.01 }
  newest_not_older: true      # series only (default true): block if any series' newest date goes backwards
  revisions: { max_changed_fraction: 0.05, tolerance: 0.1 }   # series only: warn on large history rewrites
  required_files: [boundaries/us-states.geojson]              # files kind only (required there)
```

Column types: `month` (YYYY-MM), `date` (YYYY-MM-DD), `year` (YYYY), `datetime` (ISO UTC, ending in `Z`), `float`, `int`, `str`, `bool`. Date-like columns are strings in the table.

**series** datasets also need:

```yaml
series:                       # series id (kebab-case) -> display name
  tavg: Average temperature
locations:                    # one of three forms:
  world: World                #   1. a fixed map of id -> name
# locations: {registry: us-county}                 2. every location of a geo-registry level
# locations: {registry: [us-state, country]}          (levels: world, country, us-state, us-county)
# locations: {pattern: "usgs-\\d{8,15}"}           3. ids matching a regex (station networks)
# locations: {registry: us-county, explicit: {us-county-09001: Fairfield County (legacy)}}
```

**table** datasets need `key` (the columns that identify a row) and may name a `partition` column:

```yaml
kind: table
key: [factor_id]
partition: source             # one table/<value>.json|csv per distinct value
```

## Module reference

How the raw data arrives. Define exactly one of these:

```python
RESOURCES = {"name": "https://..."}           # fetched with conditional GETs (preferred: cheap when unchanged)

def resources(ctx) -> dict[str, str]: ...     # same, computed at run time (e.g. one URL per year)

def collect(ctx) -> dict[str, bytes]: ...     # for paged or parameterized APIs:
    page = ctx.get("https://api...", params={"limit": 50000})   # retries 429/5xx, sets a contact User-Agent
    data = ctx.get_json(url, params=...)
    key = ctx.env("FIRMS_MAP_KEY")            # a required secret, with a clear error if missing
```

Optional:

```python
def normalize(name: str, body: bytes) -> bytes: ...   # strip timestamps etc. before change detection
```

What it produces, by kind:

```python
# series: a polars DataFrame with columns series, location, <schema columns...>
#         plus an optional location_name column (used for names with pattern locations; not published as data)
def transform(raw: dict[str, bytes]) -> pl.DataFrame: ...

# table: a polars DataFrame with exactly the schema columns
def transform(raw: dict[str, bytes]) -> pl.DataFrame: ...

# files: the finished files and stats; stats["rows"] is checked against checks.min_rows
def build(raw: dict[str, bytes]) -> tuple[dict[str, bytes], dict]: ...
```

Rules:
- Transforms are pure and deterministic. The same raw bytes must always give the same table: no clock reads, no network, no random ordering. The engine sorts rows.
- Use polars for anything over a few thousand rows. Python loops over millions of rows are too slow for CI runners.
- Missing values are `null`, never sentinel numbers like `-99.99` or `-9999`.
- Units: keep the source's units and record them in the schema. Add converted columns only when they're the point of the dataset.
- **Bump the package version** (`pipelines/<domain>/pyproject.toml`) when a transform changes its output. The version is part of change detection, so without a bump a fix never gets published.

## IDs

| What | Format | Example |
|---|---|---|
| dataset, series | kebab-case | `county-climate`, `tavg` |
| world | `world` | |
| country | lowercase ISO alpha-3 | `usa`, `twn` |
| US state or equivalent | `us-` + lowercase USPS | `us-ca`, `us-dc`, `us-pr` |
| US county | `us-county-` + 5-digit FIPS | `us-county-06037` |
| stations and monitors | source prefix + source id | `usgs-01646500`, `aqs-060371103` |

Countries, states and counties come from `packages/geo` (`datasites_core.geo` in Python). Never invent codes for them.

## What gets published (and the API paths)

| Kind | Files | API |
|---|---|---|
| series | `<series>/<location>.json` and `.csv`, `download/<id>.csv` and `.parquet` | `/v1/<id>/<series>/<location>.json` |
| table | `table/<partition>.json` and `.csv`, `table/all.*` (if ≤ 50,000 rows), `download/<id>.*` | `/v1/<id>/table/<partition>.json` |
| files | whatever `build()` returns, at those paths | `/v1/<id>/<path>` |

## Tests

- **Location:** tests go in `pipelines/tests/<domain>/`, with unique file names (`test_<domain>_<thing>.py`).
- **No `conftest.py` in subfolders.** Put helpers in `<domain>_helpers.py`. The root `conftest.py` provides a `store` fixture: an empty local bucket in a temp folder.
- **Fixtures:** put them in `pipelines/tests/<domain>/fixtures/`. They must be real excerpts copied verbatim from the source, with a header comment giving the URL and the date captured. Never invent data.
- **No network:** tests must pass offline. To run a whole pipeline offline, pass a fake `fetcher` to `run_dataset`, or patch `ctx.get`.
- **What to cover:**
  - parsing edge cases the real files contain (missing-value markers, footnotes, renamed columns);
  - that validation blocks a realistic bad input;
  - an end-to-end `run_dataset` into the `store` fixture.

## Independent verification script

Each domain has `scripts/verify/<domain>.mjs`, which prints `<DOMAIN> VERIFIED ...` only after every assertion passes. The script must:
- read what `datasites run <site> --target local` published, using `localDataset(site, id)` from `scripts/verify/lib.mjs`;
- re-derive at least three facts per dataset from the original source with code separate from the pipeline: a value for a known place and date, a row or location count, and a range or extreme;
- accept `--negative-control`, which corrupts its own copy of one expected value and confirms the check then fails.

## Ingest workflow

Each domain has `.github/workflows/ingest-<domain>.yml`. Copy `ingest-climate.yml` and change the name, schedule, site argument, secrets and the healthchecks secret (`HC_INGEST_<DOMAIN>`).

- **Gate:** the job must be gated on `vars.INGEST_ENABLED == 'true'`.
- **Schedule:** use a specific odd minute, never `0` or `*`.
- **Minutes budget:** the repo is private, so all ingest workflows together get about 1,400 Actions minutes a month. `scripts/verify/workflows.mjs` enforces this. Daily is the default. Anything more frequent needs a reason, and every-3-hours is the ceiling.

## Sources and licensing

- **Prefer the original public provider:** NOAA over a mirror, EPA over OpenAQ.
- **`commercial_ok` must reflect the source's real terms,** read from its terms page.
- **Never use these:** FRED as a source, the OpenAQ API, Berkeley Earth, GADM, individual voter files, or FEC individual donor names.
- **CC BY sources need their exact attribution text.** Share-alike sources (ODbL, CC BY-SA) must stay separate datasets.
- **Be polite to sources:**
  - use conditional GETs;
  - use bulk files over per-item APIs;
  - respect published rate limits;
  - never hammer a server in a loop.
