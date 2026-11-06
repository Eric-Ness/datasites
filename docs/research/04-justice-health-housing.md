# Public data sources: justicedata.dev, healthdata.dev, housingdata.dev

Research agents checked these sources on 2026-11-06 with live requests, fetches and web search. Status tags:
- **Live**: the endpoint answered a request today.
- **Docs**: we read the official page or terms today.
- **Unverified**: we could not confirm it today.

Licenses are as read from each source's own terms page. Where a page was blocked, that is stated. The rules in `docs/PIPELINES.md` apply:
- prefer the original publisher;
- `commercial_ok` must match the source's real terms;
- no scraping against terms.

---

## 0. What applies to all three sites

| Item | What we found | Effect |
|---|---|---|
| **Census Data API** | A key has been required on every data query since 2026-05-12. A keyless call redirects to `missing_key.html`. The www2.census.gov bulk files still download without a key. | Prefer the bulk files: population estimates (`co-est2025-alldata.csv`), SAHIE, BPS and the ACS summary file. Keep a key in secrets only for whatever must come from the API. |
| **Population denominators** | `www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv`, Last-Modified 2026-03-26 (**Live**). The next vintage is due in March 2027. | One shared denominator for "per 100k" indicators on all three sites. It belongs in the engine or the geo package, not in each site. |
| **data.cdc.gov (Socrata)** | Anonymous requests are throttled, and a free app token raises the limit. CSV exports return an ETag and Last-Modified, so conditional GETs work. Series get **new dataset IDs**: PLACES gets one every release, and the old NWSS tables stopped on 2026-08-07. About 13% of data.cdc.gov was taken down in early 2025, and some datasets came back modified. | Find IDs from the catalog API instead of hard-coding them. Keep raw archives, as the engine already does. Alert when a series stops updating. |
| **huduser.gov** | Every portal page and file URL answered scripted clients with an **AWS WAF challenge** (`202`, empty body, `x-amzn-waf-action: challenge`). Only the token API (`/hudapi/public/...`, 60 queries/min) answered normally. | Pipelines can't fetch HUD bulk files without getting around the challenge, which we won't do. Use the API, a file a person downloads by hand (like the MEDSL files), or ask HUD USER for access. A `202` with an empty body passes a status check, so the engine must reject empty bodies. |
| **Federal release lag** | Shutdown effects are still visible: the 2020–2024 ACS 5-year came out 2026-01-29, HUD Income Limits slipped to May 2026, and BJS has a publication gap in fall 2025. Current funding runs to Dec 11, 2026. | `sla` values need slack. Late releases are normal. |
| **The most-cited private and NGO datasets can't be republished** | NC, all-rights-reserved or unlicensed: County Health Rankings, IHME GBD, WHO GHO, Vera Incarceration Trends, the Washington Post's Fatal Force database, Mapping Police Violence, Gun Violence Archive, Zillow, Redfin, Apartment List, NAR, Case-Shiller and NLIHC. Details are in each section. | Every MVP below uses federal public-domain data. Some NGO sources may grant permission, which we can ask for later. |
| **International** | WHO GHO and UNODC have restrictive terms, and the WHO GHO API is being retired. The World Bank WDI carries the same series under CC BY 4.0 (**Live**, updated 2026-10-08). OECD (CC BY 4.0) and Eurostat (CC BY 4.0) are also usable. BIS is usable on a free site. | Route international basics through the World Bank WDI, as visualizedata.org already does. |

---

## 1. justicedata.dev

### Sources

| Source | Endpoint | Access | Latest | Geography | License | Notes | Status |
|---|---|---|---|---|---|---|---|
| **FBI CDE API, summarized offenses** | `api.usa.gov/crime/fbi/cde/summarized/{national\|state/XX\|agency/ORI}/{offense}?from=MM-YYYY&to=MM-YYYY` | api.data.gov key (DEMO_KEY allows 10 requests/hr) | Monthly, through **Sep 2026** (Oct partial) | National, state, agency | Public domain | Reported counts and rates per 100k covered population, not estimates. The last 1–3 months are incomplete. Agencies map to counties by *name*; there is no FIPS field. | **Live** |
| **FBI annual estimates** | CDE downloads page; bulk files sit behind a signed-URL service. `/estimate/national` returned 404 | — | **2025, released Aug 2026** | National, state | Public domain | NIBRS-only coverage was 89.5% of the population in 2025; NIBRS plus SRS reached 96.2%. **2021 is a hole** (about 65% coverage). NIBRS counts every offense in an incident, while SRS used the hierarchy rule, so the series aren't strictly comparable. | **Docs**; stable URL not yet found |
| **NIBRS national estimates (BJS)** | `api.ojp.gov/bjsdataset/v1/{id}.csv` (Socrata; endpoints moved July 2025) | No key | 2021+ | National only | Public domain | Earlier years are revised each year; archive datasets keep the original estimates. | **Docs** |
| **Hate crime** | CDE `/hate-crime/national` | Key | 2025 (95.3% of population covered) | National, state, agency | Public domain | Reporting is voluntary. | **Live** |
| **LEOKA** | 2025 report PDF; the API path returned 404 | — | 2025 | National, state | Public domain | | **Docs** |
| **FBI Use-of-Force collection** | Aggregate tables only | — | Rolling | National | Public domain | Low participation; it may be discontinued. | **Unverified** |
| **NCVS (BJS)** | `api.ojp.gov/bjsdataset/v1/gcuy-rt5g.csv` and siblings | No key; aggregate queries triggered Cloudflare, so pull full CSVs | 2024 (legacy instrument) | National, region | Public domain | **Series break:** a redesigned instrument from Jan 2025, and a new sample in 2026. | **Live** |
| **Prisoners / NPS (BJS)** | CSAT and report tables | — | **2023** (Prisoners in 2024 is not out yet) | National, state | Public domain | About a 2-year lag; tables come from reports, not an API. | **Docs** |
| **Probation and parole (BJS)** | Dashboards and tables | — | 2024 | National, state | Public domain | | **Docs** |
| **Jail Inmates / ASJ (BJS)** | Report CSVs | — | 2024 | **National only** | Public domain | A sample of about 900 jails; there is no annual county census. | **Docs** |
| **Capital punishment (BJS)** | Statistical tables | — | 2023 | National, state | Public domain | | **Docs** |
| **US Sentencing Commission datafiles** | `ussc.gov/sites/default/files/zip/opafy25nid_csv.zip` (21 MB, ETag) | No key | **FY2025** | Federal district → state | Public domain | Individual-level files with confidentiality withholding. | **Live** |
| **FJC Integrated Database** | `fjc.gov/.../idb/datasets/cr21to26.zip` (ETag; updated 2026-08-27) | No key | FY2026 partial | Federal district | Public domain | Large tab-delimited files; codes need the research guide. | **Live** |
| **NCHS VSRR quarterly mortality** (homicide, firearm) | `data.cdc.gov/resource/489q-934x` | No key | **2025 Q3** | National, state | Public domain | Shared with healthdata. | **Live** |
| **DPIC execution database** | `deathpenaltyinfo.org/query/executions.csv` (ETag; 1,689 rows with county FIPS) | Open | Updated daily | Execution, county | **No license stated (© DPIC)** | Excellent data; ask DPIC for permission. | **Live** (license unverified) |
| **City incident portals** | Chicago `ijzp-q8t2`, NYC `5uac-w243` (Socrata, daily). LA's dataset has been stale since March 2026 (NIBRS move). | — | Daily | City | Each city's own terms | Offense codes differ by city; later phase. | **Live** |
| **World Bank `VC.IHR.PSRC.P5`** (homicides per 100k, UNODC-sourced) | `api.worldbank.org/v2/country/all/indicator/VC.IHR.PSRC.P5` | No key | 2023 | Country | CC BY 4.0 | Use instead of UNODC directly. | **Live** |

**Excluded on license:**

| Source | Why excluded |
|---|---|
| Vera Incarceration Trends | Its `License.pdf` limits use to the recipient's own non-commercial research, with no distribution. There is no public-domain substitute for annual county jail populations. |
| Washington Post Fatal Force | Ended at 2024, and licensed CC BY-NC-SA. |
| Mapping Police Violence | No data license. |
| Gun Violence Archive | Copyrighted and not public domain, by its own statement. |
| Prison Policy Initiative | No data license. |
| The Marshall Project | Its data repos have no license. |
| NCSC Court Statistics Project | Tableau dashboards only, with no stated terms. |
| UNODC direct | Personal, non-commercial use only. |

### MVP, ranked

1. **State crime rates, 1979–2025 (FBI estimates).**
   - Coverage: violent, property, murder, robbery and aggravated assault rates per 100k, plus clearance rates. National and state, annual.
   - It is the longest clean federal series, and 2025 has just been released.
   - **To do first:** find a stable URL for the estimates file.
2. **Monthly reported crime (CDE summarized API).**
   - Shows whether crime is up or down year to date, by state, with reporting coverage shown next to every value.
   - Refreshed weekly. The newest two or three months are flagged as incomplete.
3. **Homicide and firearm death rates by state (NCHS VSRR).**
   - An independent measure of lethal violence that cross-checks the FBI's murder rate.
   - The same dataset feeds healthdata. Publish it once, on one site, and link to it from the other.
4. **Federal sentencing and caseloads (USSC plus FJC).**
   - Federal sentences per 100k by state, median sentence length, and filings by district.
   - The only open court data available.
5. **Imprisonment and community supervision by state (BJS).**
   - Prisoners per 100k, and people on probation or parole per 100k.
   - The data lags about two years, so it fits better as a yearly update than as a scheduled pipeline.

**Indicators:**
- violent crime per 100k
- murder per 100k
- property crime per 100k
- clearance rate
- year-to-date change in violent crime
- homicide deaths per 100k (CDC)
- firearm deaths per 100k
- prisoners per 100k
- federal sentences per 100k

**Not yet:**
- county jail populations: no open source;
- police killings: no licensable source;
- executions: waiting on DPIC's permission;
- county crime rates: agency reporting coverage makes county aggregates misleading, and there is no open ORI-to-FIPS crosswalk.

**justicedata.dev is a state-first site.**

### Risks

- **NIBRS comparability.** Publish reporting coverage alongside every value, and never show a county rate without it.
- **BJS lag.** Release timing is unpredictable.
- **Fragile CDE API paths.** Some paths returned 404 today.
- **Annual revisions.** Version each release, don't overwrite it. The engine already does this.
- **Political attention.** The 2025 numbers show "the largest drop since 1937", so state reported vs. estimated counts plainly.

---

## 2. healthdata.dev

### Sources

| Source | Endpoint | Access | Latest | Geography | License | Notes | Status |
|---|---|---|---|---|---|---|---|
| **VSRR drug overdose deaths, state** | `data.cdc.gov/resource/xkb8-kh2a.csv` | No key (app token advised) | Monthly; 12-month periods through **May 2026** (updated 2026-10-14) | State, US, NYC | Public domain | Reported and predicted values; completeness thresholds. | **Live** |
| **VSRR drug overdose deaths, county** | `gb4e-yj24` | Same as above | 12 months ending **2026-03-31** | County FIPS | Public domain | Counts of 1–9 suppressed; includes a percent-pending field. | **Live** |
| **VSRR quarterly mortality** | `489q-934x` | Same as above | **2025 Q3** | National, state | Public domain | Age-adjusted rates by cause, including homicide and firearm. | **Live** |
| **Provisional COVID, flu and RSV deaths** | `r8kw-7aab`, `4bc2-bbpq` | Same as above | Week ending **2026-10-31** | State | Public domain | | **Live** |
| **CDC WONDER** | POST API | No key; about 1 query per 2 minutes | Final through 2024 | **API: national only** | Public domain, under a data use agreement | State and county data are only in the web UI, so they're off-limits for pipelines. Suppression under 10. | **Docs** |
| **Stale CDC tables** | `bi63-dtpu` (leading causes, ends 2017), `it4f-frdc` (state life expectancy, 2021), `rpvx-m2md` | — | — | — | Public domain | No current successor on data.cdc.gov. | **Live** (metadata) |
| **CDC PLACES 2025** | County `swc5-untb` (one ID per release) | No key | BRFSS 2022–2023 data | County, tract, ZCTA, place | Public domain | Modeled estimates; methods change between releases. | **Live** |
| **BRFSS state prevalence** | `d2rk-yvas` | No key | **2025** | State, MMSA | Public domain | Methodology break in 2011. | **Live** |
| **ATSDR SVI** | Download page | — | SVI 2022 (no 2024 edition) | County, tract | Federal | Don't compare across versions. | **Docs** |
| **Census SAHIE** | `www2.census.gov/.../sahie-2024-csv.zip` (13.6 MB) | Bulk needs no key | **2024** (released Aug 2026) | County, state | Public domain | Modeled; under 65 only. | **Live** |
| **CMS Medicare Geographic Variation** | `data.cms.gov/data-api/v1/dataset/6219697b-.../data` | No key | **2024** | National, state, county | US government (license field empty) | Fee-for-service only; small cells suppressed. | **Live** |
| **CMS Provider Data Catalog** | `data.cms.gov/provider-data/api/1/...` | No key | Rolling | Facility | US government | Roll facilities up to county. | **Live** |
| **Medicaid enrollment** | `data.medicaid.gov/api/1/datastore/query/{id}/0` | No key | Monthly (modified 2026-10-30) | State | US government | | **Live** |
| **NPPES NPI** | Monthly ZIP, about 1 GB | Free | November 2026 | Provider address | Public | Large; geocode to county ourselves. | **Live** |
| **HRSA HPSA / AHRF** | data.hrsa.gov downloads | No key | HPSA daily; AHRF 2025-12 | County, tract | "Usage limitations: None" | | **Docs** |
| **NNDSS weekly** | `x9gk-5huc` | Socrata | 2026 week 43 | State | CDC | Provisional; disease list changes. | **Live** |
| **NWSS wastewater (new series)** | `j9g8-acpt`, `ymmh-divb`, `45cq-cw4i`, `atcp-73re` … | Socrata, updated daily | Samples to 2026-11-03 | Sewershed, state | Public domain | Sewersheds can span several counties. | **Live** |
| **NHSN respiratory admissions** | `vdzy-6i9v` | Socrata | Weekly | State | Public domain | | **Live** |
| **SAMHSA NSDUH state estimates** | 2023–2024 tables | Free | Released Dec 2025 | State | Federal | Modeled; break around 2020. | **Unverified** |
| **USDA Food Environment Atlas** | XLSX/CSV | Free | Page updated 2026-08 | County | Public domain | Each variable has its own vintage. | **Docs** |
| **World Bank WDI health indicators** | `api.worldbank.org/v2/...` | No key | Updated 2026-10-08 | Country | CC BY 4.0 | | **Live** |

**Excluded on license:**

| Source | Why excluded |
|---|---|
| County Health Rankings | No commercial use without consent, and no redistribution. |
| IHME GBD | Non-commercial only. |
| WHO GHO | Public-health use only, CC BY-NC-SA. The API is also being retired. |
| Our World in Data | Series derived from those sources inherit their terms. |
| Hospital price transparency files | There is no central file, only thousands of separate hospital files. |

### MVP, ranked

1. **Drug overdose deaths: state monthly, county 12-month.**
   - The most current and most-watched federal health series, as small CSVs with ETags.
   - **Indicator:** overdose deaths per 100k by state.
2. **Uninsured rate by county, 2008–2024 (SAHIE).**
   - One keyless file a year.
   - Pairs with income and poverty on economicdata.dev.
3. **CDC PLACES county health measures.**
   - About 40 measures, such as obesity, diabetes, smoking and asthma.
   - Pairs with airdata (asthma, COPD) and climatedata (heat).
   - Label them as modeled estimates.
4. **Medicare spending and use by county, 2014–2024 (CMS).**
   - Covers health care cost, from a keyless JSON API.
5. **Respiratory surveillance this week: wastewater, admissions and deaths.**
   - Updated daily or weekly, which suits a "right now" page. visualizedata.org's /now/ page already uses waterdata and wildfire data.

**Indicators:**
- overdose deaths per 100k
- uninsured rate under 65
- adult obesity, diabetes and smoking (PLACES, age-adjusted)
- Medicare standardized spending per beneficiary
- wastewater viral activity level
- homicide and firearm deaths per 100k (shared with justicedata)
- life expectancy by country (World Bank)

**Not yet:** age-adjusted death rates and life expectancy by county or state. No current public-domain bulk source exists:
- the WONDER API is national only;
- County Health Rankings and IHME can't be republished;
- data.cdc.gov's own tables stop in 2017–2021.

A once-a-year hand pull from the WONDER web UI, with its suppression rules and credit line, is possible. It would be a manual step like the MEDSL files, not a scheduled pipeline.

### Risks

- **Disappearing and renamed CDC series.** Dataset IDs change, and landing pages lag the data.
- **License traps.** Several of the best-known sources are non-commercial or can't be redistributed.
- **Modeled data.** PLACES, SAHIE, NSDUH and SVI are model estimates and warn against comparing versions.
- **Provisional data.** Recent figures are revised for months.
- **Small-count suppression.** Rural counties will have gaps. Never back-calculate suppressed cells.
- **Geographies that aren't counties:**
  - sewersheds can span several counties;
  - VSRR reports New York City separately from New York State;
  - Connecticut moved to planning regions in 2022.

---

## 3. housingdata.dev

### Sources

| Source | Endpoint | Access | Latest | Geography | License | Notes | Status |
|---|---|---|---|---|---|---|---|
| **FHFA HPI master file** | `fhfa.gov/hpi/download/monthly/hpi_master.csv` (17 MB) | No key | Monthly to **2026-08**; quarterly to **2026 Q2** | US, division, state, MSA | Federal (terms page not read) | Revised every release. | **Live** |
| **FHFA HPI annual county, ZIP and tract** | `fhfa.gov/hpi/download/annual/hpi_at_county.xlsx` and similar | No key | **2025** (updated 2026-03-31) | County FIPS, ZIP3, ZIP5, tract | Federal | XLSX only. FHFA calls these "developmental". All-transactions, so appraisals are included. | **Live** |
| **FHFA National Mortgage Database aggregates** | Zipped CSVs | No key | 2026 Q2 | State, 100 metros (**no county**) | Federal | Built from a 5% sample. | **Docs** |
| **FHFA UAD aggregates** | Zipped CSVs | No key | **Stalled at 2024 Q3** | County, tract | Federal | Historical only. | **Docs** |
| **Census Building Permits Survey** | `www2.census.gov/econ/bps/County/co{YYMM}c.txt` | No key | Monthly to **2026-09** | County, place, CBSA, state | Public domain | Two header rows. The CBSA series breaks in Jan 2024. | **Live** |
| **New Residential Construction** | Census time-series XLS | No key | Sep 2026 | US, region | Public domain | | **Live** |
| **Housing Vacancy Survey** | XLSX tables | No key | **2026 Q3** (homeownership 65.5%) | US, region, state (annual) | Public domain | | **Docs** |
| **ACS housing tables** | Keyless summary file `www2.census.gov/.../acsdt5y2024-b25064.dat` and others | Bulk needs no key; the API needs one | 2020–2024 5-year; 2025 1-year | US, state, county, tract | Public domain | Tables: B25064 median rent, B25077 median value, B25070/B25091 cost burden, B25003 tenure, B25002/4 vacancy. 5-year windows overlap. | **Live** |
| **American Housing Survey** | PUF | — | 2023 | US and some metros | Public domain | No county data. | **Docs** |
| **HMDA (CFPB/FFIEC)** | Data Browser API `/v2/data-browser-api/view/aggregations?...`; snapshot 737 MB | No key | **2025** | Loan-level by state, county and tract | Federal | Use the API aggregates by county. | **Live** |
| **HUD Fair Market Rents and SAFMR** | `huduser.gov/hudapi/public/fmr/...` (Bearer token, 60/min) | Free token | **FY2027** (effective 2026-10-01) | FMR area, ZIP | Federal, with an attribution notice | FMR areas group counties, so a crosswalk is needed. Bulk files are behind the WAF. | API **Live** (401 without a token) |
| **HUD Income Limits, CHAS, Picture of Subsidized Households** | API or portal | Token or WAF | Income Limits FY2026; CHAS 2017–2021 | County, tract | Federal | | **Unverified** (WAF) |
| **HUD PIT/AHAR homelessness** | `2007-2025-PIT-Counts-by-State.xlsb` (behind the WAF) | WAF | **2025** (released 2026-05-29: 745,652 people) | State, CoC | Federal | 29 CoCs skipped the 2025 unsheltered count. Not available by county. | Release **Docs**; file **Unverified** |
| **HUD USPS vacancy** | Login app | Registered government or nonprofit users only | Quarterly | Tract | **Restricted** | Treat as not redistributable. | **Docs** |
| **Eviction Lab** | `data-downloads.evictionlab.org` (S3) | Open | County 2000–2018; tracking sites 2020 to now | County, tract, about 30 cities | **ODC-BY 1.0** (citation required) | County data stops in 2018. | **Live** |
| **Freddie Mac PMMS** | `freddiemac.com/pmms/docs/PMMS_history.csv` | No key | Weekly, 2026-11-05 (30-year 7.18%) | US | **Conflicting.** The PMMS page allows use "with proper attribution". The site terms forbid redistributing or caching "Data". | Ask Freddie Mac in writing first. | **Live** + **Docs** |
| **OECD house prices** | SDMX `DSD_AN_HOUSE_PRICES@DF_HOUSE_PRICES` | No key | 2026 Q2 | About 50 countries | CC BY 4.0 (via search) | | **Live** |
| **BIS residential property prices** | `data.bis.org/static/bulk/WS_SPP_csv_flat.zip` | No key | 2026 Q2 | About 60 countries | Free use, as long as it isn't charged for | | **Live** |
| **Eurostat HPI** | `prc_hpi_q` | No key | 2026 Q2 | EU/EEA | CC BY 4.0 | | **Live** |

**Can't republish:**

| Source | Terms we read |
|---|---|
| Zillow ZHVI/ZORI | Personal, non-commercial use only. |
| Redfin Data Center | Personal, non-commercial use; no automated queries; no redistribution. |
| Apartment List | Personal, non-commercial use; no robots. |
| Realtor.com | Unclear. |
| NAR | Permission needed. |
| S&P Case-Shiller | Prior written permission needed. |
| NLIHC Out of Reach | Copyrighted. We can build our own "housing wage" from FMR and wages instead. |
| Harvard JCHS | Couldn't read its terms. |

### MVP, ranked

1. **County house prices since 1975 (FHFA HPI).**
   - The annual county file plus the quarterly state, metro and US series.
   - Federal, keyless and keyed on county FIPS.
2. **Building permits by county (Census BPS).**
   - Monthly units by structure type.
   - The freshest county-level housing signal, from plain text files.
3. **ACS housing profile by county.**
   - Median rent, median value, cost burden, tenure and vacancy, from the keyless summary files.
   - Pairs with income on economicdata.dev.
4. **Mortgage lending by county (HMDA).**
   - Originations, denial rates and median loan amounts, 2018–2025.
   - From a keyless API with county filters.
5. **Homelessness by state (HUD PIT, 2007–2025) and Fair Market Rents by county.**
   - High interest, but blocked by the HUD access problem.
   - For PIT, a once-a-year hand download is fine.
   - For FMR, the token API at 60/min with a county-to-FMR-area crosswalk.

**Indicators:**
- house price growth, 1-year and 5-year
- permits per 1,000 residents
- share of permits in buildings with 5+ units
- median rent and median home value
- price-to-income ratio (ACS B25077 ÷ B19013)
- share of renters cost-burdened (paying 30%+ of income) and severely burdened (50%+)
- homeownership rate
- rental vacancy rate
- mortgage originations per 1,000 residents and denial rate (HMDA)
- homeless per 10k residents and share unsheltered (PIT, state)
- 2-bedroom FMR and our own housing wage (FMR × 12 ÷ 0.3 ÷ 2080)
- real house prices and price-to-income by country (OECD)

### Risks

- **HUD access** (see section 0).
- **Licensing:** the best-known private data, Zillow and Redfin, is off-limits.
- **Geography joins:**
  - Connecticut's 2022 planning regions;
  - FMR areas and CoCs aren't counties;
  - CBSA redefinitions in 2023 and the BPS metro break in 2024.
- **Data quality:**
  - FHFA county indexes are developmental and include appraisals;
  - PIT methods change from year to year;
  - Eviction Lab county data ends in 2018.
- **Formats:**
  - FHFA county data is XLSX only;
  - PIT is XLSB, which needs `pyxlsb`;
  - HMDA snapshots are about 737 MB, so use the API instead.

---

## 4. Recommendation

| Site | Strength | Build first | Hard part |
|---|---|---|---|
| **housingdata.dev** | Strongest. Four of its five MVP datasets are keyless federal bulk files at county level, all **Live** today. | FHFA county HPI and Census BPS permits | HUD (homelessness, FMR) |
| **healthdata.dev** | Good: current, county-level, public-domain data on data.cdc.gov and Census. | VSRR overdose deaths and SAHIE | Death rates and life expectancy below the national level |
| **justicedata.dev** | Thinnest. Federal crime data are state-level, and most NGO datasets can't be licensed. | FBI state estimates and the CDE monthly API | NIBRS comparability; no county data |

Suggested order: **housing, then health, then justice.**
- All three would add to visualizedata.org's US indicators, by county for housing and health, and by state for justice.
- The homicide and firearm death series should live on healthdata. justicedata would show it as an indicator and link to it.

**Shared groundwork, before any of the three:**
1. A Census population-estimates denominator for per-capita indicators.
2. A Socrata app token secret.
3. Engine checks that reject empty or challenge bodies, such as HUD's `202`.
4. Catalog-based dataset ID discovery for data.cdc.gov.
5. An `xlsx`/`xlsb` dependency in the pipelines.
