# Public data sources: climatedata.dev, waterdata.dev, airdata.dev, carbondata.dev, wildfiredata.dev

Research agent report. Checked 2026-10-23/24.

**Status codes used in the tables**
- **V**: I loaded the endpoint or page myself during this research.
- **S**: confirmed only through search results or third-party pages.
- **R**: live, but at risk or changing.
- **X**: discontinued or moved.
- **U**: could not verify.

Where I was not sure of an exact endpoint, the URL is a landing page.

---

## 0. Federal and other changes from 2025–26 that affect these sites

| Item | Status, Oct 2026 | Where the data is now |
|---|---|---|
| NOAA Billion-Dollar Disasters | X. NOAA retired it on 7 May 2025 and made no updates after 2024. | The 1980–2024 archive is still at NCEI and is public domain. Climate Central relaunched it with the same methodology, and the former NOAA lead runs it. It uses proprietary insurance inputs, so its redistribution terms are not verified. |
| EPA EJScreen | X. Removed in Feb 2025. A lawsuit to restore it was dismissed on 13 Mar 2026 for lack of standing. | Public Environmental Data Partners (PEDP) hosts a v2.3 reconstruction at screening-tools.com, alongside CEJST, EJAM and HIFLD Next. |
| climate.gov | X. Redirected to noaa.gov since 24 Jun 2025. | climate.us, a nonprofit clone run by former staff, launched Jun 2026. It is not a data API. |
| National Climate Assessment (globalchange.gov) | X. Offline since about 30 Jun 2025. | The NCA5 report is in the NOAA library. No new data products. |
| EPA Greenhouse Gas Reporting Program (GHGRP) | R. EPA's newest public data is reporting year (RY) 2023 (data-sets page updated 30 Jul 2026). EPA never published RY2024. The RY2025 deadline moved to 30 Oct 2026, and one source says a 14 Oct 2026 interim rule moved it again to 10 Mar 2027. The Sept 2025 proposal to drop 46 of 47 source categories is still pending. | The Environmental Integrity Project (EIP) obtained RY2024 through a FOIA request and posts it on Google Drive with no licence stated. The Envirofacts API is still live (I tested it). |
| US GHG Inventory | X. The last official edition covers 1990–2022 (Apr 2024). The 1990–2023 edition came out only through an EDF FOIA request. The US filed no 2026 inventory, and EDF reports the US is withdrawing from the UNFCCC effective Feb 2027. | University of Maryland's Center for Global Sustainability published an independent 1990–2024 inventory. |
| eGRID, EPA Emission Factors Hub, USEEIO | R. EPA's newest releases are eGRID2023 (rev2, Jun 2025) and the Jan 2025 Hub. EPA has stopped developing USEEIO. | Cornerstone (Watershed, Stanford and ERG) publishes eGRID2024, Hub 2026 and the USEEIO-based factors v1.4 on Zenodo under CC BY 4.0. These are not official EPA releases. |
| USGS legacy WaterServices | R. Service degradation starts Nov 2026, with planned outages in Jan 2027 and shutdown on **22 Feb 2027**. The labs Observations API was retired on 19 Oct 2026. | api.waterdata.usgs.gov (OGC API) |
| AirNow API | R. Six legacy endpoints were retired on 30 Sep 2026. | New `/aq/forecast/current/`, `/aq/observation/current/*` and `/aq/dailydata/` endpoints. `/aq/data/` (hourly by bounding box) remains. |
| NCEI | Partial cuts. nClimGrid-Daily FTP access was retired in Jun 2025; HURSAT was decommissioned; snow and ice products (Sea Ice Index, SNODAS) were cut to "Basic" support. | AWS copies through NOAA's Open Data Dissemination program (NODD) |
| NOAA Mauna Loa CO2 | R. Data was still updating as of Jun 2026. The FY2027 budget proposal would defund the observatory, and its power has not been restored since the 2022 eruption. | gml.noaa.gov |
| MODIS (Terra and Aqua) | R. NASA's LAADS archive says science data collection ends Feb 2027 (Terra) and Sep 2027 (Aqua). Both satellites are already drifting in orbit. | Move to VIIRS. |
| EIA | R. Staff is down an estimated 30–40%, and several reports have been delayed or cancelled. | The API is still listed. |
| Wildland fire agencies | The Interior Department stood up a US Wildland Fire Service in Jan 2026. The Forest Service is still separate, and Congress did not fund a new agency. | The NIFC/WFIGS feeds still update; the current-perimeters layer was last edited 2026-10-24. |

**Licensing traps across domains (non-commercial or no-redistribution terms)**
- OpenAQ API: says data is "free for non-commercial uses"; commercial use needs a paid Custom Use Agreement.
- Berkeley Earth: CC BY-NC 4.0.
- EDGAR's IEA-based fossil CO2 series: CC BY-NC-ND 4.0.
- GRDC portal: research only, no redistribution.
- Open-Meteo free API: non-commercial only.
- PRISM 800 m product: commercial use prohibited.
- PurpleAir: paid API with a restrictive licence.
- CWFIS agency perimeter database (NFDB): needs an End-User Agreement.
- AirNow and EEA: both say not to alter values.

---

## 1. climatedata.dev

| # | Source (publisher) | URL | Access | Formats | Update | Coverage | License / commercial | Status | Good for |
|---|---|---|---|---|---|---|---|---|---|
| 1 | nClimDiv county/division files + Climate at a Glance (NOAA NCEI) | https://www.ncei.noaa.gov/pub/data/cirs/climdiv/ ; https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/ | Bulk files, no key; Climate at a Glance also has a REST API (CSV/JSON/XML) | Fixed-width text | Monthly (latest files 2026-10-06) | Contiguous US county, division, state and nation; 1895– | US public domain. NOAA asks for attribution and bars implying endorsement. | V | County Tavg/Tmax/Tmin/precip, PDSI, HDD/CDD, plus county 1991–2020 normals files |
| 2 | GHCN-Daily (NCEI, via NODD) | https://registry.opendata.aws/noaa-ghcn/ (s3://noaa-ghcn-pds) | S3, no account | CSV by year | Daily | Global stations, 1763– | CC0 | V. NODD is changing the S3 prefix layout. | Station daily temperature, precip, snow |
| 3 | nClimGrid daily and monthly (NOAA) | https://registry.opendata.aws/noaa-nclimgrid/ | S3 | NetCDF; county/state CSV and Parquet | Daily (preliminary about every 3 days), monthly | Contiguous US ~5 km grid; daily 1951–, monthly 1895– | Open (NODD) | S. FTP retired. | Ready-made county daily series |
| 4 | US Climate Normals 1991–2020 (NCEI) | https://www.ncei.noaa.gov/products/climate-normals | Bulk; also AWS and Azure | CSV | Static | US stations: hourly, daily, monthly, annual | Public domain | S | Official baselines |
| 5 | ERA5 / ERA5-Land (ECMWF C3S) | https://cds.climate.copernicus.eu ; also ARCO-ERA5 Zarr at gs://gcp-public-data-arco-era5 | Free account plus `cdsapi` key; requests are queued | GRIB, NetCDF, Zarr | Daily, about 5-day lag | Global 0.25° (ERA5-Land 0.1°), 1940– | **CC BY 4.0 since 2 Jul 2025** | S | Any-point global history |
| 6 | GISTEMP v4 (NASA GISS) | https://data.giss.nasa.gov/gistemp/ | Bulk | CSV, TXT, NetCDF | Monthly | Global and 2° grid, 1880– | Public domain | S. A Nov 2025 note says the GHCN v4 input has been missing some countries since Aug 2025. | Headline global anomaly |
| 7 | HadCRUT5 (Met Office) | https://www.metoffice.gov.uk/hadobs/hadcrut5/ | Bulk | NetCDF, CSV | Monthly | Global, 1850– | OGL v3 with mandatory acknowledgement text | S | Second global series with uncertainty |
| 8 | GHG trends (NOAA GML) | https://gml.noaa.gov/ccgg/trends/ | Bulk | TXT/CSV | Monthly | Mauna Loa 1958–; global CO2, CH4, N2O, SF6 | Public domain | R | Concentrations |
| 9 | NEX-GDDP-CMIP6 (NASA) | https://registry.opendata.aws/nex-gddp-cmip6 | S3, no account | NetCDF | Static | Global 0.25°, daily, 1950–2100, about 35 models | CC0 per the AWS registry (Earth Engine lists some models as CC BY 4.0, so check file attributes) | S | Downscaled projections |
| 10 | WMO WIS2 Global Broker/Cache | https://community.wmo.int/en/node/253491 | MQTT subscription, HTTPS download | BUFR/GRIB | Real-time | Global; 116 nodes from 92 WMO members (Jun 2026) | "Core" data free with no conditions; "recommended" data may carry a licence | S | Non-US real-time observations |

**Conditional sources (licence or rate limits):**
- Berkeley Earth: CC BY-NC.
- Open-Meteo: the data is CC BY 4.0, but the free API is non-commercial with a 10k calls/day cap. Commercial use needs the paid tier or self-hosting under AGPLv3.
- PRISM: the main terms allow redistribution with attribution, but the 800 m product bars commercial use.
- NASA POWER: free, public domain, about 30 req/min on the climatology endpoint (a 2021 figure).
- Meteostat: CC BY-NC (not re-verified).

**MVP dataset: a US county climate "report card"**
- **What:** monthly average, maximum and minimum temperature and precipitation for all ~3,100 contiguous-US counties, 1895 to present. Each value comes with its 1991–2020 normal, anomaly, percentile and rank (for example, "3rd warmest September since 1895").
- **Global layer:** the same metrics for about 200 world capitals from ERA5, 1940 to present.
- **Why first:**
  - One file family that is public domain and updated monthly.
  - Strong public and media demand that lost its main outlet when climate.gov went dark.
  - Small and cheap to host.
  - Gives an SEO landing page for every county.
- **Sources:**
  - nClimDiv `tmpccy`, `tmaxcy`, `tmincy`, `pcpncy` files and the matching `norm-*` files.
  - ERA5 monthly-means single levels.

**Value-add**
- Parse the fixed-width files and map NCEI's state codes (01–48, alphabetical) to FIPS codes.
- Convert °F and inches to metric.
- Offer two baselines (1901–2000 and 1991–2020), plus ranks and percentiles.
- Keep ERA5 point values on the same baseline as the county data.
- Publish one JSON file per county and a Parquet bulk file, with versioned monthly snapshots.

**Volume and cadence**
- Raw input is about 175 MB a month (4 files of ~39 MB plus normals files).
- The ERA5 backfill is about 6–8 GB once; keep only ~10 MB of extracted points.
- Output is about 150 MB Parquet or about 300 MB of per-county JSON.
- Monthly: NCEI posts around the 6th; ERA5 monthly means arrive around the 5th–6th.

**Gotchas**
- **nClimDiv:** the whole history is reissued every month, and recent months are revised. Missing values use -99.99 or -9.99 sentinels.
- **ERA5 via the Copernicus Climate Data Store (CDS):** requests wait in a queue and have size caps. Credit line: "Contains modified Copernicus Climate Change Service information [year]".
- **ARCO-ERA5:** its documentation says it only has data to 2023, so it may lag.
- **GHCN on AWS:** don't hard-code paths; NODD is restructuring prefixes.
- **CDO v2 API, if used:** free token, documented limits of 5 req/s and 10k/day (not re-verified).

---

## 2. waterdata.dev

| # | Source (publisher) | URL | Access | Formats | Update | Coverage | License / commercial | Status | Good for |
|---|---|---|---|---|---|---|---|---|---|
| 1 | USGS Water Data APIs (OGC API) | https://api.waterdata.usgs.gov/ (base `/ogcapi/v1/`) | REST. A free api.data.gov key gives about 1,000 req/hr; anonymous use gets less. | JSON/GeoJSON, CSV | Real-time (continuous values) and daily | US: streamflow, gage height, groundwater (`field-measurements`), plus Samples and Statistics APIs | Public domain | V. Legacy waterservices shuts down 22 Feb 2027. | Core streamflow and groundwater |
| 2 | Water Quality Portal (USGS, EPA and ~400 agencies) | https://www.waterqualitydata.us/ | REST/bulk, no key | CSV/zip (WQX 2.2; WQX 3.0 under `/beta`) | Continuous | US; 100M+ results | Public domain (provider-contributed) | R. Cloud cutover 31 Aug 2026; modernization runs to Sep 2028. The WQX 2.2 profiles **lack USGS data after 11 Mar 2024**. | Discrete water quality |
| 3 | US Drought Monitor (NDMC, USDA, NOAA) | https://droughtmonitor.unl.edu/DmData/DataDownload/WebServiceInfo.aspx | REST at usdmdataservices.unl.edu/api, no key; GIS downloads | CSV/JSON/XML, SHP | Weekly (Thursday) | US states, counties, HUCs; 2000– | Free, with a required credit line ("Map courtesy of NDMC") | V | Drought % area, DSCI, weeks in drought |
| 4 | NWPS API (NOAA NWS) | https://api.water.noaa.gov/nwps/v1/docs/ | REST, no key | JSON | Real-time | US forecast points: observations, official forecasts, flood categories | Public domain | S | Flood stage and forecasts (little history) |
| 5 | National Water Model, operational and retrospective (NOAA) | https://registry.opendata.aws/nwm-archive/ | S3 | NetCDF, Zarr | Hourly (operational) | About 2.7M contiguous-US reaches; retrospective v3.0 covers 1979–2023 | No restrictions | S | Modeled flow at ungauged reaches |
| 6 | Reservoirs: USBR RISE and USACE CWMS Data API | https://data.usbr.gov/ ; https://cwms-data.usace.army.mil/cwms-data/ | REST; public reads | JSON | Daily / hourly | Reclamation (western US) and Corps projects nationwide | Public domain | S | Storage, elevation, releases |
| 7 | EPA ECHO Safe Drinking Water Act (SDWA) dataset | https://echo.epa.gov/tools/data-downloads | Bulk zip (457 MB) plus REST | CSV | Quarterly | All US public water systems: violations, enforcement | Public domain | V (page updated 16 Sep 2026) | Drinking-water compliance |
| 8 | GloFAS historical (Copernicus CEMS) | https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical | Free EWDS account plus `cdsapi` | GRIB2/NetCDF | Daily (intermediate) / monthly (consolidated) | Global 0.05° (v4), 1979– | CC BY 4.0 | S | Global modeled discharge |
| 9 | GRACE/GRACE-FO JPL mascons RL06.3 v4 (NASA JPL) | https://podaac.jpl.nasa.gov/dataset/TELLUS_GRAC-GRFO_MASCON_GRID_RL06.3_V4 | Earthdata login | NetCDF | Monthly | Global, 2002– | NASA open data | R. Extension past 2026 is pending review; the successor launches Dec 2028. | Groundwater and storage anomalies |
| 10 | GRDC-Caravan and Caravan | https://zenodo.org/records/15349031 | Zenodo | CSV/NetCDF | Irregular | 22k catchments, 1950–2023 | **CC BY 4.0** (the GRDC portal itself is non-commercial) | S | Commercial-safe global gauge data |

**Also:**
- EEA Waterbase Water Quality ICM: 2026 edition published 7 Jul 2026, covering 1900–2025. CSV/SQLite plus the Discodata API. EEA CC BY, but with a no-alteration clause.
- California CDEC: the `dynamicapp/wsSensorData` endpoint (CSV/JSON).

**MVP dataset: national streamflow conditions**
- **What:** daily mean discharge for every active USGS streamgage, with:
  - the percentile for that day of year and "% of normal";
  - the 7-day trend;
  - NWS flood status;
  - the weekly USDM drought category for the gage's county and HUC8.
- **Why first:** the legacy USGS service and R `readNWIS()` stop working in Feb 2027, so thousands of scripts and apps need a simple, stable replacement. The data is public domain, updates daily and is manageable in size.
- **Sources:** USGS `daily`, `monitoring-locations`, `time-series-metadata` and the Statistics API; NWPS; USDM.

**Value-add**
- One flat site table: USGS ID mapped to HUC, county FIPS, drainage area and the NWS location ID crosswalk.
- Discharge in both cfs and m³/s.
- USGS qualifiers (provisional, ice-affected, estimated) turned into boolean flags.
- Precomputed percentiles.
- GeoJSON "map of today", plus Parquet history.

**Volume and cadence**
- Daily snapshot is about 2–5 MB.
- Full daily history for active gages is roughly 1.5–3 GB as Parquet.
- Percentile tables are about 100–200 MB.
- Daily refresh, plus hourly "latest" if wanted.

**Gotchas**
- **Rate limit:** about 1,000 req/hr per key. Batch by state or bounding box (`limit` goes up to 50k per request), and email wdfn@usgs.gov for a custom limit.
- **Provisional data:** values are revised for months, so reprocess the trailing 12–18 months.
- **Build only on api.waterdata.usgs.gov.**
- **GRDC:** don't use the GRDC portal (non-commercial terms); use GRDC-Caravan instead.
- **USDM:** the credit line is required.
- **NWM:** retrospective files are very large; subset with Zarr.

---

## 3. airdata.dev

| # | Source (publisher) | URL | Access | Formats | Update | Coverage | License / commercial | Status | Good for |
|---|---|---|---|---|---|---|---|---|---|
| 1 | AQS API and AirData files (EPA) | https://aqs.epa.gov/aqsweb/documents/data_api.html ; https://aqs.epa.gov/aqsweb/airdata/download_files.html | REST with a free email key: **≤10 req/min**, ≤1M rows, date range within one year. Also bulk zips. | JSON; CSV | API updates as data is validated (6+ month lag); files twice a year (latest "as of 2026-06-25") | US regulatory monitors, 1980– | Public domain | V | Validated history |
| 2 | AirNow API and file products (EPA and partners) | https://docs.airnowapi.org/ ; files.airnowtech.org | REST with a free key; hourly flat files | JSON/CSV/XML/KML | Hourly observations; daily forecasts | US, Canada, Mexico reporting areas and sites | Free. Data Use Guidelines: credit the agencies, label data preliminary, **don't alter values**. | V (six endpoints retired 30 Sep 2026) | Real-time AQI and forecasts |
| 3 | OpenAQ v3 | https://docs.openaq.org/ | REST with a free key (60/min, 2,000/hr); AWS S3 archive | JSON/CSV | Hourly | Global reference and low-cost sensors | **Licence varies by provider.** The API is "free for non-commercial uses"; commercial use needs a paid Custom Use Agreement, and competing products are barred. | V | Discovering monitors and looking up provider licences |
| 4 | CAMS via the Atmosphere Data Store (ECMWF) | https://ads.atmosphere.copernicus.eu | Free account plus `cdsapi` | NetCDF/GRIB | Forecasts twice daily | Global 5-day forecasts (~0.4°); EAC4 reanalysis 2003–; Europe 0.1° | CC BY 4.0 (since Jul 2025) | S | Modeled PM2.5, O3, NO2 anywhere |
| 5 | Sentinel-5P TROPOMI (ESA, Copernicus Data Space Ecosystem) | https://dataspace.copernicus.eu | Free account; OData/STAC/openEO; quotas apply | NetCDF L2 | Daily | Global, 2018– | Copernicus open data, free with attribution (licence text not re-checked) | S | NO2, CO, SO2, aerosol index |
| 6 | TEMPO (NASA/SAO) | https://www.earthdata.nasa.gov/data/instruments/tempo | Earthdata login | NetCDF L2/L3 | Hourly in daytime; near-real-time 2–3 h | North America, Aug 2023– | NASA open data | R. V03 is being reprocessed to V04, and the mission extension is unconfirmed. | Hourly NO2/HCHO over cities |
| 7 | Satellite-derived surface PM2.5 V6.GL.02.04 (WashU ACAG) | https://sites.wustl.edu/acag/datasets/surface-pm2-5 | Bulk | NetCDF | Annual | Global ~0.01°, 1998–2023 | Not stated. The older SEDAC version allowed commercial use with attribution. | U (licence) | PM2.5 exposure everywhere |
| 8 | EEA Air Quality Download Service | https://aqportal.discomap.eea.europa.eu/download-data | Two-step scripted API returning Parquet, no key | Parquet | Hourly up-to-date (UTD) data plus annual verified data | EU/EEA stations, 2013– | EEA CC BY, with a no-alteration clause | S | European monitors |
| 9 | Sensor.Community archive | https://archive.sensor.community/ | Bulk daily CSV | CSV | Daily | Global low-cost PM sensors | DbCL 1.0 (per ClickHouse docs) | U | Dense sensor coverage |

**Avoid for redistribution:** PurpleAir (paid points API, restrictive licence) and WAQI/aqicn (terms restrict redistribution; not re-verified).

**MVP dataset: county AQI history plus live AQI**
- **What:** daily and hourly AQI and concentrations (PM2.5, ozone, NO2) for every US monitor, county and CBSA. The series is continuous: validated AQS daily data from 1980 up to about six months ago, stitched to preliminary AirNow data for the recent window, with a `validated` flag on each row.
- **Why first:**
  - EPA splits this across two disconnected systems: AQS is slow and capped at 10 req/min; AirNow is preliminary, rate-capped and just changed endpoints.
  - Users want one county series.
  - Both sources are public domain.
- **Sources:** AQS pre-generated files and API; AirNow hourly files and the `/aq/data/` endpoint.

**Value-add**
- Recompute historical AQI on EPA's 2024 PM2.5 breakpoints so the series is consistent.
- Join monitors to county, CBSA and census-tract centroids.
- Deduplicate co-located parameter occurrence codes (POCs).
- Unit-normalize to µg/m³ and ppb.
- Publish raw values as received next to derived AQI, clearly labeled, to satisfy AirNow's "don't alter" rule.

**Volume and cadence**
- AQS daily criteria pollutants, 1980 to present: about 25–30M rows, roughly 0.5–1 GB Parquet.
- AirNow hourly: about 300–400k rows/day, roughly 200 MB/yr as Parquet.
- County/CBSA product: about 50–100 MB.
- Hourly refresh for recent data; monthly or twice-yearly AQS backfill.

**Gotchas**
- **AQS:** use the bulk files, not the API, for the backfill. Watch for the PM2.5 data advisories on the 2017 and 2020–2026 files.
- **AirNow:** hourly limits are fixed and cannot be raised, so use the `files.airnowtech.org` files.
- **OpenAQ:** commercial use needs a paid agreement. Pull from the original providers (AirNow, EEA and national agencies) instead.
- **Attribution:** Copernicus/CAMS requires it.

---

## 4. carbondata.dev

| # | Source (publisher) | URL | Access | Formats | Update | Coverage | License / commercial | Status | Good for |
|---|---|---|---|---|---|---|---|---|---|
| 1 | GHGRP facility data (EPA) | https://www.epa.gov/ghgreporting/data-sets ; https://data.epa.gov/efservice/ | Bulk files; REST with no key | XLSX/CSV/JSON | Annual (stalled) | About 8k US large emitters and suppliers, 2010–2023 | Public domain | **R.** RY2024 not published by EPA; RY2025 delayed; rollback proposed. | Facility and parent-company Scope 1 |
| 2 | eGRID (EPA) and eGRID2024 (Cornerstone) | https://www.epa.gov/egrid ; https://zenodo.org/records/18968658 | Bulk | XLSX | Annual | US plants, states, eGRID subregions | EPA version public domain; Cornerstone version CC BY 4.0 | V. EPA's newest is eGRID2023. Cornerstone's 2024 version was generated from EPA's code and not quality-checked by EPA. | Location-based Scope 2 |
| 3 | GHG Emission Factors Hub (EPA Jan 2025; Cornerstone 2026) | https://www.epa.gov/climateleadership/ghg-emission-factors-hub ; https://zenodo.org/records/20403776 | Bulk | XLSX (including a machine-readable sheet) | Annual | US fuels, mobile, refrigerants, waste, travel | Public domain / CC BY 4.0 | V | Scope 1–3 activity factors (2026 version uses AR6 GWPs) |
| 4 | Supply Chain GHG Emission Factors / USEEIO v1.4.0 (Cornerstone) | https://zenodo.org/records/17202747 | Bulk | XLSX, PDF, RDS | Annual (planned) | US commodities, kg CO2e per USD 2024 | CC BY 4.0 on Zenodo (Climatiq lists it as BY-SA) | V | Spend-based Scope 3 |
| 5 | UK conversion factors 2026 (DESNZ) | https://www.gov.uk/government/collections/government-conversion-factors-for-company-reporting | Bulk | XLSX | Annual (June) | UK plus international travel, freight, materials | OGL v3 | V | Commonly used factor set for CSRD |
| 6 | Climate TRACE | https://climatetrace.org/data ; API https://api.climatetrace.org/v7 | Bulk packages; beta REST API with no SLA; BigQuery | CSV/GPKG/JSON | Monthly (v5.11.0 released 24 Sep 2026, data to Jul 2026) | Global; about 2.77M sources from about 745M assets; owner-level | CC BY 4.0 | V | Asset and owner emissions |
| 7 | EDGAR (EC JRC) | https://edgar.jrc.ec.europa.eu/dataset_ghg2025 | Bulk | XLSX; 0.1° grids in TXT/NetCDF | Annual (the 2026 release covers 1970–2025) | Global, country × IPCC sector, all GHGs | CC BY 4.0, **except the IEA-EDGAR CO2 series, which is CC BY-NC-ND** | V | Non-CO2 gases by country and sector |
| 8 | Global Carbon Budget fossil CO2 (Global Carbon Project) | https://zenodo.org/records/17417124 | Bulk | CSV/JSON | Annual (November) | 221 countries, 1750–2024 | CC BY 4.0 | V | Authoritative national fossil CO2 |
| 9 | EU ETS Union Registry data and EEA Industrial Reporting (EEA) | https://www.eea.europa.eu/data-and-maps/data/european-union-emissions-trading-scheme-17 ; https://industry.eea.europa.eu/industrial-emissions/dataset | Bulk | CSV/SQL | Annual | ETS verified emissions 2005–2024; E-PRTR/LCP v16 2007–2024 | EEA CC BY, with a no-alteration clause | S | EU installation-level data |
| 10 | Ember electricity data | https://ember-energy.org/data/ | API with a free key; CSV | CSV/JSON | About twice a month | 215 countries annual, 88 monthly | CC BY 4.0 | S | Global grid carbon intensity |

**Also:**
- PRIMAP-hist v2.6.1 or newer: CC BY 4.0, all Kyoto gases, 1750–2023.
- OWID CO2 dataset: CC BY 4.0; cite the upstream sources.
- Global Energy Monitor trackers: CC BY 4.0, unit-level ownership, download is behind a form.
- UNFCCC CRT inventory files: 2026 files are posted; the US did not submit; terms not verified.
- EIA API: public domain, but delivery is at risk from the staff cuts.

**MVP dataset: open emission-factor API**
- **What:** one versioned, unit-normalized library covering Scope 1, Scope 2 (location-based) and Scope 3 (spend-based):
  - Hub 2026 for combustion, mobile and refrigerants;
  - eGRID2024 subregion and state factors;
  - Ember country grid intensity, monthly and annual;
  - DESNZ 2026;
  - USEEIO v1.4.
- **Why first:**
  - **California SB 253's first Scope 1/2 deadline is 10 Nov 2026** (CARB's regulation was approved 12 Oct 2026).
  - EPA has stopped maintaining these factor sets, so practitioners are piecing them together from Zenodo spreadsheets.
  - Every input is CC BY, OGL or public domain.
  - The data is tiny and companies already pay for this (Climatiq is the comparator).
  - CSRD scope shrank under the Omnibus I package (above 1,000 employees and €450M turnover; Wave 2 now reports in 2028), but those filers need the same factors.
- **Product #2:** facility emissions linked to parent companies (GHGRP, EU ETS, Climate TRACE, GEM).

**Value-add**
- Turn the spreadsheets into one long table: activity, unit, gas, factor, GWP set, CO2e, source, version, valid year, region code.
- Recompute CO2e under both AR5 and AR6 GWPs.
- Unit conversions.
- Crosswalks: eGRID subregion to ZIP (only if a lookup file is available), NAICS to BEA for USEEIO, ISO3 for Ember.
- Immutable versioned URLs so auditors can cite the exact factor used.

**Volume and cadence**
- Under 100 MB in total, tens of thousands of rows.
- Annual updates (Hub, eGRID, DESNZ, USEEIO) plus monthly Ember.

**Gotchas**
- Label Cornerstone releases as "EPA methodology, Cornerstone release (not EPA-official)".
- eGRID2024 uses AR5 GWPs while Hub 2026 uses AR6. Don't mix them silently.
- DESNZ factors should match the year the activity happened.
- USEEIO factors are in 2024 dollars, so spend must be deflated.
- EDGAR's IEA-based CO2 series is non-commercial and no-derivatives.
- GHGRP RY2024 exists only as EIP's FOIA copy with no licence.
- Climate TRACE asks users to keep API volume low; use the bulk downloads instead.
- CC BY requires attribution and a note of changes.

---

## 5. wildfiredata.dev

| # | Source (publisher) | URL | Access | Formats | Update | Coverage | License / commercial | Status | Good for |
|---|---|---|---|---|---|---|---|---|---|
| 1 | FIRMS (NASA LANCE) | https://firms.modaps.eosdis.nasa.gov/api/ | REST with a free MAP_KEY: **5,000 transactions per 10 min**, day range 1–5. The archive needs an Earthdata login or email code. | CSV, KML; archive in SHP/CSV/JSON | Near-real-time ~3 h; faster real-time feeds in US/Canada | Global. MODIS 2000–; VIIRS (S-NPP, NOAA-20, NOAA-21) 2012–; Landsat for US/Canada | NASA open data; citation requested | V. The `country` endpoint is "currently unavailable"; MODIS ends 2027. | Active fire detections |
| 2 | WFIGS incidents and perimeters, plus InterAgency perimeter history (NIFC) | https://data-nifc.opendata.arcgis.com/ ; https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0 | ArcGIS REST, no key | GeoJSON, PBF | About every 5 min | US incidents (IRWIN IDs): current, year-to-date, history through 2024 | Federal public domain (no licence text) | V (edited 2026-10-24) | Official name, acres, containment, perimeter |
| 3 | MTBS (USGS and USFS) | https://www.mtbs.gov/ | Bulk | SHP, GeoTIFF | Quarterly; 6–12 month lag | US fires ≥1,000 ac (West) / ≥500 ac (East), 1984– | Public domain | S (Q3 2026 release on 15 Jul) | Burn severity, final perimeters |
| 4 | HMS smoke and fire (NOAA NESDIS) | https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/ | HTTPS directory | SHP/KML/GeoTIFF | Daily (finalized next morning, Eastern time) | North America | Public domain | V (updated 2026-10-23) | Smoke extent and density |
| 5 | EFFIS / GWIS (EC JRC, Copernicus EMS) | https://forest-fire.emergency.copernicus.eu/applications/data-and-services | WFS for burnt areas; GWIS country-profile API; historic data by request form | SHP/SpatiaLite/XLSX | Daily during fire season | Europe/MENA; global statistics 2002– | JRC open data / CC BY 4.0 (confirm on the EFFIS licence page) | S | Non-US burned area |
| 6 | CWFIS (Natural Resources Canada) | https://cwfis.cfs.nrcan.gc.ca/datamart | Downloads plus WMS/WFS | CSV/SHP/GeoTIFF | Daily | Canada: active fires, hotspots, perimeter estimates, FWI grids | OGL-Canada (acknowledge NRCan). Agency perimeters (NFDB) need an End-User Agreement. | S | Canadian incidents and fire weather |
| 7 | CEMS fire danger historical (Copernicus) | https://ewds.climate.copernicus.eu/datasets/cems-fire-historical-v1 | EWDS account plus `cdsapi` | NetCDF/GRIB2 | Daily | Global 0.25°, 1940–; Canadian, US and Australian fire danger systems | CC BY 4.0 | V | Fire-weather percentiles |
| 8 | gridMET (UC Merced / University of Idaho) | https://tds-proxy.nkn.uidaho.edu/thredds/reacch_climate_MET_catalog.html | THREDDS/OPeNDAP | NetCDF | Daily; provisional data becomes final after ~2 months | Contiguous US 4 km, 1979–: ERC, BI, fuel moisture, VPD | Public domain per mirrored docs (confirm) | S | US fire-danger indices |
| 9 | HRRR including smoke fields (NOAA) | https://registry.opendata.aws/noaa-hrrr-pds | S3 | GRIB2 | Hourly runs | Contiguous US and Alaska, 3 km; near-surface smoke (MASSDEN, 8 m) | NOAA open data | S | Smoke forecasts |
| 10 | NWS API alerts | https://www.weather.gov/documentation/services-web-api | REST, no key, User-Agent header required | GeoJSON/CAP | Real-time | US; `/alerts` keeps only 7 days | Public domain | S | Red Flag Warning polygons |

**Also:** NASA MODIS MCD64A1 and VIIRS VNP64A1 burned-area products (Earthdata login; not separately verified).

**MVP dataset: unified active-fire feed (also powers FireWatch)**
- **What:**
  - VIIRS/MODIS hotspots for the US and Canada (global VIIRS optional), grouped into fire events;
  - each event linked to its official incident (WFIGS IRWIN ID or CWFIS) and current perimeter;
  - a daily HMS smoke overlay.
- **Format:** GeoJSON and a JSON API, refreshed every 15–30 minutes.
- **Why first:**
  - FIRMS gives raw pixels with no incident context, while WFIGS gives incidents with no satellite context.
  - "Fires near me, with name, acres and containment" is what insurers, utilities and apps want.
  - It directly reuses FireWatch work.
- **Sources:** FIRMS (VIIRS SNPP, NOAA-20, NOAA-21 near-real-time), WFIGS current incidents and perimeters, CWFIS active fires, HMS smoke.

**Value-add**
- Deduplicate detections across the three VIIRS satellites and MODIS, then cluster in space and time (about 1–2 km and 24–48 h).
- Link clusters to incidents.
- One schema across the US and Canada, with hectares and acres.
- **Archive WFIGS perimeter snapshots.** The current layer drops fires under its "fall-off" rules, so a perimeter-progression history is something nobody else publishes.

**Volume and cadence**
- US and Canada: about 1–5 MB/day.
- Global VIIRS: about 10–50 MB/day raw.
- Peak-season perimeter snapshots: about 5–50 MB.
- About 1–3 GB/yr as Parquet.
- Refresh every 15–30 min (FIRMS) and 5–15 min (WFIGS); HMS daily.

**Gotchas**
- **FIRMS limits:** large or "world" requests count as multiple transactions. Faster real-time records are purged after 6 hours, and near-real-time records are replaced by science-quality data about 5 months later, so reprocess.
- **MODIS sunset:** plan for VIIRS only.
- **WFIGS:** 2,000-record pages. NIFC warns against repeatedly polling with relative-date queries; page by `ObjectID` or last-edit time instead.
- **MTBS:** 6–12 month lag.
- **EFFIS:** history is only available through a request form.
- **CWFIS:** agency perimeters (NFDB) need the End-User Agreement.
- **HMS:** smoke polygons are analyst-drawn categories, not concentrations.
- **Attribution:** FIRMS citation, Copernicus CC BY, NRCan acknowledgement.

---

## Main sources behind the status claims
- [Climate Central relaunch of the disasters database (WRKF/NPR)](https://www.wrkf.org/2025-11-06/a-tool-tracking-billion-dollar-disasters-is-active-again-after-being-retired-by-trump-administration)
- [NESDIS 2025 Notice of Changes: billion-dollar disasters](https://www.nesdis.noaa.gov/about/documents-reports/notice-of-changes/2025-notice-of-changes/billion-dollar-weather-and-climate-disasters)
- [Harvard EJ Tracker on EJScreen](https://eelp.law.harvard.edu/tracker/epa-added-environmental-health-indicators-to-ejscreen/)
- [Nieman Lab on climate.us](https://www.niemanlab.org/2026/09/the-trump-administration-killed-climate-gov-a-new-website-is-resurrecting-it/)
- [EIP's FOIA release of 2024 GHGRP data](https://environmentalintegrity.org/2024-greenhouse-gas-reporting-program-data/)
- [EDF on the University of Maryland US inventory](https://blogs.edf.org/energyexchange/2026/04/15/the-university-of-maryland-steps-up-to-develop-a-u-s-greenhouse-gas-inventory-and-analysis/)
- [USGS WaterServices decommission timeline](https://content.govdelivery.com/accounts/USDOIGS/bulletins/42b03d7)
- [USGS/EPA Water Quality Portal modernization](https://waterdata.usgs.gov/blog/wqp-modernization/)
- [AirNow June 2026 API changes notice](https://docs.airnowapi.org/docs/AirNowAPIUpdates2026June.pdf)
- [ECMWF: CC BY licence replaces Copernicus licence, 2 Jul 2025](https://forum.ecmwf.int/t/cc-by-licence-to-replace-licence-to-use-copernicus-products-on-02-july-2025/13464)
- [NASA LAADS: MODIS to VIIRS transition](https://ladsweb.modaps.eosdis.nasa.gov/learn/modis-to-viirs-transition/)
- [V&E: CARB's SB 253 deadline of 10 Nov 2026](https://www.velaw.com/insights/oal-approves-carbs-initial-regulation-locking-in-november-10-2026-sb-253-reporting-deadline/)
- [GHGRP RY2025 deadline extension (J.J. Keller)](https://jjkellercompliancenetwork.com/news/epa-further-delays-2025-ghgrp-submissions)
- [US Wildland Fire Service launch (Wildfire Task Force)](https://wildfiretaskforce.org/?p=106979)
