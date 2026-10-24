# Public data sources: economicdata.dev, electiondata.dev / votingdata.net, satellitedata.dev, visualizedata.dev

Research agent report. I checked everything on 2026-10-23 with web search and fetches. Status terms used below:
- **Live**: the endpoint answered a request today.
- **Docs**: I read the official page or terms today.
- **Unverified**: I could not confirm it.

Where I was unsure of an exact endpoint, I give the landing page. Data volumes are my estimates unless marked verified.

---

## 0. Recent changes that affect all four sites

| Item | What changed | Effect on the plan |
|---|---|---|
| ProPublica Congress API | Shut down. The docs remain only as an archive and new keys are not issued. GovTrack also ended its API and bulk data. | This leaves a gap that votingdata.net can fill. |
| Google Civic Information API | The Representatives endpoint was turned down in April 2025 (a third party gives Apr 30). The Elections and Divisions APIs were kept, per Google's notice; I did not re-check them for 2026. | Use it only for OCD-ID and division lookups. |
| UCS Satellite Database | Updates are paused, with data current to 2023-05-01. UCS says it is "evaluating plans for resuming." | Treat it as historical only. |
| CelesTrak | Policy: one download per data update, enforced since 2026-03-26; repeat requests get HTTP 403. CSV became the default format on 2026-05-09. Five-digit catalog numbers ran out on 2026-07-11, and TLE cannot hold the new numbers. | Use OMM in JSON or CSV, not TLE. |
| US federal statistics, 2025–26 | Oct 1 – Nov 12, 2025 shutdown: October 2025 CPI was never published, and the October household survey was not collected. A partial funding lapse from Feb 1, 2026 delayed the January 2026 jobs report. The current stopgap funding runs only to **Dec 11, 2026**. | Expect gaps and release-date slips. Flag them in the data. |
| BLS | CPI price collection stopped in Lincoln NE and Provo UT (Apr 2025) and Buffalo NY (Jun 2025); about 15% of the sample was cut in 72 other areas. About 350 PPI indexes were discontinued with the July 2025 data (released 2025-08-14). LAUS 2025 annual averages use only 11 months. | Subnational CPI is now noisier, and 2025 LAUS averages break year-over-year comparisons. |
| BEA | Many national and international tables were discontinued in 2025. On 2026-02-05, county and metro tables (CAINC, CAGDP, BEARFACTS) were marked "impacted" by new statistical-area definitions. Earlier cuts (2024): CAEMP25 county employment by industry, CAINC35 transfer receipts, CAINC45 farm income. Five satellite accounts are no longer regularly produced (Feb–Mar 2026). | Use QCEW for county employment by industry. |
| Census | An API key has been required on every query since **2026-05-12**. The 2025 ACS 1-year release was pushed from September to **2026-10-29**, tied to a new Commerce disclosure-avoidance order (DAO 216-26). No date is set for the 2021–2025 ACS 5-year release. | Update the pipelines and watch whether the new order changes comparability. |
| Federal Reserve Board Data Download Program | "Build Your Package" will be removed the week of Nov 9; the release pages were updated 2026-10-19, so this is almost certainly 2026. The Board says the whole program will be retired later. | Use the XML/SDMX files on each release page, or FRED. |
| IMF | The legacy data portal was retired 2025-11-05. The new SDMX 3.0 portal is data.imf.org. IFS is no longer a single dataset, and old series codes do not map to new ones. data.imf.org has maintenance scheduled Oct 24–26, 2026. | Expect a full rebuild of any IMF pipeline. |
| Mid-decade redistricting | NCSL lists 10 states with new congressional maps since summer 2025: AL, CA, FL, LA, MO, NC, OH, TN, TX, UT. Louisiana is redrawing after the *Callais* ruling. | The Census 119th-Congress district files will not match 2026 districts in those states. |

---

## 1. economicdata.dev

### Sources

| Source (publisher) | URL | Access | Formats | Update frequency | Coverage | License / commercial use | Status | Good for |
|---|---|---|---|---|---|---|---|---|
| BLS Public Data API, flat files and QCEW (US Bureau of Labor Statistics) | https://www.bls.gov/developers/ · bulk: https://download.bls.gov/pub/time.series/ · QCEW: https://www.bls.gov/cew/additional-resources/open-data/ | REST with free key (v2): 500 queries/day, 50 series and 20 years per query, 50 requests per 10 s. Flat files and QCEW CSV slices need no key. | JSON, TSV, CSV | Monthly (CPI, payrolls, LAUS). QCEW quarterly, about 5–6 months behind. | US, states, metros, counties | Public domain; commercial use OK | Docs; 2025 data gaps | County and metro unemployment (LAUS); county employment and wages by industry (QCEW) |
| BEA API (Bureau of Economic Analysis) | https://apps.bea.gov/api/signup/ · guide: https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf | REST with free UserID. Limits: 100 requests/min, 100 MB/min, 30 errors/min. Over the limit you get HTTP 429 with Retry-After (guide dated 2026-04-20). | JSON, XML | GDP quarterly; state data quarterly; county data annual (2024 county figures came out 2026-02-05) | US, states, counties, metros (the "Regional" dataset) | Public domain | Docs; tables cut 2024–26 | GDP, personal income, regional price parities |
| Census Data API (US Census Bureau) | https://www.census.gov/data/developers.html | REST; key required since 2026-05-12 | JSON | ACS yearly (2025 1-year on 10-29); CBP yearly (2023 data released 2025-06-26) | Down to tract and block group, and ZIP (CBP) | Public domain | Docs | Population and income denominators; business counts |
| FRED / ALFRED (Federal Reserve Bank of St. Louis) | https://fred.stlouisfed.org/docs/api/fred/ · terms: https://fred.stlouisfed.org/docs/api/terms_of_use.html | REST with free key. About 120 requests/min according to third parties; I did not find this in official docs. | JSON, XML | Follows each source | US and international series, plus past data versions (ALFRED) | Terms forbid apps that "replicate or attempt to replace" FRED. Third-party "Copyright" series need the owner's permission. A non-endorsement notice is required. "FRED" may not appear in your hostname. | Docs | Benchmarking and quality checks only; **not a source to redistribute** |
| World Bank Indicators API v2 | https://api.worldbank.org/v2/ · docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api | REST, no key | JSON, XML | Several times a year (US GDP series last updated 2026-10-08) | About 16,000 indicators across 45+ databases | CC BY 4.0 by default; a few datasets differ | Live | Annual backbone for all countries. The Bank is rebranding its portal as "Data360"; the v2 API is unaffected so far. |
| IMF SDMX API | https://data.imf.org/en/Resource-Pages/IMF-API | REST (SDMX 2.1 and 3.0). The Swagger page mentions a portal account; key and rate-limit rules are unclear. | SDMX (XML/JSON/CSV) | Per dataset; World Economic Outlook in April and October | About 190 countries | Reuse allowed with attribution, but **"for any potential commercial reuse… request permission"** (copyright@imf.org). Automated bulk download needs permission. No LLM training without permission. If you sell IMF data as a standalone product, you must tell buyers it is free from the IMF. | Changed (see section 0) | WEO forecasts; balance of payments; government finance |
| OECD Data Explorer SDMX API | https://sdmx.oecd.org/public/rest/ | REST, no key. Rate-limited since Nov 2024: one OECD page says 20 queries/min and 20 downloads/hr, another says 60 downloads/hr. Traffic from VPNs and anonymizers is blocked. | SDMX-ML, SDMX-JSON, CSV | Monthly or quarterly | OECD members and partners | CC BY 4.0 for content from 2024-07-01; earlier content falls under OECD terms similar to CC BY. Third-party material is excluded. | Live (a dataflow query worked) | Comparable quarterly GDP, CPI and unemployment |
| Eurostat API (European Commission) | https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction | REST: Statistics API (JSON-stat), SDMX 2.1 and 3.0, plus asynchronous mode for large pulls; no key | JSON-stat, SDMX-CSV, TSV | Daily release calendar | EU, EFTA and candidate countries; NUTS 1–3 regions | Commercial reuse OK with source credit, **but data for non-EU/EFTA/candidate countries (US, JP, CN…) and data from non-Eurostat sources must be removed before commercial reuse** | Docs | NUTS regional data (Europe's equivalent of county data); HICP inflation |
| ECB Data Portal API (European Central Bank) | https://data.ecb.europa.eu/help/api/overview | REST (SDMX 2.1), no key | CSV, SDMX-JSON/XML | Daily (exchange and interest rates), monthly | Euro area and EU | Free reuse including commercial, citing "Source: ECB statistics". **The statistics must not be modified.** Third-party data excluded. | **Unverified**: HTTP 503 on two attempts today | Euro reference exchange rates, policy rates, money supply |
| BIS Data Portal API (Bank for International Settlements) | https://data.bis.org · API docs: https://stats.bis.org/api-doc/v2/ | REST (SDMX); no authentication reported | JSON, XML, CSV | Daily to quarterly | About 60 central banks and economies | Use is "unrestricted" with a citation, **but a commercial product may include BIS data only if this does "not result in any additional charge."** No Russian data after 2022-02-28. | Docs | Central-bank policy rates, credit-to-GDP, property prices |

Also worth knowing:
- **UNdata / UNSD**: https://data.un.org/ws/rest/. Data may be copied and redistributed if "UNdata" is cited. The separate SDG API is labelled a "TEST API."
- **DBnomics**: https://db.nomics.world. No key, 90+ providers. It is both a competitor and a useful cross-check.
- **National statistics offices**: Statistics Canada's Web Data Service runs under its Open Licence, with attribution required. The UK ONS shut its PublishMyData service on 2025-03-31 and is retiring its v0 API in favour of v1.

### MVP: US Regional Economic Panel v1
- **What it is:** one dataset covering every county, metro and state:
  - Monthly: labor force, employment and unemployment rate (LAUS).
  - Quarterly: employment and average weekly wage by ownership and broad industry group (QCEW).
  - Annual: personal income, per-capita income and GDP (BEA); population (Census population estimates); median household income and poverty (ACS 5-year); establishments (CBP).
- **Why it comes first:**
  - FRED carries these figures but as thousands of separate series (one per county per measure), and its terms bar copying it. A panel-shaped bulk product fills the real gap.
  - Every input is US public domain, so paid tiers carry no licensing risk.
  - Harmonization is valuable right now, because of the BEA geography change of 2026-02-05, Connecticut's planning regions and the 2025 data gaps.
  - It feeds the first maps on visualizedata.dev.
- **Next product:** a "Global Macro Core" covering GDP, CPI, unemployment, policy rate and population for about 200 countries, built from World Bank, OECD, Eurostat (EU only for paid use) and ECB. Add BIS to the free tier only, and add IMF after getting written permission.

### Value-add
- **One tidy schema:** `geo_id` (Census GEOID), `geo_level`, `geo_vintage`, `indicator`, `period`, `freq`, `value`, `unit`, `seasonal_adj`, `source`, `release_date`, `vintage`.
- **Geography crosswalks by vintage:**
  - Connecticut's 8 counties became 9 planning regions.
  - Virginia independent cities and Alaska boroughs need special handling.
  - Metro totals are recomputed on 2023 metro definitions.
- **Units:** BEA reports county figures in thousands of dollars and metro figures in millions; normalize both.
- **Derived values:** per-capita figures, real dollars (using regional price parities), year-over-year change, and rankings.
- **Gap flags:** October 2025 has no LAUS or CPI values; 2025 LAUS averages use 11 months; three cities have suspended CPI collection.
- **Release history:** keep every vintage so users can query the data "as of" a date. This is a natural paid feature.

### Volume and cadence
- **Size:** about 20M observations for 1990/2000 to today, roughly 200–500 MB as Parquet or about 2 GB as CSV (estimate). New data adds less than 50 MB a month.
- **Cadence:**
  - LAUS monthly: September 2026 state data came out Oct 20, metro data Oct 28.
  - QCEW quarterly.
  - BEA county annual, BEA state quarterly.
  - ACS and CBP annual.

### Gotchas
- **Federal funding:** the current stopgap runs to Dec 11, 2026. If it lapses, releases slip, as they did in Oct–Nov 2025 and Feb 2026. Watch release calendars and mark records as "delayed."
- **Rate limits:** BLS v2 allows 500 queries a day, so do bulk loads from the flat files. BEA allows 100 requests a minute. Census now needs a key and may rate-limit by IP.
- **Licensing traps:**
  - FRED's no-replication clause.
  - IMF needs permission for commercial use.
  - BIS forbids extra charges for its data.
  - Eurostat requires removing non-EU countries for commercial use.
  - ECB forbids modifying its statistics, so publish derived values as separate, labelled fields.
  - OECD and other third-party material is excluded from open licences.
- **OECD access:** it blocks VPN and anonymized traffic, so run scheduled pulls from a stable, identified egress IP.
- **Attribution:** keep a `license` and `attribution` field on every series and every bulk file.

---

## 2. electiondata.dev and votingdata.net

### 2a. Election results sources (electiondata.dev)

| Source (publisher) | URL | Access | Formats | Update frequency | Coverage | License / commercial use | Status | Good for |
|---|---|---|---|---|---|---|---|---|
| State election offices and county results sites (official) | No single URL: each Secretary of State's results page, plus county election-night sites, many run by vendors (e.g. Clarity) | Web pages and downloads; some JSON/XML feeds | CSV, XLSX, XML, JSON, PDF | Election night (unofficial), then canvass, then certification | Every office, down to precinct | Public records; site terms on automated access vary | Formats change every cycle | **The only source of certified 2026 results** |
| MIT Election Data + Science Lab (MEDSL) | https://electionlab.mit.edu/data · https://dataverse.harvard.edu/dataverse/2024_precincts · https://github.com/MEDSL/2024-elections-official | Bulk download (Dataverse API, GitHub) | CSV plus codebook | After certification. National 2024 precinct files were published **2026-05-01**; county presidential file v20 on 2026-02-25. | President by county 2000–2024; House and Senate 1976–2024; precinct 2016–2024 | **CC0 1.0**, verified for the county-presidential and 2024 precinct President datasets. The GitHub repo has no license file. | Live | Clean historical baseline; a precinct schema worth copying |
| OpenElections | https://github.com/openelections | GitHub bulk: `openelections-data-XX` (cleaned) and `openelections-sources-XX` (raw) | CSV | Rolling, run by volunteers (repos updated Oct 2026) | Precinct and county; uneven by state | **No organization-wide license**; says "free to anyone"; at least one repo is MIT | Live; license unclear | Precinct cross-checks; raw official files |
| VEST precinct shapefiles (Harvard Dataverse) and Redistricting Data Hub | Example VEST record: doi:10.7910/DVN/XPW7T7 · https://redistrictingdatahub.org | Download | Shapefile | Each cycle | Precinct boundaries joined to results | License not confirmed. RDH re-hosts partner data that may carry the partners' own terms. | Unverified | Precinct maps |
| Census CPS Voting and Registration Supplement | https://www.census.gov/data/tables/time-series/demo/voting-and-registration/p20-590.html | Tables and microdata (also via Census API) | XLSX, CSV | Every two years. 2024 tables: 2025-04-30. 2022 tables: 2023-05-02. | National and state, by demographic group (self-reported) | Public domain | Live | Turnout and registration by age, race and education |
| EAC Election Administration and Voting Survey (EAVS) | https://www.eac.gov/research-and-data/datasets-codebooks-and-surveys | Bulk download | XLSX, CSV, Stata, SPSS, SAS | Every two years. 2024 v1.0 released 2025-06-30; v2.0 2026-02-12. | Each local jurisdiction: registrations, ballots cast, mail and provisional ballots, overseas/military voters | Public domain | Docs | Administrative turnout and mail-voting figures |
| FEC OpenFEC API and bulk files | https://api.open.fec.gov/developers/ · rules: https://www.fec.gov/updates/sale-or-use-contributor-information | REST with api.data.gov key: 1,000 calls/hr, 120/min on request (figures from mirrored docs); bulk files | JSON, bulk TXT/ZIP | Daily | Federal candidates and committees; money raised and spent | Public domain, **but individual contributors' names and addresses may not be sold or used for any commercial purpose or for soliciting donations** (52 U.S.C. §30111(a)(4); 11 CFR 104.15) | Docs page exists; limits not re-checked | Candidate IDs, committee totals, independent spending |
| Google Civic Information API | https://developers.google.com/civic-information | REST with Google key | JSON | Per election | Elections and divisions only | Google terms; caching limited to 24 h (polling places and contests) and 30 days (districts) | Representatives endpoint gone | OCD-ID lookups |

Avoid:
- **ProPublica Congress API** (shut down) and **GovTrack's API** (ended).
- **UF Election Lab**: 2022 and 2024 precinct data are paid, and the free archive is CC BY-NC-ND, so it cannot be used commercially.
- **AP Elections API, Decision Desk HQ, Ballotpedia, Dave Leip's Atlas**: commercial products. I did not check their terms in this session; do not scrape or redistribute them.

### When 2026 midterm results become available (election: Nov 3, 2026)

| When | What |
|---|---|
| Nov 3 | Unofficial results on state and county results sites |
| Within 1 week (15 states) or by the 2nd week (21 more) | County canvass deadlines (NCSL). Examples: Michigan county boards must meet by Nov 5 and finish by Nov 17. Texas counties canvass between Nov 6 and Nov 17. Idaho now allows 9 days (about Nov 12) under a 2026 law. |
| Nov 19–23 | Arizona: counties by Nov 19, Secretary of State by Nov 23. Michigan state board by Nov 23. Nebraska: counties report to the state by Nov 23. |
| Late Nov to mid-Dec | Other states. NCSL gives a range from the Friday after the election (Oklahoma) to the 2nd Tuesday of December (Missouri, Dec 8). California: counties 30 days (Dec 3), Secretary of State 38 days (Dec 11); these are statutory and I did not re-check them. Pennsylvania and Rhode Island have no deadline for the final state step. |
| Dec 3 | FEC post-general campaign finance reports due (30 days after the election) |
| Nov–Dec 2026 | Most counties publish precinct-level certified results when they certify; formats vary |
| Months | OpenElections adds states on a rolling basis |
| ~2027–28 | MEDSL national precinct files: the 2024 files took about 18 months. MEDSL says it hopes to automate and shorten this. |
| ~Apr–May 2027 | CPS 2026 voting tables, going by the 2022 and 2024 dates |
| ~Jun 2027 | EAVS 2026 report and data, going by the 2024 cycle |

### MVP: certified 2026 general election results by county
- **What it is:** certified county-level results for every federal and statewide race in all 50 states plus DC, joined to MEDSL's county history for 2000–2024. Ship an unofficial version on election night, then replace it state by state as each state certifies.
- **Why it comes first:**
  - Results arrive mid-November to mid-December 2026, and the nearest free clean national files have lagged by about 18 months (MEDSL 2024 precinct).
  - About 3,150 counties plus New England towns is a manageable scope; there are about 200,000 precincts.
  - Licensing is clean: official records plus MEDSL CC0.
- **Sources:**
  - State and county certified canvass reports (primary).
  - Results sites for the unofficial phase.
  - MEDSL (history).
  - OpenElections (cross-checks).
  - EAVS and ACS citizen voting-age population (turnout denominators).
  - FEC candidate IDs.
- **Next product:** precinct-level results joined to precinct shapes.

### Value-add
- **One schema:** `election_date`, `state_fips`, `county_fips`, `office`, `district`, `candidate`, `party_detailed`, `party_simplified`, `mode`, `votes`, `status` (unofficial or certified), `certified_at`, `source_url`, `retrieved_at`.
- **Candidate IDs:** crosswalk names to FEC, Bioguide and OCD IDs.
- **Edge cases handled:**
  - Fusion voting in NY and CT: keep each ballot line and also give a combined total.
  - Ranked-choice rounds in Maine and Alaska.
  - Write-ins.
  - New England towns rolled up to counties.
  - Alaska's districts and Virginia's independent cities.
- **Turnout:** ballots divided by the citizen voting-age population.
- **Version history:** keep every change from unofficial to certified, with diffs.

### Volume and cadence
- **2026 county file:** about 50–100k rows, under 10 MB.
- **County history 2000–2024:** tens of MB.
- **Precinct phase:** MEDSL's 2024 President precinct CSV alone is **399.6 MB** (verified). All offices together will run to several GB as CSV; Parquet is far smaller.
- **Cadence:** every few minutes on election night, daily during the canvass, then final with corrections only.

### Gotchas
- **Do not publish voter registration files.** They contain personal information, and many states limit use to election or political purposes or ban commercial use and republication. Examples I confirmed:
  - **California**: commercial use is barred, online republication is restricted, and violation is a misdemeanor.
  - **Arizona**: non-commercial use only; the list may not be posted online.
  - **Kansas**: commercial use is a class C misdemeanor.
  - **Kentucky**: "commercial use" is defined broadly, with exceptions for scholarly, journalistic and political use.
  - Even states with open lists (e.g. Alaska, Colorado) would leave a national site distributing personal data.
  - The 2025–26 Justice Department suits demanding unredacted voter rolls (about 30 states plus DC) make this a live legal and political issue.
  - Publish only **aggregate** registration counts, from Secretary of State statistics or EAVS.
- **Redistricting:** 10 states have new congressional maps (section 0). Do not compare 2024 and 2026 at the district level without the new district shapes, which come from the states. The Census 119th-Congress files are out of date for those states.
- **Results change:** certification disputes, recounts and court contests can move numbers after a state certifies. Keep amendment history.
- **Licensing:** MEDSL CC0 is safe. OpenElections' license is unclear, so ask Derek Willis or limit its use to cross-checks. UF data is non-commercial. FEC contributor rules apply: publish committee-level totals, and never sell or rent itemized donor lists.
- **Messy details:** precinct names and boundaries change every cycle and are not stable IDs. MEDSL marks suppressed counts as `votes="*"`. On election night, crawl county sites politely with an identifying User-Agent, and label unofficial and certified results clearly to avoid spreading misinformation.

### 2b. Roll-call vote sources (votingdata.net)

| Source (publisher) | URL | Access | Formats | Update frequency | Coverage | License / commercial use | Status | Good for |
|---|---|---|---|---|---|---|---|---|
| Congress.gov API (Library of Congress) | https://api.congress.gov/ · https://github.com/LibraryOfCongress/api.congress.gov | REST with api.data.gov key; **5,000 requests/hr** | JSON, XML | Continuous | Bills, members, committees. House roll-call votes from the 118th Congress, with the 117th added Apr 2026. | Public domain | Docs. The House vote endpoints left beta in Dec 2025. **There is no Senate vote endpoint.** | Bill metadata and House votes |
| House Clerk roll-call XML | Pattern: https://clerk.house.gov/evs/{year}/roll{NNN}.xml (roll 1 of 2025 checked) | Static XML | XML | Minutes after each vote | House floor votes | Public domain | Live | The authoritative House record |
| Senate roll-call XML (LIS) | Index: https://www.senate.gov/general/XML.htm | Static XML | XML | Same day | Senate votes | Public domain | **My automated fetch got HTTP 403**, so expect bot protection | The only official machine-readable Senate source |
| Voteview (Lewis, Poole, Rosenthal et al.) | https://voteview.com/data | Bulk download | CSV, JSON | Updated as votes are taken | 1st–119th Congress, both chambers: members, roll calls, votes, parties, NOMINATE ideology scores | **No license stated**; asks for a citation | Live | Full history and ideology scores |
| unitedstates/congress-legislators | https://github.com/unitedstates/congress-legislators | GitHub | YAML, JSON, CSV | Periodic | Every member 1789–present; ID crosswalk (Bioguide, ICPSR, FEC, GovTrack, LIS…); committees | **CC0** | Docs | The ID backbone |
| Open States / Plural Open | API: https://docs.openstates.org/api-v3/ · bulk: https://open.pluralpolicy.com/data/ | REST v3 with free key (from open.pluralpolicy.com; GraphQL deprecated); bulk downloads | JSON, CSV, YAML | Bulk monthly | 50 states plus DC and PR: bills, votes, legislators | Bulk data dedicated to the public domain unless noted | Docs; v3 still labelled beta | State legislative roll calls |
| LegiScan | https://legiscan.com/legiscan | REST with free key: 30,000 queries/month (another LegiScan page says 10,000); paid tiers; weekly datasets | JSON, CSV | Daily | 50 states and Congress | **CC BY 4.0** | Docs | Cross-checking and filling gaps in state votes |

### MVP: congressional roll-call API, 1789 to today
- **What it is:** one row per member per vote, joined to the bill or amendment it concerns and to member IDs. Each member also gets attendance, party-unity and votes-with-majority statistics.
- **Why it comes first:**
  - ProPublica's API and GovTrack's API, the developer defaults, are both gone.
  - Congress.gov has no Senate votes.
  - The data is public domain or CC0 and small.
  - Daily updates make it worth paying for.
- **Sources:**
  - House Clerk and Senate XML for current votes.
  - Congress.gov for bill metadata.
  - congress-legislators for IDs.
  - Voteview for history and NOMINATE scores, with citation; ask the authors before charging for it.
- **Next product:** state legislatures, from Open States bulk data plus LegiScan.
- **Volume:** roughly 25–30M member-vote rows over all history (estimate), about 0.5–1 GB as CSV or about 100 MB as Parquet. Each year adds under 1M rows.
- **Cadence:** hourly while Congress is in session, daily otherwise.
- **Gotchas:**
  - Voteview has no license.
  - senate.gov blocks bots.
  - Vote codes differ across sources (Yea/Aye/Yes, Present, Not Voting).
  - House delegates vote only in the Committee of the Whole.
  - Quorum calls count as roll calls (roll 1 of the 119th Congress was "Call by States").
  - Voice votes are not recorded.
  - Open States marks committees and events as experimental.

---

## 3. satellitedata.dev

### 3a. Earth-observation catalogs (search and derived products, not hosting imagery)

| Source (publisher) | URL | Access | Formats | Update frequency | Coverage | License / commercial use | Status | Good for |
|---|---|---|---|---|---|---|---|---|
| Earth Search v1 (Element 84, on AWS Open Data) | https://earth-search.aws.element84.com/v1 | STAC API, no key; S3 in us-west-2 | STAC JSON, COG | Sentinel-2 within hours of Copernicus | 9 collections: sentinel-2-l2a, -c1-l2a, -l1c, -pre-c1-l2a, sentinel-1-grd, landsat-c2-l2, naip, cop-dem-glo-30/90 | Each source's license (Copernicus free/open; Landsat public domain) | **Live** | The cheapest way to index Sentinel-2 and Landsat imagery files (COGs) |
| Microsoft Planetary Computer | https://planetarycomputer.microsoft.com/api/stac/v1 | STAC API, no key; reading files requires a signed access (SAS) token | STAC, COG, Zarr, GeoParquet | Varies by collection | Large multi-domain catalog | **License set per dataset** | **Live.** Its Hub closed in 2024. Maintenance notices Sep–Oct 2026; an Apr 2026 report of "significant delay" for Sentinel-2 and Landsat. | Breadth (climate reanalysis, land cover, elevation) |
| NASA CMR and CMR-STAC | https://cmr.earthdata.nasa.gov/stac (search: https://cmr.earthdata.nasa.gov/search) | Search needs no key. Downloads need a free Earthdata Login account. Direct S3 access only from us-west-2. | STAC, UMM JSON, NetCDF/HDF/COG | Continuous | 60 provider catalogs (LPCLOUD, PODAAC, ASF, GHRC…) | NASA mission data is CC0 unless marked; partner data may differ | **Live** | Harmonized Landsat-Sentinel (HLS), MODIS/VIIRS, radar (ASF) |
| Copernicus Data Space Ecosystem | STAC: https://stac.dataspace.copernicus.eu/v1 · docs: https://documentation.dataspace.copernicus.eu/APIs/newSTACcatalogue.html | STAC, OData, Sentinel Hub and openEO. Free account for downloads. 12 TB/month transfer, then throttled to 1 MB/s. | STAC, SAFE, COG | Near real time | All Sentinel missions and Copernicus services | Free, full and open, including redistribution. Required credit: "Copernicus Sentinel data [Year]" or "Contains modified Copernicus Sentinel data [Year]" | Docs. The old `catalogue.dataspace.copernicus.eu/stac` endpoint is deprecated. | The authoritative Sentinel-1, -3 and -5P catalog |
| USGS Landsat (EROS) | STAC: https://landsatlook.usgs.gov/stac-server · M2M tokens: https://www.usgs.gov/media/files/m2m-application-token-documentation | STAC with no key. M2M API needs an account; password login ended 2025-02-26 and application tokens replace it. AWS bucket `usgs-landsat` is requester-pays. | STAC 1.1.0, COG | Daily | Landsat 1–9, Collection 2 | Public domain | STAC landing page answered | The authoritative Landsat source |
| Sentinel-2 imagery on AWS (Element 84) | https://registry.opendata.aws/sentinel-2-l2a-cogs/ | S3 `sentinel-cogs` (us-west-2), no AWS account needed; a separate bucket for the newer Collection 1 | COG plus STAC items | Hours after Copernicus | Global Sentinel-2 L2A | Copernicus terms | Docs | Free reads for derived products computed in us-west-2 |

### 3b. Data about satellites

| Source (publisher) | URL | Access | Formats | Update frequency | Coverage | License / commercial use | Status | Good for |
|---|---|---|---|---|---|---|---|---|
| CelesTrak orbital data (T.S. Kelso) | https://celestrak.org/NORAD/documentation/gp-data-formats.php · policy: https://celestrak.org/usage-policy.php | `gp.php?{GROUP\|CATNR\|INTDES\|NAME\|SPECIAL}=…&FORMAT=…`, no key | CSV (default), JSON, OMM XML/KVN; TLE/3LE only for numbers that fit TLE | About every 2 h; satellite catalog 1–2 times a day | Public catalog groups; supplemental orbit data from operators | **No explicit redistribution terms.** One download per update (HTTP 403 on repeats). Stop on any non-200 response; more than 50 errors in 2 h gets your IP firewalled. | **Live; changed** (section 0) | The easiest feed for orbits and the catalog |
| Space-Track.org (US Space Force / USSPACECOM) | https://www.space-track.org/documentation | REST query API; free account | JSON, CSV, XML, TLE/OMM | Continuous; pull orbit data at most once an hour, at a random minute | Full public catalog: orbits, satellite catalog, decays, reentry predictions | The user agreement bars transferring data without approval, **but USSPACECOM gives blanket approval to redistribute "basic SSA data" (orbit data, satellite catalog, decay and reentry data) if cited** | Docs; under 30 requests/min and 300/hr | The authoritative source, including orbit history |
| GCAT (Jonathan McDowell) | https://planet4589.org/space/gcat/ | Bulk files | TSV, text, tar.gz | Release 1.8.8 on 2026-09-03; data update 2026-10-24 | Every artificial object, including failed launches; launch log, organizations, vehicles, sites; `satcat100k` for the new 6-digit numbers | **CC BY 4.0.** Cite "data from GCAT (J. McDowell, planet4589.org/space/gcat)" | **Live** | Owner, operator, mass and type details |
| UCS Satellite Database | https://www.ucs.org/resources/satellite-database | Download | XLSX, TXT | **Paused**; data through 2023-05-01 | About 7,560 operating satellites (May 2023) | No license stated | **At risk / stale** | Historical purpose and user fields only |
| Launch Library 2 (The Space Devs) | https://ll.thespacedevs.com/2.3.0/ · https://thespacedevs.com/llapi | REST: 15 requests/hr without a key; more through Patreon | JSON | Near real time | Launches, providers, vehicles, upcoming launches | **No license stated** ("accessible to everyone, for free"). Ask before commercial use. | Docs | Launch schedule and history |
| SatNOGS DB (Libre Space Foundation) | https://db.satnogs.org | REST, open | JSON | Community-maintained | Satellites and radio transmitters | **CC BY-SA 4.0** (share-alike) | Docs | Radio frequencies and modes |

Also:
- **Aerospace Corp. reentry database** (https://aerospace.org/reentries): predictions, plus CSV for older data. I did not check its terms.
- **TraCSS** (Commerce Department space-traffic system): the FY2027 budget request does not fund it, so it is at risk.

### MVP: a unified satellite catalog API
- **What it is:** one record per object, with:
  - Catalog number (ready for 9 digits), international designator and GCAT ID.
  - Owner, operator and country.
  - Status (active or decayed), launch and decay dates.
  - Orbit class, perigee, apogee, inclination and period.
  - Constellation.
  - Latest orbit data as OMM, plus TLE where the number fits.
  - A daily changelog of new objects, decays and big orbit changes.
- **Why it comes first:**
  - The July 2026 move to 6-digit catalog numbers broke TLE-based tools.
  - UCS has been frozen since May 2023, so nobody offers a current "who owns it and what does it do" catalog.
  - The data is small, and the licensing is clear: Space-Track's blanket approval with citation, and GCAT CC BY 4.0.
- **Sources:**
  - Space-Track (catalog, orbits, decays).
  - CelesTrak, as a secondary orbit feed.
  - GCAT (enrichment).
  - UCS (historical fields, flagged as stale).
  - Launch Library 2 (launch links, after asking).
  - SatNOGS (transmitters, kept as a separate CC BY-SA dataset).
- **EO product after that:** a cross-provider scene index, in stac-geoparquet, of Sentinel-2 L2A, Landsat C2 L2 and HLS. Fields would be harmonized: cloud cover, tile and path/row, processing baseline, and links to the same scene on AWS, Planetary Computer, Copernicus and NASA. A derived product would follow: monthly clear-sky scene availability for each admin region. Have satellitedata.net and remotesensing.app use this .dev site as their data and API layer.

### Volume and cadence
- **Catalog:** about 65–70k objects, roughly 10–20 MB as CSV.
- **Current orbits:** about 30k objects on orbit, about 10 MB as CSV or about 30 MB as JSON.
- **GCAT:** tens of MB.
- **Daily orbit snapshots:** about 1–3 GB per year as Parquet.
- **MVP total:** under 200 MB.
- **Cadence:**
  - Orbits: Space-Track hourly at a random minute, or CelesTrak every 2 h, one pull per update.
  - Catalog: daily.
  - GCAT: about weekly.
  - Launch Library 2: hourly at most, within 15 requests/hr.
- **EO scene index:** tens of GB of metadata (estimate).

### Gotchas
- **CelesTrak:** follow the one-download-per-update rule and stop on any error. TLE cannot carry catalog numbers above 5 digits. CelesTrak states no redistribution terms, so treat Space-Track as the licensed source of record. Supplemental operator-supplied orbit data may carry other terms (unverified).
- **Space-Track:** one account per user, never share credentials, and batch queries rather than asking satellite by satellite. The blanket approval covers only basic SSA data; conjunction data and similar are excluded. Citation is required.
- **UCS and Launch Library 2:** neither states a license. Get written permission before putting their data in paid tiers.
- **SatNOGS share-alike:** do not merge it into a proprietary dataset.
- **EO licensing and credit:** use the Copernicus credit line. Planetary Computer licenses vary by dataset. NASA partner data can differ from NASA's CC0 default.
- **EO costs and access:** Landsat on AWS charges the requester for reads. Copernicus throttles you after 12 TB a month. USGS M2M needs application tokens. Planetary Computer file access needs a SAS token.
- **Budget risk:** NASA's FY2026 request proposed cutting Earth science by about 50% and ending the OCO carbon-observing missions. Congress resisted, but I could not confirm the final outcome. Build failover across mirrors.

---

## 4. visualizedata.dev

### Sources

| Source (publisher) | URL | Access | Formats | Update frequency | Coverage | License / commercial use | Status | Good for |
|---|---|---|---|---|---|---|---|---|
| Our World in Data | https://docs.owid.io/projects/etl/api/chart-api/ | Chart API: add `.csv`, `.metadata.json` or `.zip` to `/grapher/{slug}` (`csvType=full\|filtered`). Tables, Indicators and Search APIs are "under active development." | CSV, JSON | Per chart | Thousands of charts, mostly country-year | CC BY 4.0, **but some charts return HTTP 403 "Data is non-redistributable."** Credit the original source too. | Docs | Ready-made gallery content with units and citations |
| World Bank World Development Indicators | https://api.worldbank.org/v2/ | REST, no key | JSON, XML | Several times a year | All countries, from 1960 | CC BY 4.0 by default | **Live** | Country-year backbone |
| UNdata (UN Statistics Division) | https://data.un.org · terms: https://data.un.org/Host.aspx?Content=UNdataUse · SDMX: https://data.un.org/ws/rest/ | Web and SDMX REST | CSV, XML | Varies | UN statistical databases | "May be copied freely… and further distributed" if UNdata is cited; commercial use is not mentioned either way | Docs | Population, national accounts, SDG indicators |
| Gapminder (Systema Globalis, published by Open Numbers on GitHub) | https://www.gapminder.org/data/ | Bulk GitHub repositories | CSV (DDF format) | Irregular | Long-run country series | Free reuse with credit to Gapminder and the original source; Gapminder material is CC BY 4.0 (I did not check the repo's own license) | Docs | Long historical series for animated bubble charts |
| Natural Earth | https://www.naturalearthdata.com | Download | Shapefile (also mirrored on GitHub) | Rarely; v5.x, latest tags 5.1/5.2 from 2022 per a mirror's changelog | World countries, first-level regions, coastlines, at 1:10m, 1:50m and 1:110m | **Public domain**; no credit needed | Docs | Small-scale world maps; versions with alternative points of view on disputed borders |
| US Census cartographic boundary files and TIGER/Line | https://www2.census.gov/geo/tiger/GENZ2025/ · https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html | Bulk download | Shapefile, KML, GPKG, GDB | Yearly: 2025 cartographic files posted 2026-04-23; 2025 TIGER released 2025-09-23 | US from nation down to tract; metros, congressional and state legislative districts, ZIP-code areas | Public domain | **Live** | US maps keyed by GEOID |
| geoBoundaries (William & Mary geoLab) | https://www.geoboundaries.org/api.html | `/api/current/{gbOpen\|gbHumanitarian\|gbAuthoritative}/{ISO3}/{ADM0-5}/` returns file links | GeoJSON (full and simplified), TopoJSON, ZIP | As needed | Admin levels 0–5 for nearly every country | **gbOpen: CC BY 4.0. gbAuthoritative: no commercial use. gbHumanitarian: varies.** Check the `boundaryLicense` field on each layer. | Docs | Worldwide first- and second-level regions you can use commercially |
| Overture Maps Divisions | https://docs.overturemaps.org/attribution/ | Bulk GeoParquet | GeoParquet | Monthly releases (latest seen 2026-08-19) | Countries down to neighborhoods; `admin_level` field added Feb 2026 | **ODbL (share-alike)**, plus CC BY sources (geoBoundaries, Esri Community Maps, LINZ) | Docs | Detailed localities; keep separate from proprietary data |
| GADM | https://gadm.org/license.html | Download | GPKG, Shapefile | Occasional | Global admin levels | **Non-commercial only; "Redistribution or commercial use is not allowed without prior permission"** | Docs | **Avoid** |

### MVP: a shared geography pack plus the family catalog
- **World layers:** Natural Earth countries (1:110m and 1:50m) with ISO3, UN M49 and World Bank code crosswalks; geoBoundaries gbOpen first-level regions, simplified.
- **US layers:** Census 2025 cartographic files for nation, state, county, metro, congressional district and state legislative districts, at 1:500k, 1:5M and 1:20M. Add a separate **2026 congressional district** layer for the 10 redrawn states, taken from state sources.
- **Outputs:** TopoJSON, GeoJSON, PMTiles and GeoParquet, with ID crosswalks that track Census vintages.
- **Catalog:** one machine-readable catalog (e.g. a Frictionless `datapackage.json`) with license and attribution fields for every dataset across the family.
- **First gallery:** county unemployment map, 2026 county results map, satellites by orbit and operator, and global inflation, all drawn from the sibling MVPs.
- **Why first:** every sibling site needs boundaries that match its IDs, and this set is entirely public domain or CC BY.
- **Volume:** about 2–3 GB of raw inputs and about 0.5–1 GB of published products.
- **Cadence:** yearly (Census files arrive in spring), with Natural Earth and geoBoundaries updated as they change.

### Gotchas
- **Licensing:**
  - Never ship GADM.
  - Use only gbOpen from geoBoundaries for paid products.
  - Overture is share-alike under ODbL.
  - Respect OWID's 403 responses and credit OWID's original sources.
- **Disputed borders:** Kashmir, Crimea, Western Sahara, Taiwan and others. Default to de facto lines and document the choice. Natural Earth offers alternative point-of-view versions.
- **Map details:**
  - US maps need an Albers projection with insets for Alaska, Hawaii and Puerto Rico.
  - Simplify geometry with topology preserved.
  - The Connecticut GEOID change applies.
- **Attribution:** put source attribution on every chart image and embed.

---

## Not verified, or worth a follow-up
- **ECB API:** HTTP 503 on two attempts today.
- **Rate limits:** OECD's exact limits (two OECD pages disagree); FRED's per-minute limit (third parties only); IMF key and rate limits.
- **Election and vote data licenses:** VEST and Redistricting Data Hub; OpenElections; Voteview.
- **Satellite data licenses:** Launch Library 2; CelesTrak's redistribution terms (including the supplemental operator data); Aerospace reentry database.
- **Retired or changing services:** the 2026 status of Google Civic's Elections and Divisions APIs; the final retirement date of the Fed's Data Download Program.
- **Other:** LegiScan's free tier (30k vs 10k queries); senate.gov XML (blocked for automated clients); NASA FY2026 Earth-science mission outcomes; per-dataset licenses on Planetary Computer.
