// Independent check of what `datasites run housingdata --target local` published.
// It downloads the sources again and re-derives facts with its own code: its own CSV splitting for the Building
// Permits Survey, and its own XLSX reader (zip directory, inflate, shared strings) for FHFA's workbooks. No
// pipeline code is used. The state permit totals are also checked against BPS's separately published state files.
//   node scripts/verify/housing.mjs                      prints HOUSING VERIFIED ... when everything matches
//   node scripts/verify/housing.mjs --negative-control   corrupts one expected value, so it must FAIL
import { createHash } from "node:crypto";
import { check, localDataset, unzip } from "./lib.mjs";

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
  // ACS 5-year table B25064 for the United States (data.census.gov), median gross rent in dollars.
  usRent: { 2024: 1413 },
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

// --- housing-costs ---------------------------------------------------------------------------------------

const B25070_BURDENED = ["B25070_007", "B25070_008", "B25070_009", "B25070_010"];

/** One ACS 5-year release, straight from the Census Data API (its own request; no pipeline code). */
async function acs(year, forClause, inClause) {
  const key = process.env.CENSUS_API_KEY;
  check(key, "housing-costs: set CENSUS_API_KEY to re-read the Census Data API");
  const counts = ["B25003_001", "B25003_002", "B25070_001", "B25070_011", ...B25070_BURDENED];
  const vars = ["B25064_001E", "B25077_001E", ...counts.flatMap((v) => [`${v}E`, `${v}M`])];
  const q = new URLSearchParams({ get: vars.join(","), for: forClause, key });
  if (inClause) q.set("in", inClause);
  let body;
  try {
    body = await download(`https://api.census.gov/data/${year}/acs/acs5?${q}`);
  } catch {
    check(false, `Census ACS ${year} request failed`); // never print the URL: it carries the key
  }
  const [header, ...rows] = JSON.parse(body.toString("utf8"));
  return rows.map((r) => Object.fromEntries(header.map((h, i) => [h, r[i]])));
}

/** Census's derived-proportion formula, written out again here: share and margin in percent, one decimal. */
function derivedShare(num, numMoe, den, denMoe) {
  if (!(den > 0)) return [null, null];
  const p = num / den;
  const minus = numMoe ** 2 - p ** 2 * denMoe ** 2;
  const moe = Math.sqrt(minus >= 0 ? minus : numMoe ** 2 + p ** 2 * denMoe ** 2) / den;
  const round1 = (x) => Math.round(x * 1000) / 10;
  return [round1(p), round1(moe)];
}

const USPS = {
  "01": "al", "02": "ak", "04": "az", "05": "ar", "06": "ca", "08": "co", "09": "ct", 10: "de", 11: "dc", 12: "fl", 13: "ga",
  15: "hi", 16: "id", 17: "il", 18: "in", 19: "ia", 20: "ks", 21: "ky", 22: "la", 23: "me", 24: "md", 25: "ma", 26: "mi",
  27: "mn", 28: "ms", 29: "mo", 30: "mt", 31: "ne", 32: "nv", 33: "nh", 34: "nj", 35: "nm", 36: "ny", 37: "nc", 38: "nd",
  39: "oh", 40: "ok", 41: "or", 42: "pa", 44: "ri", 45: "sc", 46: "sd", 47: "tn", 48: "tx", 49: "ut", 50: "vt", 51: "va",
  53: "wa", 54: "wv", 55: "wi", 56: "wy", 72: "pr",
};

async function verifyHousingCosts() {
  const ds = localDataset("housingdata", "housing-costs");
  const year = ds.doc.coverage.end;
  const published = (series, loc) => ds.json(`${series}/${loc}.json`).data.find((d) => d.date === year);
  const n = Number;
  const median = (s) => (n(s) < 0 ? null : n(s)); // Census's "no value" sentinels are large negative numbers
  const same = (a, b) => (a === null || b === null ? a === b : Math.abs(a - b) < 0.051);
  const states = await acs(year, "state:*");
  const counties = (await acs(year, "county:*", "state:06,36,48")).filter((_, i) => i % 7 === 0); // a sample
  let checked = 0;
  for (const r of [...states, ...counties]) {
    const usps = USPS[r.state] ?? USPS[n(r.state)];
    check(usps, `unknown state FIPS ${r.state}`);
    const loc = r.county ? `us-county-${r.state}${r.county}` : `us-${usps}`;
    const rent = published("median-gross-rent", loc);
    check(rent.value === median(r.B25064_001E), `${loc} ${year}: median rent ${rent.value}, Census ${r.B25064_001E}`);
    const home = published("median-home-value", loc);
    check(home.value === median(r.B25077_001E), `${loc} ${year}: median home value ${home.value}, Census ${r.B25077_001E}`);
    const [bShare, bMoe] = derivedShare(
      B25070_BURDENED.reduce((s, v) => s + n(r[`${v}E`]), 0),
      Math.sqrt(B25070_BURDENED.reduce((s, v) => s + n(r[`${v}M`]) ** 2, 0)),
      n(r.B25070_001E) - n(r.B25070_011E),
      Math.sqrt(n(r.B25070_001M) ** 2 + n(r.B25070_011M) ** 2),
    );
    const b = published("rent-burdened-share", loc);
    check(same(b.value, bShare) && same(b.margin_of_error, bMoe), `${loc} ${year}: rent-burdened ${b.value}±${b.margin_of_error}, re-derived ${bShare}±${bMoe}`);
    const [oShare, oMoe] = derivedShare(n(r.B25003_002E), n(r.B25003_002M), n(r.B25003_001E), n(r.B25003_001M));
    const o = published("owner-occupied-share", loc);
    check(same(o.value, oShare) && same(o.margin_of_error, oMoe), `${loc} ${year}: owner-occupied ${o.value}±${o.margin_of_error}, re-derived ${oShare}±${oMoe}`);
    checked++;
  }
  const us = published("median-gross-rent", "usa").value;
  const expectedRent = EXPECTED.usRent[year] + (NEGATIVE ? 1 : 0);
  check(us === expectedRent, `US median gross rent ${year}: published ${us}, expected ${expectedRent}`);
  check(ds.doc.coverage.start === "2009", `published coverage ${JSON.stringify(ds.doc.coverage)}`);
  return `housing-costs ${ds.doc.version}: ${checked} states and counties re-derived from the Census Data API for ${year}; US median gross rent ${us}`;
}

// --- mortgage-lending ------------------------------------------------------------------------------------

// The CFPB's own aggregation API (Data Browser) counts the same loan-level records. It serves HMDA's latest
// revision of a year, while the dataset counts the first snapshot, so the two can differ a little: up to 2%.
const HMDA_API = "https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations";
const HMDA_PURPOSES = { 1: "home-purchase", 31: "refinance" };

async function verifyMortgageLending() {
  const ds = localDataset("housingdata", "mortgage-lending");
  const years = Object.keys(ds.doc.files).filter((p) => /^table\/\d{4}\.json$/.test(p)).map((p) => p.slice(6, 10)).sort();
  check(years[0] === "2018" && years.length >= 7, `mortgage-lending years ${years}`);
  const year = years.at(-1);
  const rows = ds.json(`table/${year}.json`).rows;
  const at = (loc, purpose) => rows.find((r) => r.location === loc && r.loan_purpose === purpose);
  let compared = 0;
  for (const [usps, loc] of [["MD", "us-md"], ["CA", "us-ca"], ["TX", "us-tx"], ["WY", "us-wy"], ["DC", "us-dc"]]) {
    const q = new URLSearchParams({ years: year, states: usps, actions_taken: "1", loan_purposes: Object.keys(HMDA_PURPOSES).join(",") });
    const api = JSON.parse((await download(`${HMDA_API}?${q}`)).toString("utf8")).aggregations;
    for (const [code, purpose] of Object.entries(HMDA_PURPOSES)) {
      const theirs = api.find((a) => a.loan_purposes === code);
      const ours = at(loc, purpose);
      check(theirs && ours, `${loc} ${year} ${purpose}: missing (API ${!!theirs}, published ${!!ours})`);
      const off = Math.abs(ours.originations - theirs.count) / theirs.count;
      check(off <= 0.02, `${loc} ${year} ${purpose}: ${ours.originations} loans published, CFPB API ${theirs.count} (${(off * 100).toFixed(2)}% apart)`);
      const offAmount = Math.abs(ours.originated_amount - theirs.sum) / theirs.sum;
      check(offAmount <= 0.02, `${loc} ${year} ${purpose}: $${ours.originated_amount} published, CFPB API $${theirs.sum}`);
      compared++;
    }
  }
  // Counties never add up to more than their state (records without a county count only for the state).
  const byState = new Map();
  for (const r of rows.filter((r) => r.location.startsWith("us-county-") && r.loan_purpose === "home-purchase")) {
    const st = r.location.slice(10, 12);
    byState.set(st, (byState.get(st) ?? 0) + r.originations);
  }
  const md = byState.get("24");
  check(md <= at("us-md", "home-purchase").originations, `Maryland counties ${md} > state ${at("us-md", "home-purchase").originations}`);
  const usa = at("usa", "home-purchase").originations + (NEGATIVE ? 1 : 0);
  const states = rows.filter((r) => /^us-[a-z]{2}$/.test(r.location) && r.loan_purpose === "home-purchase").reduce((s, r) => s + r.originations, 0);
  check(states <= usa && usa - states < usa * 0.01, `${year}: states add up to ${states} home-purchase loans, the nation ${usa}`);
  return `mortgage-lending ${ds.doc.version}: ${years.length} years; ${compared} state figures for ${year} within 2% of the CFPB's API; US ${year} home-purchase loans ${usa}`;
}

const results = [await verifyHousePrices(), await verifyPermits(), await verifyHousingCosts(), await verifyMortgageLending()];
console.log(`HOUSING VERIFIED (${results.join("; ")})`);
