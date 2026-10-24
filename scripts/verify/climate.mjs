// G4/G5: every climatedata dataset published to the local bucket matches facts re-derived here from the
// original sources, with parsing written independently of the Python pipelines:
//   - its own fixed-width reader for nClimDiv, and its own NCEI-code-to-FIPS mapping (the alphabetical state
//     order from the geo registry plus NCEI's county-to-climdivs.txt crosswalk, rather than the pipeline's table);
//   - its own CSV readers for GISTEMP, HadCRUT5 and NOAA GML; its own POWER request and capital lookup.
// For each dataset: a value for a known place and month, a row or location count, and an extreme.
//   node scripts/verify/climate.mjs                     prints CLIMATE VERIFIED ... when everything matches
//   node scripts/verify/climate.mjs --negative-control  corrupts one expected value, so it must FAIL
// Large source files are cached under the OS temp folder and revalidated with conditional GETs.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const SITE = "climatedata";
const UA = "datasites-verify (+mailto:hello@climatedata.dev)";
const CACHE = join(tmpdir(), "datasites-verify-climate");
const NC = "https://www.ncei.noaa.gov/monitoring-content/data/us/climdiv/monthly/current/";
const facts = [];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const round2 = (x) => Math.round(x * 100) / 100;
const close = (a, b, tol = 1e-9) => typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= tol;

/** GET with retries; with `cache`, keeps the body on disk and revalidates it with If-None-Match/If-Modified-Since. */
async function original(url, { cache = true } = {}) {
  mkdirSync(CACHE, { recursive: true });
  const path = join(CACHE, sha256(url).slice(0, 32));
  const metaPath = `${path}.json`;
  const headers = { "user-agent": UA };
  let meta = null;
  if (cache && existsSync(path) && existsSync(metaPath)) {
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
    if (cache) {
      writeFileSync(path, body);
      writeFileSync(metaPath, JSON.stringify({ url, etag: res.headers.get("etag"), lastModified: res.headers.get("last-modified") }));
    }
    return body;
  }
}

/** Fails clearly if the source moved on after the published version was built (then re-run the pipeline). */
function sameAsPublished(ds, name, body, normalize = (b) => b.toString("latin1")) {
  const entry = (ds.doc.source.raw ?? []).find((r) => r.name === name);
  check(entry, `${ds.doc.dataset}: version ${ds.doc.version} lists no raw input named ${name}`);
  if (sha256(body) === entry.sha256) return;
  const archived = entry.key ? join(ROOT, ".data", "r2", entry.key) : null;
  const same = archived && existsSync(archived) && normalize(readFileSync(archived)) === normalize(body);
  check(
    same,
    `${ds.doc.dataset}: the source behind '${name}' changed after version ${ds.doc.version} was built; ` +
      "re-run `uv run datasites run climatedata --target local` (from pipelines/) and verify again",
  );
}

function rowsOf(ds, path) {
  const doc = ds.json(path);
  check(Array.isArray(doc.data), `${ds.doc.dataset}: ${path} has no data array`);
  return doc;
}

function byDate(rows) {
  return new Map(rows.map((r) => [r.date, r]));
}

function expectRow(label, got, want) {
  check(got, `${label}: not published`);
  for (const [key, value] of Object.entries(want)) {
    const ok = typeof value === "number" ? close(got[key], value, 1e-6) : got[key] === value;
    check(ok, `${label}: ${key} is ${JSON.stringify(got[key])}, the source gives ${JSON.stringify(value)}`);
  }
}

// --- nClimDiv ---------------------------------------------------------------------------------

/** code -> year -> 12 values (null where missing). County records: 5-digit code; statewide: 4 (state + division). */
function parseClimdiv(body, { codeWidth, element, precipitation }) {
  const out = new Map();
  const start = codeWidth + 6;
  for (const raw of body.toString("latin1").split("\n")) {
    const line = raw.trimEnd();
    if (!line) continue;
    check(line.length === start + 84, `nClimDiv record has ${line.length} characters: ${line.slice(0, 40)}`);
    check(line.slice(codeWidth, codeWidth + 2) === element, `nClimDiv record with element ${line.slice(codeWidth, codeWidth + 2)}, expected ${element}`);
    const code = line.slice(0, codeWidth);
    const year = Number(line.slice(codeWidth + 2, codeWidth + 6));
    const values = [];
    for (let m = 0; m < 12; m++) {
      const v = Number(line.slice(start + 7 * m, start + 7 * (m + 1)));
      values.push(precipitation ? (v < 0 ? null : v) : v <= -99 ? null : v);
    }
    if (!out.has(code)) out.set(code, new Map());
    out.get(code).set(year, values);
  }
  return out;
}

function monthsOf(record) {
  const out = [];
  for (const [year, values] of [...record.entries()].sort((a, b) => a[0] - b[0])) {
    values.forEach((v, m) => out.push({ date: `${year}-${String(m + 1).padStart(2, "0")}`, month: m, value: v }));
  }
  const last = out.findLastIndex((r) => r.value !== null);
  return out.slice(0, last + 1);
}

function rankAmong(record, month, value) {
  let higher = 0;
  for (const values of record.values()) if (values[month] !== null && values[month] > value) higher++;
  return higher + 1;
}

function hottest(record, month) {
  let best = null;
  for (const [year, values] of record) if (values[month] !== null && (best === null || values[month] > best.value)) best = { year, value: values[month] };
  return best;
}

/** NCEI state codes are the 48 contiguous states in alphabetical order, then 49 Hawaii and 50 Alaska. */
function nceiStates() {
  const registry = JSON.parse(readFileSync(join(ROOT, "packages", "geo", "src", "locations.json"), "utf8")).locations;
  const states = registry.filter((l) => l.level === "us-state" && l.kind === "state");
  const contiguous = states.filter((s) => !["AK", "HI"].includes(s.usps)).sort((a, b) => (a.name < b.name ? -1 : 1));
  check(contiguous.length === 48, `registry has ${contiguous.length} contiguous states`);
  const table = new Map(contiguous.map((s, i) => [String(i + 1).padStart(2, "0"), s]));
  table.set("49", states.find((s) => s.usps === "HI"));
  table.set("50", states.find((s) => s.usps === "AK"));
  return table;
}

async function countyClimate() {
  const ds = localDataset(SITE, "county-climate");
  const date = (await original(`${NC}procdate.txt`, { cache: false })).toString().trim();
  const tmpc = await original(`${NC}climdiv-tmpccy-v1.0.0-${date}`);
  const norm = await original(`${NC}climdiv-norm-tmpccy-v1.0.0-${date}`);
  const pcpn = await original(`${NC}climdiv-pcpncy-v1.0.0-${date}`);
  sameAsPublished(ds, "tavg", tmpc);
  sameAsPublished(ds, "tavg-normals", norm);
  sameAsPublished(ds, "pcpn", pcpn);
  const crosswalkText = (await original(`${NC}county-to-climdivs.txt`)).toString("latin1");

  const states = nceiStates();
  const crosswalk = new Map();
  for (const line of crosswalkText.split("\n")) {
    const m = /^(\d{5}) (\d{5}) \d{4}\s*$/.exec(line.trim());
    if (m) crosswalk.set(m[2], m[1]);
  }
  check(crosswalk.get("18511") === "11001", "county-to-climdivs.txt no longer maps NCDC 18511 to the District of Columbia");
  const fips = (code) => crosswalk.get(code) ?? `${states.get(code.slice(0, 2)).fips}${code.slice(2)}`;

  const temps = parseClimdiv(tmpc, { codeWidth: 5, element: "02" });
  const normals = parseClimdiv(norm, { codeWidth: 5, element: "02" });
  const precip = parseClimdiv(pcpn, { codeWidth: 5, element: "01", precipitation: true });

  // 1. a known place and month: Los Angeles County (NCEI 04037), July 2023
  const la = temps.get("04037");
  const julyValue = la.get(2023)[6];
  const julyNormal = normals.get("04037").get(10)[6]; // period code 0010 = 1991-2020
  const want = { value: julyValue + (NEGATIVE ? 0.1 : 0), normal: julyNormal, anomaly: round2(julyValue - julyNormal), rank: rankAmong(la, 6, julyValue) };
  const laRows = byDate(rowsOf(ds, `tavg/us-county-${fips("04037")}.json`).data);
  expectRow("county-climate tavg/us-county-06037 2023-07", laRows.get("2023-07"), want);
  // the District of Columbia is filed under Maryland (18511) and must be published as 11001
  const dc = byDate(rowsOf(ds, "tavg/us-county-11001.json").data);
  expectRow("county-climate tavg/us-county-11001 2020-01", dc.get("2020-01"), { value: temps.get("18511").get(2020)[0] });
  // precipitation uses a different missing marker (-9.99): Harris County TX (NCEI 41201), August 2017
  const harris = byDate(rowsOf(ds, "pcpn/us-county-48201.json").data);
  expectRow("county-climate pcpn/us-county-48201 2017-08", harris.get("2017-08"), { value: precip.get("41201").get(2017)[7] });

  // 2. counts: every county in the file is a published location; LA has one row per observed month
  const locations = ds.locations();
  const expectedIds = [...temps.keys()].map((c) => `us-county-${fips(c)}`).sort();
  check(Object.keys(locations).length === expectedIds.length, `county-climate has ${Object.keys(locations).length} locations, the source has ${expectedIds.length} counties`);
  for (const id of expectedIds) check(id in locations, `county-climate: ${id} from the source is not published`);
  const ct = Object.entries(locations).filter(([id]) => /^us-county-090(0[1-9]|1[0-5])$/.test(id));
  check(ct.length === 8 && ct.every(([, name]) => name.endsWith(", CT (pre-2022)")), `Connecticut legacy counties: ${JSON.stringify(ct)}`);
  const laMonths = monthsOf(la);
  check(laRows.size === laMonths.length, `tavg/us-county-06037 has ${laRows.size} months, the source has ${laMonths.length}`);
  const harrisMonths = monthsOf(precip.get("41201"));
  check(harris.size === harrisMonths.length, `pcpn/us-county-48201 has ${harris.size} months, the source has ${harrisMonths.length}`);

  // 3. extremes: LA County's warmest July is ranked 1; coverage ends at the source's last observed month
  const record = hottest(la, 6);
  const top = [...laRows.values()].filter((r) => r.date.endsWith("-07") && r.rank === 1);
  check(top.some((r) => r.date === `${record.year}-07` && close(r.value, record.value)), `warmest LA July: source ${record.year} ${record.value}, published ${JSON.stringify(top)}`);
  check(ds.doc.coverage.end === laMonths.at(-1).date, `county-climate coverage ends ${ds.doc.coverage.end}, source ${laMonths.at(-1).date}`);

  facts.push(`county-climate ${expectedIds.length} counties, LA July 2023 ${julyValue}F (rank ${want.rank}), warmest LA July ${record.year}`);
}

async function usClimate() {
  const ds = localDataset(SITE, "us-climate");
  const date = (await original(`${NC}procdate.txt`, { cache: false })).toString().trim();
  const tmpc = await original(`${NC}climdiv-tmpcst-v1.0.0-${date}`);
  const norm = await original(`${NC}climdiv-norm-tmpcst-v1.0.0-${date}`);
  sameAsPublished(ds, "tavg", tmpc);
  sameAsPublished(ds, "tavg-normals", norm);
  const temps = parseClimdiv(tmpc, { codeWidth: 4, element: "02" });
  const normals = parseClimdiv(norm, { codeWidth: 4, element: "02" });
  const states = nceiStates();

  // 1. the contiguous US (code 110), July 2012
  const nation = temps.get("1100");
  const v = nation.get(2012)[6];
  const n = normals.get("1100").get(10)[6];
  const usa = byDate(rowsOf(ds, "tavg/usa.json").data);
  expectRow("us-climate tavg/usa 2012-07", usa.get("2012-07"), { value: v, normal: n, anomaly: round2(v - n), rank: rankAmong(nation, 6, v) });

  // 2. counts: states 001-050 plus the nation, nothing else (no regions)
  const expected = [...temps.keys()].filter((c) => c.endsWith("0") && (Number(c.slice(0, 3)) <= 50 || c === "1100"));
  const ids = expected.map((c) => (c === "1100" ? "usa" : `us-${states.get(c.slice(1, 3)).usps.toLowerCase()}`)).sort();
  check(JSON.stringify(Object.keys(ds.locations()).sort()) === JSON.stringify(ids), `us-climate locations ${Object.keys(ds.locations()).length}, source ${ids.length}`);
  check(usa.size === monthsOf(nation).length, `tavg/usa has ${usa.size} months, the source has ${monthsOf(nation).length}`);

  // 3. extreme: California's (004) warmest July
  const ca = temps.get("0040");
  const record = hottest(ca, 6);
  const top = rowsOf(ds, "tavg/us-ca.json").data.filter((r) => r.date.endsWith("-07") && r.rank === 1);
  check(top.some((r) => r.date === `${record.year}-07` && close(r.value, record.value)), `warmest California July: source ${record.year}, published ${JSON.stringify(top)}`);
  facts.push(`us-climate ${ids.length} areas, US July 2012 ${v}F, warmest California July ${record.year}`);
}

// --- global temperature -----------------------------------------------------------------------

function gistempMonths(body) {
  const lines = body.toString("utf8").split("\n").filter((l) => l.trim());
  check(lines[1].startsWith("Year,Jan,Feb,Mar"), `GISTEMP header: ${lines[1]}`);
  const out = [];
  for (const line of lines.slice(2)) {
    const cells = line.split(",");
    for (let m = 1; m <= 12; m++) if (cells[m] !== "***") out.push({ date: `${cells[0]}-${String(m).padStart(2, "0")}`, value: Number(cells[m]) });
  }
  return out;
}

function hadcrutMonths(body) {
  const [header, ...lines] = body.toString("utf8").trim().split("\n");
  check(header.startsWith("Time,Anomaly (deg C)"), `HadCRUT5 header: ${header}`);
  return lines.map((l) => {
    const [date, value, lower, upper] = l.split(",");
    return { date, value: Number(value), lower: Number(lower), upper: Number(upper) };
  });
}

async function globalTemperature() {
  const ds = localDataset(SITE, "global-temperature");
  const glb = await original("https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv");
  const hc = await original(
    "https://www.metoffice.gov.uk/hadobs/hadcrut5/data/HadCRUT.5.2.0.0/analysis/diagnostics/HadCRUT.5.2.0.0.analysis.summary_series.global.monthly.csv",
  );
  sameAsPublished(ds, "gistemp-world", glb);
  sameAsPublished(ds, "hadcrut5-world", hc);
  const gis = gistempMonths(glb);
  const had = hadcrutMonths(hc);
  const gisPub = byDate(rowsOf(ds, "gistemp/world.json").data);
  const hadPub = byDate(rowsOf(ds, "hadcrut5/world.json").data);

  // 1. a known month in each series
  expectRow("global-temperature gistemp/world 2016-02", gisPub.get("2016-02"), { value: gis.find((r) => r.date === "2016-02").value, baseline: "1951-1980" });
  const h = had.find((r) => r.date === "1998-02");
  expectRow("global-temperature hadcrut5/world 1998-02", hadPub.get("1998-02"), { value: h.value, lower: h.lower, upper: h.upper, baseline: "1961-1990" });
  // 2. counts
  check(gisPub.size === gis.length, `gistemp/world has ${gisPub.size} months, GLB.Ts+dSST.csv has ${gis.length}`);
  check(hadPub.size === had.length, `hadcrut5/world has ${hadPub.size} months, the HadCRUT5 CSV has ${had.length}`);
  // 3. extreme: the warmest month in HadCRUT5
  const hottestMonth = had.reduce((a, b) => (b.value > a.value ? b : a));
  const pubMax = [...hadPub.values()].reduce((a, b) => (b.value > a.value ? b : a));
  check(pubMax.date === hottestMonth.date && close(pubMax.value, hottestMonth.value), `warmest HadCRUT5 month: source ${hottestMonth.date}, published ${pubMax.date}`);
  facts.push(`global-temperature GISTEMP ${gis.length} / HadCRUT5 ${had.length} months, warmest ${hottestMonth.date}`);
}

// --- NOAA GML -----------------------------------------------------------------------------------

function gmlRows(body) {
  const rows = [];
  for (const line of body.toString("utf8").split("\n")) {
    if (!line.trim() || line.startsWith("#") || line.startsWith("year")) continue;
    const [year, month, , average, averageUnc, trend] = line.split(",").map(Number);
    rows.push({ date: `${year}-${String(month).padStart(2, "0")}`, average, averageUnc: averageUnc < 0 ? null : averageUnc, trend });
  }
  return rows;
}

const withoutCreation = (b) => b.toString("utf8").replace(/^# File Creation.*$/m, "");

async function greenhouseGases() {
  const ds = localDataset(SITE, "greenhouse-gases");
  const ch4Body = await original("https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_mm_gl.csv");
  const n2oBody = await original("https://gml.noaa.gov/webdata/ccgg/trends/n2o/n2o_mm_gl.csv");
  sameAsPublished(ds, "ch4", ch4Body, withoutCreation);
  sameAsPublished(ds, "n2o", n2oBody, withoutCreation);
  const ch4 = gmlRows(ch4Body);
  const n2o = gmlRows(n2oBody);
  const ch4Pub = byDate(rowsOf(ds, "ch4/world.json").data);
  const n2oPub = byDate(rowsOf(ds, "n2o/world.json").data);

  const c = ch4.find((r) => r.date === "1990-01");
  expectRow("greenhouse-gases ch4/world 1990-01", ch4Pub.get("1990-01"), { value: c.average, uncertainty: c.averageUnc });
  const t = n2o.find((r) => r.date === "2010-01");
  expectRow("greenhouse-gases n2o-trend/world 2010-01", byDate(rowsOf(ds, "n2o-trend/world.json").data).get("2010-01"), { value: t.trend });
  check(ch4Pub.size === ch4.length && n2oPub.size === n2o.length, `CH4 ${ch4Pub.size}/${ch4.length}, N2O ${n2oPub.size}/${n2o.length} months published/source`);
  const peak = ch4.reduce((a, b) => (b.average > a.average ? b : a));
  const pubPeak = [...ch4Pub.values()].reduce((a, b) => (b.value > a.value ? b : a));
  check(pubPeak.date === peak.date && close(pubPeak.value, peak.average), `highest CH4: source ${peak.date} ${peak.average}, published ${pubPeak.date} ${pubPeak.value}`);
  const latest = ch4.at(-1);
  check(ch4Pub.get(latest.date)?.uncertainty === latest.averageUnc, `ch4 ${latest.date}: uncertainty should be ${latest.averageUnc}`);
  facts.push(`greenhouse-gases CH4 ${ch4.length} / N2O ${n2o.length} months, CH4 peak ${peak.average} ppb (${peak.date})`);
}

async function co2Monthly() {
  const ds = localDataset(SITE, "co2-monthly");
  const body = await original("https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv");
  sameAsPublished(ds, "noaa-mlo", body, withoutCreation);
  const rows = [];
  for (const line of body.toString("utf8").split("\n")) {
    if (!line.trim() || line.startsWith("#") || line.startsWith("year")) continue;
    const [year, month, , average] = line.split(",").map(Number);
    rows.push({ date: `${year}-${String(month).padStart(2, "0")}`, average });
  }
  const pub = byDate(rowsOf(ds, "average/noaa-mlo.json").data);
  expectRow("co2-monthly average/noaa-mlo 1958-03", pub.get("1958-03"), { value: rows[0].average });
  check(pub.size === rows.length, `average/noaa-mlo has ${pub.size} months, the source has ${rows.length}`);
  const peak = rows.reduce((a, b) => (b.average > a.average ? b : a));
  check(close(Math.max(...[...pub.values()].map((r) => r.value)), peak.average), `highest Mauna Loa month should be ${peak.average}`);
  facts.push(`co2-monthly ${rows.length} months, peak ${peak.average} ppm`);
}

// --- capitals -------------------------------------------------------------------------------------

async function capitalsClimate() {
  const ds = localDataset(SITE, "world-capitals");
  const places = JSON.parse(
    (await original("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_populated_places.geojson")).toString("utf8"),
  );
  const capitals = places.features.filter((f) => f.properties.FEATURECLA === "Admin-0 capital");
  // countries with an ISO 3166 code (Natural Earth writes -99 for Kosovo and Somaliland)
  const countries = new Set(capitals.filter((f) => /^[A-Z]{2}$/.test(f.properties.ISO_A2)).map((f) => f.properties.ADM0_A3.toLowerCase()));
  const published = Object.keys(ds.locations());
  check(published.length === countries.size && published.every((id) => countries.has(id)), `world-capitals has ${published.length} countries, Natural Earth has ${countries.size} capitals with ISO codes`);

  const paris = capitals.find((f) => f.properties.NAME === "Paris");
  const [lon, lat] = paris.geometry.coordinates;
  const query = new URLSearchParams({
    parameters: "T2M",
    community: "AG",
    longitude: lon.toFixed(4),
    latitude: lat.toFixed(4),
    start: "1981",
    end: String(new Date().getUTCFullYear()),
    format: "JSON",
  });
  const power = JSON.parse((await original(`https://power.larc.nasa.gov/api/temporal/monthly/point?${query}`, { cache: false })).toString("utf8"));
  const t2m = power.properties.parameter.T2M;
  const fill = power.header.fill_value;
  const julys = Object.entries(t2m).filter(([k, v]) => k.endsWith("07") && k.length === 6 && v !== fill).map(([k, v]) => ({ year: Number(k.slice(0, 4)), value: v }));
  const v2020 = t2m["202007"];
  const base = julys.filter((j) => j.year >= 1991 && j.year <= 2020);
  check(base.length === 30, `POWER has ${base.length} Paris Julys in 1991-2020`);
  const normal = base.reduce((s, j) => s + j.value, 0) / 30; // published rounded to 0.01 (either way on a tie)
  const pub = byDate(rowsOf(ds, "tavg/fra.json").data);
  const row = pub.get("2020-07");
  expectRow("world-capitals tavg/fra 2020-07", row, { value: v2020, rank: 1 + julys.filter((j) => j.value > v2020).length, capital: "Paris" });
  check(Math.abs(row.normal - normal) <= 0.005 + 1e-9, `world-capitals tavg/fra July normal is ${row.normal}, the 1991-2020 mean of POWER's values is ${normal}`);
  check(Math.abs(row.anomaly - (v2020 - row.normal)) <= 0.005 + 1e-9, `world-capitals tavg/fra 2020-07 anomaly ${row.anomaly} is not value minus normal`);
  const months = Object.entries(t2m).filter(([k, v]) => !k.endsWith("13") && v !== fill).length;
  check(pub.size === months, `tavg/fra has ${pub.size} months, POWER returns ${months}`);
  const warmest = julys.reduce((a, b) => (b.value > a.value ? b : a));
  const top = [...pub.values()].filter((r) => r.date.endsWith("-07") && r.rank === 1);
  check(top.some((r) => r.date === `${warmest.year}-07` && close(r.value, warmest.value)), `warmest Paris July: POWER ${warmest.year}, published ${JSON.stringify(top)}`);
  facts.push(`world-capitals ${countries.size} capitals, Paris July 2020 ${v2020}C, warmest Paris July ${warmest.year}`);
}

await countyClimate();
await usClimate();
await globalTemperature();
await greenhouseGases();
await co2Monthly();
await capitalsClimate();
console.log(`CLIMATE VERIFIED (${facts.join("; ")})`);
