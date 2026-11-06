// Independent check of what `datasites run housingdata --target local` published.
// It downloads the sources again and re-derives facts with its own code: its own CSV splitting for the Building
// Permits Survey, and its own XLSX reader (zip directory, inflate, shared strings) for FHFA's workbooks. No
// pipeline code is used. The state permit totals are also checked against BPS's separately published state files.
//   node scripts/verify/housing.mjs                      prints HOUSING VERIFIED ... when everything matches
//   node scripts/verify/housing.mjs --negative-control   corrupts one expected value, so it must FAIL
import { createHash } from "node:crypto";
import { inflateRawSync } from "node:zlib";
import { check, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const UA = "datasites-verify/1 (+https://housingdata.dev)";
const BPS = "https://www2.census.gov/econ/bps";
const FHFA = "https://www.fhfa.gov/hpi/download/annual";

// Facts from the publishers' own releases.
const EXPECTED = {
  // Census Bureau annual totals for the US (sum of the state files): 2005 was the peak, 2009 the trough.
  usUnits: { 2005: 2155316, 2009: 582963 },
  // FHFA hpi_at_county.xlsx, Los Angeles County (06037), 2025: annual change, HPI, 1990 base, 2000 base.
  la2025: [1.7, 2386.19, 410.35, 403.74],
  // Alaska 2013: the county file adds up to 26 units less than BPS's state file (1,053 vs 1,079).
  stateExceptions: { "2013|02": 26 },
};
if (NEGATIVE) EXPECTED.la2025[1] += 1;

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

async function download(url) {
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, { headers: { "user-agent": UA }, signal: AbortSignal.timeout(120_000) });
    } catch (err) {
      if (attempt < 3) continue;
      check(false, `could not fetch ${url}: ${err.message}`);
    }
    if ([429, 500, 502, 503, 504].includes(res.status) && attempt < 3) {
      await new Promise((r) => setTimeout(r, 3000 * (attempt + 1)));
      continue;
    }
    check(res.ok, `${url} answered HTTP ${res.status}`);
    return Buffer.from(await res.arrayBuffer());
  }
}

// --- building-permits ------------------------------------------------------------------------------------

/** A BPS annual file -> [{ state, county, units }], units summed over the four building sizes (estimates). */
function bpsRows(buf) {
  const rows = [];
  for (const line of buf.toString("latin1").split(/\r?\n/).slice(2)) {
    const f = line.split(",").map((s) => s.trim());
    if (f.length < 18 || !/^\d+$/.test(f[1])) continue;
    rows.push({ state: f[1].padStart(2, "0"), county: f[2].padStart(3, "0"), name: f[5], units: +f[7] + +f[10] + +f[13] + +f[16] });
  }
  return rows;
}

async function verifyPermits() {
  const ds = localDataset("housingdata", "building-permits");
  const raw = new Map(ds.doc.source.raw.map((r) => [r.name, r.sha256]));
  const years = [...raw.keys()].map((n) => +n.slice(4)).sort((a, b) => a - b);
  check(years[0] === 1990 && years.length === years.at(-1) - 1989, `published from ${years[0]} to ${years.at(-1)} with gaps`);
  const published = new Map(ds.paths().filter((p) => p.startsWith("permits/") && p.endsWith(".json")).map((p) => [p.slice(8, -5), ds.json(p).data]));
  const value = (loc, year) => published.get(loc)?.find((r) => r.date === String(year))?.units;
  const stateId = new Map(); // state FIPS -> us-xx, from the published state files' location names
  for (const loc of published.keys()) if (/^us-[a-z]{2}$/.test(loc)) stateId.set(loc, loc);

  let counties = 0;
  let exactStates = 0;
  for (const year of years) {
    const file = await download(`${BPS}/County/co${year}a.txt`);
    check(sha256(file) === raw.get(`bps-${year}`), `co${year}a.txt changed since the published version (re-run the pipeline)`);
    const rows = bpsRows(file).filter((r) => !["43", "52"].includes(r.state));
    // One row per county code (2014-2015 list a few counties twice with the same figures).
    const byCode = new Map(rows.map((r) => [r.state + r.county, r]));
    const total = [...byCode.values()].reduce((s, r) => s + r.units, 0);
    check(value("usa", year) === total, `${year}: published US total ${value("usa", year)}, county file adds up to ${total}`);
    const real = [...byCode.values()].filter((r) => !["000", "099", "999"].includes(r.county));
    for (const r of real) {
      const got = value(`us-county-${r.state}${r.county}`, year);
      check(got === r.units, `${year} ${r.name} (${r.state}${r.county}): published ${got}, file ${r.units}`);
      counties++;
    }
    if (EXPECTED.usUnits[year] !== undefined) {
      check(total === EXPECTED.usUnits[year], `${year}: US total ${total}, Census published ${EXPECTED.usUnits[year]}`);
    }
    if (year >= 2004) {
      // BPS's own state file must equal the county sum for every state and DC (territories aren't in the county files).
      const stateFile = await download(`${BPS}/State/st${year}a.txt`);
      const sums = new Map();
      for (const r of byCode.values()) sums.set(r.state, (sums.get(r.state) ?? 0) + r.units);
      for (const line of stateFile.toString("latin1").split(/\r?\n/).slice(2)) {
        const f = line.split(",").map((s) => s.trim());
        if (f.length < 17 || !/^\d+$/.test(f[1])) continue;
        const fips = f[1].padStart(2, "0");
        if (!sums.has(fips)) continue; // Puerto Rico, the Virgin Islands, American Samoa
        const official = +f[6] + +f[9] + +f[12] + +f[15];
        const gap = EXPECTED.stateExceptions[`${year}|${fips}`] ?? 0;
        check(official - sums.get(fips) === gap, `${year} ${f[4]}: state file ${official}, county files add up to ${sums.get(fips)}`);
        exactStates++;
      }
    }
  }
  return `building-permits ${ds.doc.version}: ${counties} county-years and the US total for ${years.length} years re-derived; ${exactStates} state-years equal BPS's state files (2004-${years.at(-1)}); US 2005 = ${EXPECTED.usUnits[2005]}, 2009 = ${EXPECTED.usUnits[2009]}`;
}

// --- house-prices ----------------------------------------------------------------------------------------

/** The files in a zip archive, by name, from its central directory. */
function unzip(buf) {
  let eocd = buf.length - 22;
  while (eocd >= 0 && buf.readUInt32LE(eocd) !== 0x06054b50) eocd--;
  check(eocd >= 0, "xlsx: no zip end-of-directory record");
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const files = new Map();
  for (let i = 0; i < count; i++) {
    check(buf.readUInt32LE(p) === 0x02014b50, "xlsx: bad central directory entry");
    const method = buf.readUInt16LE(p + 10);
    const size = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.toString("utf8", p + 46, p + 46 + nameLen);
    const dataStart = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const data = buf.subarray(dataStart, dataStart + size);
    files.set(name, method === 8 ? inflateRawSync(data) : data);
    p += 46 + nameLen + extraLen + commentLen;
  }
  return files;
}

const unescapeXml = (s) =>
  s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");

/** The first worksheet of a workbook as rows of cell text, by column letter. */
function sheetRows(buf) {
  const files = unzip(buf);
  const shared = [];
  const ss = files.get("xl/sharedStrings.xml")?.toString("utf8") ?? "";
  for (const si of ss.matchAll(/<si>([\s\S]*?)<\/si>/g)) {
    shared.push([...si[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((m) => unescapeXml(m[1])).join(""));
  }
  const sheet = files.get("xl/worksheets/sheet1.xml").toString("utf8");
  const rows = [];
  for (const row of sheet.matchAll(/<row[^>]*>([\s\S]*?)<\/row>/g)) {
    const cells = {};
    for (const c of row[1].matchAll(/<c r="([A-Z]+)\d+"([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
      const [, col, attrs, inner = ""] = c;
      const v = inner.match(/<v>([\s\S]*?)<\/v>/)?.[1];
      const inline = inner.match(/<t[^>]*>([\s\S]*?)<\/t>/)?.[1];
      cells[col] = /t="s"/.test(attrs) ? shared[+v] : v !== undefined ? unescapeXml(v) : inline !== undefined ? unescapeXml(inline) : "";
    }
    rows.push(cells);
  }
  return rows;
}

async function verifyHousePrices() {
  const ds = localDataset("housingdata", "house-prices");
  const raw = new Map(ds.doc.source.raw.map((r) => [r.name, r.sha256]));
  const published = (loc) => (ds.entry(`all-transactions/${loc}.json`) ? ds.json(`all-transactions/${loc}.json`).data : null);
  const num = (s) => (s === undefined || s === "" || s === "." ? null : Number(s));
  let checked = 0;
  let places = 0;
  for (const [level, key] of [["county", "C"], ["state", "C"]]) {
    const file = await download(`${FHFA}/hpi_at_${level}.xlsx`);
    check(sha256(file) === raw.get(level), `hpi_at_${level}.xlsx changed since the published version (re-run the pipeline)`);
    const rows = sheetRows(file);
    const header = rows.findIndex((r) => r.A === "State");
    check(header >= 0 && rows[header].D === "Year" && rows[header].F === "HPI", `hpi_at_${level}.xlsx: header row not found`);
    const byPlace = new Map();
    for (const r of rows.slice(header + 1)) {
      if (!r[key]) continue;
      const values = [num(r.E), num(r.F), num(r.G), num(r.H)];
      if (values.every((v) => v === null)) continue; // a year FHFA left empty is not published
      const loc = level === "county" ? `us-county-${r[key]}` : null;
      const id = loc ?? `us-${String(r.B).toLowerCase()}`;
      if (!byPlace.has(id)) byPlace.set(id, []);
      byPlace.get(id).push({ date: r.D, values });
    }
    for (const [loc, years] of byPlace) {
      const pub = published(loc);
      check(pub && pub.length === years.length, `${loc}: published ${pub?.length} years, FHFA lists ${years.length}`);
      const byYear = new Map(pub.map((p) => [p.date, p]));
      for (const y of years) {
        const p = byYear.get(y.date);
        const got = [p?.annual_change ?? null, p?.hpi ?? null, p?.hpi_1990 ?? null, p?.hpi_2000 ?? null];
        check(JSON.stringify(got) === JSON.stringify(y.values), `${loc} ${y.date}: published ${got}, FHFA ${y.values}`);
        checked++;
      }
      places++;
    }
    if (level === "county") {
      const la = byPlace.get("us-county-06037").find((y) => y.date === "2025").values;
      check(JSON.stringify(la) === JSON.stringify(EXPECTED.la2025), `Los Angeles 2025 in FHFA's file is ${la}, expected ${EXPECTED.la2025}`);
      const pub = published("us-county-06037").find((p) => p.date === "2025");
      check(pub.hpi === EXPECTED.la2025[1], `Los Angeles 2025 published HPI ${pub.hpi}, expected ${EXPECTED.la2025[1]}`);
    }
  }
  const coverage = ds.doc.coverage;
  check(coverage.start === "1975" && coverage.end === "2025", `published coverage ${JSON.stringify(coverage)}`);
  return `house-prices ${ds.doc.version}: ${checked} place-years in ${places} counties and states re-derived from FHFA's workbooks; Los Angeles 2025 HPI ${EXPECTED.la2025[1]}`;
}

const results = [await verifyHousePrices(), await verifyPermits()];
console.log(`HOUSING VERIFIED (${results.join("; ")})`);
