// Independent check of what justicedata published. It asks the FBI's CDE API again for every state and both
// offense groups, and re-derives each published row with its own code (no pipeline code is used): state counts,
// rates, coverage and populations, and the national rates repeated in every state's answer.
//   node scripts/verify/justice.mjs                       checks .data/r2 (after `datasites run justicedata --target local`)
//   node scripts/verify/justice.mjs --api <base>          checks a deployed API instead, e.g. the staging Worker
//   node scripts/verify/justice.mjs --negative-control    corrupts one expected value, so it must FAIL
// Needs DATA_GOV_API_KEY (an api.data.gov key; the USGS water key works) in the environment: 102 requests.
import { check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const apiArg = process.argv.indexOf("--api");
const API_BASE = apiArg > 0 ? process.argv[apiArg + 1]?.replace(/\/$/, "") : null;
const UA = "datasites-verify/1 (+https://justicedata.dev)";
const CDE = "https://api.usa.gov/crime/fbi/cde";
const KEY = process.env.DATA_GOV_API_KEY ?? process.env.USGS_WATERDATA_API_KEY;
if (!KEY) fail("set DATA_GOV_API_KEY (an api.data.gov key) to verify justicedata");

const SERIES = { V: "violent-crime", P: "property-crime" };
// The 50 states and DC, as the CDE names them in its answers.
const STATES = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
  DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
  IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
  MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana",
  NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
  RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};

// Facts from the CDE itself (captured 2026-11-06): Minnesota, violent crime, January 2024.
const EXPECTED = { mnViolentJan2024: { offenses: 1169, clearances: 547, coverage_pct: 99.89 } };
if (NEGATIVE) EXPECTED.mnViolentJan2024.offenses += 1;

async function getJson(url, headers = {}) {
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, { headers: { "user-agent": UA, ...headers }, signal: AbortSignal.timeout(120_000) });
    } catch (err) {
      if (attempt < 3) continue;
      fail(`could not fetch ${url.split("?")[0]}: ${err.message}`);
    }
    if ([429, 500, 502, 503, 504].includes(res.status) && attempt < 3) {
      await new Promise((r) => setTimeout(r, 5000 * (attempt + 1)));
      continue;
    }
    if (res.status === 404) return null;
    check(res.ok, `${url.split("?")[0]} answered HTTP ${res.status}`);
    return res.json();
  }
}

/** Published rows of one series and place, from the local bucket or a deployed API. */
const published = API_BASE
  ? async (series, loc) => (await getJson(`${API_BASE}/v1/crime-monthly/${series}/${loc}.json?site=justicedata`))?.data ?? null
  : (() => {
      const ds = localDataset("justicedata", "crime-monthly");
      return async (series, loc) => (ds.entry(`${series}/${loc}.json`) ? ds.json(`${series}/${loc}.json`).data : null);
    })();

const month = (key) => `${key.slice(3)}-${key.slice(0, 2)}`; // "MM-YYYY" -> "YYYY-MM"
const same = (a, b) => (a ?? null) === (b ?? null) || (typeof a === "number" && typeof b === "number" && Math.abs(a - b) < 1e-9);

const now = new Date();
const to = `${String(now.getUTCMonth() + 1).padStart(2, "0")}-${now.getUTCFullYear()}`;
let rows = 0;
const national = new Map(); // series|month -> expected national row
for (const [code, series] of Object.entries(SERIES)) {
  for (const [usps, name] of Object.entries(STATES)) {
    const doc = await getJson(`${CDE}/summarized/state/${usps}/${code}?from=01-2018&to=${to}`, { "X-Api-Key": KEY });
    check(doc && !doc.error, `CDE ${usps}/${code}: ${JSON.stringify(doc?.error ?? "no answer")}`);
    const rate = doc.offenses.rates;
    const act = doc.offenses.actuals ?? {};
    const cov = doc.tooltips["Percent of Population Coverage"];
    const pop = doc.populations.population;
    const part = doc.populations.participated_population;
    const expect = new Map();
    for (const key of new Set([...Object.keys(rate[`${name} Offenses`] ?? {}), ...Object.keys(act[`${name} Offenses`] ?? {})])) {
      expect.set(month(key), {
        offenses: act[`${name} Offenses`]?.[key], clearances: act[`${name} Clearances`]?.[key],
        offense_rate: rate[`${name} Offenses`]?.[key], clearance_rate: rate[`${name} Clearances`]?.[key],
        coverage_pct: cov[name]?.[key], population: pop[name]?.[key], covered_population: part[name]?.[key],
      });
    }
    for (const key of Object.keys(rate["United States Offenses"])) {
      national.set(`${series}|${month(key)}`, {
        offenses: null, clearances: null,
        offense_rate: rate["United States Offenses"][key], clearance_rate: rate["United States Clearances"]?.[key],
        coverage_pct: cov["United States"]?.[key], population: pop["United States"]?.[key], covered_population: part["United States"]?.[key],
      });
    }
    check(expect.size > 90, `CDE ${usps}/${code}: only ${expect.size} months`);
    const loc = `us-${usps.toLowerCase()}`;
    const pub = await published(series, loc);
    check(pub, `${series}/${loc} is not published`);
    check(pub.length === expect.size, `${series}/${loc}: published ${pub.length} months, the CDE has ${expect.size}`);
    for (const p of pub) {
      const e = expect.get(p.date);
      check(e, `${series}/${loc}: published ${p.date}, which the CDE doesn't list`);
      for (const [col, want] of Object.entries(e)) check(same(p[col], want), `${series}/${loc} ${p.date} ${col}: published ${p[col]}, CDE ${want}`);
      rows++;
    }
    if (usps === "MN" && code === "V") {
      const jan = pub.find((p) => p.date === "2024-01");
      for (const [col, want] of Object.entries(EXPECTED.mnViolentJan2024)) {
        check(jan?.[col] === want, `Minnesota violent crime 2024-01 ${col}: published ${jan?.[col]}, expected ${want}`);
      }
    }
  }
}
for (const series of Object.values(SERIES)) {
  const pub = await published(series, "usa");
  check(pub, `${series}/usa is not published`);
  for (const p of pub) {
    const e = national.get(`${series}|${p.date}`);
    check(e, `${series}/usa: published ${p.date}, which the CDE doesn't list`);
    for (const [col, want] of Object.entries(e)) check(same(p[col], want), `${series}/usa ${p.date} ${col}: published ${p[col]}, CDE ${want}`);
    rows++;
  }
}
console.log(`JUSTICE VERIFIED (crime-monthly: ${rows} rows for 51 states and the US re-derived from the CDE API${API_BASE ? ` at ${API_BASE}` : ""}; Minnesota violent crime 2024-01 = ${EXPECTED.mnViolentJan2024.offenses})`);
