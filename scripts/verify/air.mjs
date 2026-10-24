// G4/G5: the airdata datasets published to the local bucket match facts re-derived here from the original
// EPA files, with parsing written independently of the Python pipelines:
//   - its own zip reader and CSV parser for AirData's pre-generated files;
//   - its own county mapping (state code + county code) and its own name-to-code crosswalk for the annual
//     file, built from the same year's daily file instead of aqs_sites.zip (which the pipeline uses);
//   - its own AQI category breakpoints and its own max-of-sites county value from an AirNow daily file.
// county-aqi-daily: Los Angeles County on 2025-07-04, the number of counties and rows in 2025, the 2025
//   maximum, and a preliminary LA day re-derived from AirNow; every preliminary row comes after its county's
//   validated record and never duplicates it.
// county-aqi-annual: Los Angeles County's 2025 summary, the number of counties in 2025, the 1980 maximum,
//   and the annual maximum agreeing with the daily file.
//   node scripts/verify/air.mjs                     prints AIR VERIFIED ... when everything matches
//   node scripts/verify/air.mjs --negative-control  corrupts one expected value, so it must FAIL
// Source files are cached under the OS temp folder and revalidated with conditional GETs.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { inflateRawSync } from "node:zlib";
import { ROOT, check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const SITE = "airdata";
const UA = "datasites-verify (+mailto:hello@climatedata.dev)";
const CACHE = join(tmpdir(), "datasites-verify-air");
const AIRDATA = "https://aqs.epa.gov/aqsweb/airdata";
const AIRNOW = "https://files.airnowtech.org/airnow";
const LA = "us-county-06037";
const facts = [];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

/** GET with retries; keeps the body on disk and revalidates it with If-None-Match/If-Modified-Since. */
async function original(url) {
  mkdirSync(CACHE, { recursive: true });
  const path = join(CACHE, sha256(url).slice(0, 32));
  const metaPath = `${path}.json`;
  const headers = { "user-agent": UA };
  let meta = null;
  if (existsSync(path) && existsSync(metaPath)) {
    meta = JSON.parse(readFileSync(metaPath, "utf8"));
    if (meta.etag) headers["if-none-match"] = meta.etag;
    if (meta.lastModified) headers["if-modified-since"] = meta.lastModified;
  }
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, { headers, signal: AbortSignal.timeout(300_000) });
    } catch (err) {
      if (attempt < 3) {
        await sleep(3000 * (attempt + 1));
        continue;
      }
      fail(`${url}: ${err.cause?.code ?? err.message}`);
    }
    if (res.status === 304 && meta) return readFileSync(path);
    if ([429, 500, 502, 503, 504].includes(res.status) && attempt < 3) {
      await sleep(5000 * (attempt + 1));
      continue;
    }
    check(res.ok, `${url} answered HTTP ${res.status}`);
    const body = Buffer.from(await res.arrayBuffer());
    writeFileSync(path, body);
    writeFileSync(metaPath, JSON.stringify({ url, etag: res.headers.get("etag"), lastModified: res.headers.get("last-modified") }));
    return body;
  }
}

/** Fails clearly if the source moved on after the published version was built (then re-run the pipeline). */
function sameAsPublished(ds, name, body) {
  const entry = (ds.doc.source.raw ?? []).find((r) => r.name === name);
  check(entry, `${ds.doc.dataset}: version ${ds.doc.version} lists no raw input named ${name}`);
  check(
    sha256(body) === entry.sha256,
    `${ds.doc.dataset}: the source behind '${name}' changed after version ${ds.doc.version} was built; ` +
      "re-run `uv run datasites run airdata --target local` (from pipelines/) and verify again",
  );
}

/** The only file in a zip: end-of-central-directory -> central directory -> local header -> inflate. */
function unzipOnly(buf) {
  let eocd = buf.length - 22;
  while (eocd >= 0 && buf.readUInt32LE(eocd) !== 0x06054b50) eocd--;
  check(eocd >= 0, "not a zip file");
  const count = buf.readUInt16LE(eocd + 10);
  check(count === 1, `expected one file in the zip, found ${count}`);
  const cd = buf.readUInt32LE(eocd + 16);
  check(buf.readUInt32LE(cd) === 0x02014b50, "bad zip central directory");
  const method = buf.readUInt16LE(cd + 10);
  const size = buf.readUInt32LE(cd + 20);
  const local = buf.readUInt32LE(cd + 42);
  const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
  const data = buf.subarray(start, start + size);
  return (method === 0 ? data : inflateRawSync(data)).toString("latin1");
}

/** One CSV line, honoring double quotes. */
function splitCsv(line) {
  const out = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

function csvRecords(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.length);
  const header = splitCsv(lines[0]);
  return lines.slice(1).map((l) => {
    const cells = splitCsv(l);
    return Object.fromEntries(header.map((h, i) => [h, cells[i]]));
  });
}

// EPA's AQI categories, written out here rather than imported (40 CFR 58 App. G, 2024 revision).
function category(aqi) {
  if (aqi <= 50) return "Good";
  if (aqi <= 100) return "Moderate";
  if (aqi <= 150) return "Unhealthy for Sensitive Groups";
  if (aqi <= 200) return "Unhealthy";
  if (aqi <= 300) return "Very Unhealthy";
  return "Hazardous";
}

const isUsCounty = (state, county) => /^\d\d$/.test(state) && state !== "80" && `${state}${county}` !== "23901";

function same(actual, expected, label) {
  check(JSON.stringify(actual) === JSON.stringify(expected), `${label}: published ${JSON.stringify(actual)}, source says ${JSON.stringify(expected)}`);
  facts.push(label);
}

// --- county-aqi-daily ----------------------------------------------------------------------------------

const daily = localDataset(SITE, "county-aqi-daily");
const dailyZip = await original(`${AIRDATA}/daily_aqi_by_county_2025.zip`);
sameAsPublished(daily, "airdata-2025", dailyZip);
const source2025 = csvRecords(unzipOnly(dailyZip)).filter((r) => isUsCounty(r["State Code"], r["County Code"]));
check(source2025.length > 300_000, `AirData 2025 has only ${source2025.length} US county rows`);

// every published county series, read once: per-county 2025 validated rows and the stitch boundaries
const published2025 = new Map(); // location -> count of validated 2025 rows
let publishedMax2025 = { aqi: -1 };
let preliminaryRows = 0;
const stitchProblems = [];
const laRows = new Map();
for (const path of daily.paths().filter((p) => /^aqi\/us-county-[0-9]{5}\.csv$/.test(p))) {
  const location = path.slice(4, -4);
  const lines = daily.text(path).split("\n").filter((l) => l.length);
  const header = lines[0].split(",");
  check(
    header.join(",") === "date,aqi,category,defining_parameter,defining_site,sites_reporting,validated",
    `${path}: unexpected header ${lines[0]}`,
  );
  let lastValid = "";
  let firstPrelim = "";
  let seen = new Set();
  for (const line of lines.slice(1)) {
    const [date, aqiText, cat, param, site, sites, validated] = line.split(",");
    const aqi = Number(aqiText);
    if (seen.has(date)) stitchProblems.push(`${location} ${date} appears twice`);
    seen.add(date);
    if (cat !== category(aqi)) stitchProblems.push(`${location} ${date}: AQI ${aqi} published as ${cat}`);
    if (location === LA) laRows.set(date, { aqi, category: cat, defining_parameter: param, defining_site: site, sites_reporting: Number(sites), validated: validated === "true" });
    if (validated === "true") {
      if (date > lastValid) lastValid = date;
      if (date.startsWith("2025-")) {
        published2025.set(location, (published2025.get(location) ?? 0) + 1);
        if (aqi > publishedMax2025.aqi) publishedMax2025 = { aqi, location, date };
      }
    } else {
      preliminaryRows++;
      if (!firstPrelim || date < firstPrelim) firstPrelim = date;
    }
  }
  if (firstPrelim && lastValid && firstPrelim <= lastValid) stitchProblems.push(`${location}: preliminary ${firstPrelim} is not after validated ${lastValid}`);
}

// 1. Los Angeles County on Independence Day 2025, straight from the AirData file
const laSource = source2025.find((r) => r["State Code"] === "06" && r["County Code"] === "037" && r.Date === "2025-07-04");
check(laSource, "AirData 2025 has no Los Angeles row for 2025-07-04");
const laExpected = {
  aqi: Number(laSource.AQI) + (NEGATIVE ? 1 : 0),
  category: laSource.Category,
  defining_parameter: laSource["Defining Parameter"],
  defining_site: laSource["Defining Site"],
  sites_reporting: Number(laSource["Number of Sites Reporting"]),
  validated: true,
};
same(laRows.get("2025-07-04"), laExpected, "county-aqi-daily: Los Angeles County on 2025-07-04");

// 2. counties and rows in 2025
const counties2025 = new Set(source2025.map((r) => r["State Code"] + r["County Code"]));
same(published2025.size, counties2025.size, "county-aqi-daily: counties with validated 2025 data");
same([...published2025.values()].reduce((a, b) => a + b, 0), source2025.length, "county-aqi-daily: validated rows in 2025");

// 3. the 2025 maximum
const max2025 = source2025.reduce((best, r) => (Number(r.AQI) > best.aqi ? { aqi: Number(r.AQI), location: `us-county-${r["State Code"]}${r["County Code"]}`, date: r.Date } : best), { aqi: -1 });
same(publishedMax2025, max2025, "county-aqi-daily: highest AQI of 2025");

// 4. the stitch: preliminary rows exist, come after each county's validated record, never repeat a date,
//    and every category follows the AQI
check(preliminaryRows > 0, "county-aqi-daily: no preliminary (validated: false) rows were published");
check(stitchProblems.length === 0, `county-aqi-daily stitch: ${stitchProblems.slice(0, 5).join("; ")}`);
facts.push(`county-aqi-daily: ${preliminaryRows} preliminary rows all after their county's validated record`);

// 5. a preliminary Los Angeles day, re-derived from the AirNow daily file (final ~1 day after the date)
const retrieved = daily.doc.source.retrieved_at.slice(0, 10);
const cutoff = new Date(Date.parse(`${retrieved}T00:00:00Z`) - 3 * 86_400_000).toISOString().slice(0, 10);
const laPrelimDate = [...laRows.entries()].filter(([d, r]) => !r.validated && d <= cutoff).map(([d]) => d).sort().pop();
check(laPrelimDate, `county-aqi-daily: no preliminary Los Angeles day on or before ${cutoff}`);
const ymd = laPrelimDate.replaceAll("-", "");
const airnowBody = await original(`${AIRNOW}/${ymd.slice(0, 4)}/${ymd}/daily_data_v2.dat`);
sameAsPublished(daily, `airnow-${ymd}`, airnowBody);
const mmddyy = `${ymd.slice(4, 6)}/${ymd.slice(6, 8)}/${ymd.slice(2, 4)}`;
let best = null;
const laSites = new Set();
for (const line of airnowBody.toString("latin1").split(/\r?\n/)) {
  const f = line.split("|");
  if (f.length < 13 || f[0] !== mmddyy || !/^06037\d{4}$/.test(f[1]) || !f[12].startsWith("840")) continue;
  const aqi = Number(f[8]);
  if (!(aqi >= 0)) continue;
  laSites.add(f[1]);
  if (!best || aqi > best.aqi) best = { aqi, parameter: f[3] };
}
check(best, `AirNow ${ymd} has no Los Angeles site with an AQI`);
const published = laRows.get(laPrelimDate);
same(
  { aqi: published.aqi, category: published.category, sites_reporting: published.sites_reporting, validated: published.validated },
  { aqi: best.aqi, category: category(best.aqi), sites_reporting: laSites.size, validated: false },
  `county-aqi-daily: preliminary Los Angeles County on ${laPrelimDate} (max of ${laSites.size} AirNow sites)`,
);

// --- county-aqi-annual ---------------------------------------------------------------------------------

const annual = localDataset(SITE, "county-aqi-annual");
const annualZip = await original(`${AIRDATA}/annual_aqi_by_county_2025.zip`);
sameAsPublished(annual, "airdata-2025", annualZip);
const annual2025 = csvRecords(unzipOnly(annualZip));
// names -> codes from the daily file of the same year (both come from AQS and spell names the same way)
const codeOf = new Map();
for (const r of csvRecords(unzipOnly(dailyZip))) codeOf.set(`${r["State Name"].trim()}|${r["county Name"].trim()}`, `${r["State Code"]}${r["County Code"]}`);
const annualUs = annual2025.filter((r) => !["Country Of Mexico", "Canada"].includes(r.State.trim()));

// 1. Los Angeles County's 2025 summary
const laAnnual = annualUs.find((r) => r.State === "California" && r.County === "Los Angeles");
check(laAnnual, "AirData annual 2025 has no Los Angeles row");
const laPublished = csvRecords(annual.text(`summary/${LA}.csv`)).find((r) => r.date === "2025");
check(laPublished, "county-aqi-annual: no 2025 row for Los Angeles County");
const pairs = [
  ["days_with_aqi", "Days with AQI"], ["good_days", "Good Days"], ["moderate_days", "Moderate Days"],
  ["unhealthy_sensitive_days", "Unhealthy for Sensitive Groups Days"], ["unhealthy_days", "Unhealthy Days"],
  ["very_unhealthy_days", "Very Unhealthy Days"], ["hazardous_days", "Hazardous Days"], ["max_aqi", "Max AQI"],
  ["aqi_90th_percentile", "90th Percentile AQI"], ["median_aqi", "Median AQI"], ["days_co", "Days CO"],
  ["days_no2", "Days NO2"], ["days_ozone", "Days Ozone"], ["days_pm25", "Days PM2.5"], ["days_pm10", "Days PM10"],
]; // prettier-ignore
same(
  Object.fromEntries(pairs.map(([p]) => [p, Number(laPublished[p])])),
  Object.fromEntries(pairs.map(([p, s]) => [p, Number(laAnnual[s])])),
  "county-aqi-annual: Los Angeles County 2025",
);

// 2. counties in 2025 (every US name must map through the daily file's crosswalk)
const annualCodes = new Set();
for (const r of annualUs) {
  const code = codeOf.get(`${r.State.trim()}|${r.County.trim()}`);
  check(code, `no daily-file code for ${r.State}, ${r.County}`);
  if (code !== "23901") annualCodes.add(code);
}
const publishedAnnual2025 = annual
  .paths()
  .filter((p) => /^summary\/us-county-[0-9]{5}\.csv$/.test(p))
  .filter((p) => annual.text(p).split("\n").some((l) => l.startsWith("2025,"))).length;
same(publishedAnnual2025, annualCodes.size, "county-aqi-annual: counties with a 2025 summary");

// 3. the 1980 maximum, and the annual maximum agreeing with the daily file for Los Angeles
const annual1980Zip = await original(`${AIRDATA}/annual_aqi_by_county_1980.zip`);
sameAsPublished(annual, "airdata-1980", annual1980Zip);
const max1980 = Math.max(...csvRecords(unzipOnly(annual1980Zip)).filter((r) => !["Country Of Mexico", "Canada"].includes(r.State.trim())).map((r) => Number(r["Max AQI"])));
let publishedMax1980 = -1;
for (const p of annual.paths().filter((x) => /^summary\/us-county-[0-9]{5}\.csv$/.test(x))) {
  const row = csvRecords(annual.text(p)).find((r) => r.date === "1980");
  if (row) publishedMax1980 = Math.max(publishedMax1980, Number(row.max_aqi));
}
same(publishedMax1980, max1980, "county-aqi-annual: highest county max AQI of 1980");
const laDailyMax = Math.max(...source2025.filter((r) => r["State Code"] === "06" && r["County Code"] === "037").map((r) => Number(r.AQI)));
same(Number(laPublished.max_aqi), laDailyMax, "county-aqi-annual: Los Angeles 2025 max AQI equals the daily file's maximum");

console.log(`AIR VERIFIED (${facts.length} facts re-derived from EPA AirData and AirNow: ${facts.join("; ")})`);
