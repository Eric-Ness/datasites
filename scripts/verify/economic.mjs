// G4/G5: every keyless economicdata dataset published to the local bucket matches facts re-derived here from
// the original sources, by code written independently of the Python pipelines and, where the source allows,
// from different files than the pipelines read:
//   laus         the Delaware state file la.data.14.Delaware (the pipeline reads the all-states, metro, micro
//                and county files) and la.series for the area counts
//   qcew         QCEW area slices for one county and the nation (the pipeline reads industry slices), plus the
//                newest industry slice for the county count and the highest weekly wage
//   world-macro  per-country World Bank API queries (the pipeline reads all-country pages) and the economy list
// For each: values for a known place and period, a location count, and an extreme.
// The keyed datasets (bea-county-income, census-acs-income) are only reported: re-deriving them needs their keys.
//   node scripts/verify/economic.mjs                     prints ECONOMIC VERIFIED ... when everything matches
//   node scripts/verify/economic.mjs --negative-control  corrupts one expected value, so it must FAIL
// Large source files are cached under the OS temp folder and revalidated with conditional GETs.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const SITE = "economicdata";
const UA = "datasites-verify (+mailto:hello@climatedata.dev)"; // BLS refuses requests without a contact
const CACHE = join(tmpdir(), "datasites-verify-economic");
const LA = "https://download.bls.gov/pub/time.series/la/";
const CEW = "https://data.bls.gov/cew/data/api/";
const WB = "https://api.worldbank.org/v2/";
const facts = [];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const close = (a, b, tol = 1e-9) => typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));

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

/** Fails clearly if a source file changed after the published version was built (then re-run the pipeline). */
function sameAsPublished(ds, name, body) {
  const entry = (ds.doc.source.raw ?? []).find((r) => r.name === name);
  check(entry, `${ds.doc.dataset}: version ${ds.doc.version} lists no raw input named ${name}`);
  check(
    sha256(body) === entry.sha256,
    `${ds.doc.dataset}: the source behind '${name}' changed after version ${ds.doc.version} was built; ` +
      "re-run `uv run datasites run economicdata --target local` (from pipelines/) and verify again",
  );
}

function published(ds, path) {
  const doc = ds.json(path);
  check(Array.isArray(doc.data), `${ds.doc.dataset}: ${path} has no data array`);
  return new Map(doc.data.map((r) => [r.date, r]));
}

function expectRow(label, got, want) {
  check(got, `${label}: not published`);
  for (const [key, value] of Object.entries(want)) {
    const ok = typeof value === "number" ? close(got[key], value) : got[key] === value;
    check(ok, `${label}: ${key} is ${JSON.stringify(got[key])}, the source gives ${JSON.stringify(value)}`);
  }
}

/** Simple CSV for QCEW and the published downloads (no embedded commas in the fields we read). */
function csvRows(text) {
  const lines = text.trim().split(/\r?\n/);
  const header = lines[0].split(",").map((h) => h.replace(/"/g, ""));
  return lines.slice(1).map((line) => Object.fromEntries(line.split(",").map((v, i) => [header[i], v.replace(/"/g, "")])));
}

// --- LAUS ------------------------------------------------------------------------------------------

const MEASURE = { "03": "unemployment_rate", "04": "unemployed", "05": "employed", "06": "labor_force" };

/** la.data.* -> Map "S|area|YYYY-MM" -> { measure: value|null, footnotes: Set } (annual averages skipped). */
function parseLausData(body) {
  const out = new Map();
  const lines = body.toString("latin1").split("\n");
  for (const raw of lines.slice(1)) {
    const cols = raw.replace(/\r$/, "").split("\t");
    if (cols.length < 4) continue;
    const id = cols[0].trim();
    const measure = MEASURE[id.slice(18, 20)];
    const month = cols[2].trim();
    if (!measure || month === "M13") continue;
    const key = `${id[2]}|${id.slice(3, 18)}|${cols[1].trim()}-${month.slice(1)}`;
    if (!out.has(key)) out.set(key, { footnotes: new Set() });
    const entry = out.get(key);
    const value = cols[3].trim();
    entry[measure] = value === "-" ? null : Number(value);
    for (const code of (cols[4] ?? "").trim()) if (/[A-Z]/.test(code)) entry.footnotes.add(code);
  }
  return out;
}

async function verifyLaus() {
  const ds = localDataset(SITE, "laus");
  const series = await original(LA + "la.series");
  sameAsPublished(ds, "la.series", series);
  const delaware = parseLausData(await original(LA + "la.data.14.Delaware"));

  // 1. Values: every month BLS has for four Delaware areas, compared field by field (latest month included).
  const areas = [
    ["U", "CN1000100000000", "nsa/us-county-10001.json", "Kent County"],
    ["U", "MT1020100000000", "nsa/us-cbsa-20100.json", "Dover metro"],
    ["U", "MC1042580000000", "nsa/us-cbsa-42580.json", "Seaford micro"],
    ["S", "ST1000000000000", "sa/us-de.json", "Delaware (seasonally adjusted)"],
  ];
  let compared = 0;
  let kentLatest = null;
  for (const [seasonal, area, path, label] of areas) {
    const rows = published(ds, path);
    const months = [...delaware.keys()].filter((k) => k.startsWith(`${seasonal}|${area}|`));
    check(months.length > 300, `${label}: only ${months.length} months in la.data.14.Delaware`);
    check(rows.size === months.length, `${label}: ${rows.size} months published, the Delaware file has ${months.length}`);
    for (const key of months) {
      const src = delaware.get(key);
      const month = key.split("|")[2];
      const want = {
        labor_force: src.labor_force,
        employed: src.employed,
        unemployed: src.unemployed,
        unemployment_rate: src.unemployment_rate,
        preliminary: src.footnotes.has("P"),
        footnotes: src.footnotes.size ? [...src.footnotes].sort().join(",") : null,
      };
      if (NEGATIVE && area === "CN1000100000000" && month === "2025-09") want.labor_force += 1; // the one corrupted value
      expectRow(`laus ${label} ${month}`, rows.get(month), want);
      compared++;
    }
    if (area === "CN1000100000000") kentLatest = months.map((k) => k.split("|")[2]).sort().at(-1);
  }
  const gap = published(ds, "nsa/us-county-10001.json").get("2025-10");
  check(gap && gap.unemployment_rate === null && gap.footnotes === "X", "laus: Kent County October 2025 should be null with footnote X");
  facts.push(`laus: ${compared} Delaware area-months match la.data.14.Delaware (Kent County through ${kentLatest}; 2025-10 null, X)`);

  // 2. Counts: areas that have a not-seasonally-adjusted unemployment-rate series in la.series.
  const kinds = { F: new Set(), B: new Set(), D: new Set(), A: new Set() };
  for (const raw of series.toString("latin1").split("\n").slice(1)) {
    const cols = raw.replace(/\r$/, "").split("\t");
    if (cols.length < 5) continue;
    if (cols[3] === "03" && cols[4] === "U" && kinds[cols[1]]) kinds[cols[1]].add(cols[2]);
  }
  const locations = Object.keys(ds.locations());
  const count = (prefix) => locations.filter((l) => l.startsWith(prefix)).length;
  const states = locations.filter((l) => /^us-[a-z]{2}$/.test(l)).length;
  check(count("us-county-") === kinds.F.size, `laus: ${count("us-county-")} counties published, la.series has ${kinds.F.size}`);
  check(count("us-cbsa-") === kinds.B.size + kinds.D.size, `laus: ${count("us-cbsa-")} CBSAs published, la.series has ${kinds.B.size} metro + ${kinds.D.size} micro`);
  check(states === kinds.A.size, `laus: ${states} states published, la.series has ${kinds.A.size}`);
  facts.push(`laus: ${kinds.F.size} counties, ${kinds.B.size + kinds.D.size} CBSAs, ${kinds.A.size} states as in la.series`);

  // 3. Extreme: Kent County's highest monthly unemployment rate since 1990.
  let peak = { rate: -1, month: null };
  for (const [key, src] of delaware) {
    if (key.startsWith("U|CN1000100000000|") && src.unemployment_rate !== null && src.unemployment_rate > peak.rate) {
      peak = { rate: src.unemployment_rate, month: key.split("|")[2] };
    }
  }
  const kent = [...published(ds, "nsa/us-county-10001.json").values()].filter((r) => r.unemployment_rate !== null);
  const top = kent.reduce((a, b) => (b.unemployment_rate > a.unemployment_rate ? b : a));
  check(top.unemployment_rate === peak.rate, `laus: Kent County peak is ${top.unemployment_rate} (${top.date}), the source gives ${peak.rate} (${peak.month})`);
  facts.push(`laus: Kent County peak ${peak.rate}% in ${peak.month}`);
}

// --- QCEW ------------------------------------------------------------------------------------------

function qcewTotal(rows, area) {
  const row = rows.find((r) => r.area_fips === area && r.own_code === "0" && r.industry_code === "10" && r.size_code === "0");
  check(row, `qcew: no total-covered row for ${area}`);
  return row;
}

async function verifyQcew() {
  const ds = localDataset(SITE, "qcew");
  const end = ds.doc.coverage.end; // first month of the newest quarter
  const year = end.slice(0, 4);
  const quarter = (Number(end.slice(5, 7)) - 1) / 3 + 1;
  const industry = await original(`${CEW}${year}/${quarter}/industry/10.csv`);
  sameAsPublished(ds, `${year}-q${quarter}`, industry);

  // 1. Values from the area slices, a different endpoint than the pipeline's industry slices.
  for (const [area, location, label] of [["10001", "us-county-10001", "Kent County DE"], ["US000", "usa", "United States"]]) {
    const src = qcewTotal(csvRows((await original(`${CEW}${year}/${quarter}/area/${area}.csv`)).toString("latin1")), area);
    const want = {
      establishments: Number(src.qtrly_estabs),
      month1_employment: Number(src.month1_emplvl),
      month2_employment: Number(src.month2_emplvl),
      month3_employment: Number(src.month3_emplvl),
      total_wages: Number(src.total_qtrly_wages),
      avg_weekly_wage: Number(src.avg_wkly_wage),
      suppressed: false,
    };
    expectRow(`qcew ${label} ${year} Q${quarter}`, published(ds, `total-covered/${location}.json`).get(end), want);
    facts.push(`qcew: ${label} ${year} Q${quarter} ${want.month3_employment} jobs, $${want.avg_weekly_wage}/week`);
  }

  // 2. Count and 3. extreme, from the newest industry slice parsed here.
  const counties = csvRows(industry.toString("latin1")).filter(
    (r) => r.own_code === "0" && r.agglvl_code === "70" && r.size_code === "0" && !r.area_fips.endsWith("999"),
  );
  const bulk = csvRows(ds.text("download/qcew.csv")).filter((r) => r.date === end && r.location.startsWith("us-county-"));
  check(bulk.length === counties.length, `qcew: ${bulk.length} counties published for ${end}, the slice has ${counties.length}`);
  const top = counties.reduce((a, b) => (Number(b.avg_wkly_wage) > Number(a.avg_wkly_wage) ? b : a));
  const topPublished = bulk.reduce((a, b) => (Number(b.avg_weekly_wage) > Number(a.avg_weekly_wage) ? b : a));
  check(
    topPublished.location === `us-county-${top.area_fips}` && Number(topPublished.avg_weekly_wage) === Number(top.avg_wkly_wage),
    `qcew: highest weekly wage published is ${topPublished.location} $${topPublished.avg_weekly_wage}, the slice gives ${top.area_fips} $${top.avg_wkly_wage}`,
  );
  facts.push(`qcew: ${counties.length} counties in ${year} Q${quarter}; top weekly wage ${top.area_fips} $${top.avg_wkly_wage}`);
}

// --- World Bank ------------------------------------------------------------------------------------

async function wbValue(country, indicator, query) {
  const doc = JSON.parse((await original(`${WB}country/${country}/indicator/${indicator}?format=json&${query}`, { cache: false })).toString("utf8"));
  check(Array.isArray(doc) && Array.isArray(doc[1]) && doc[1].length === 1, `World Bank ${country} ${indicator} ${query}: ${JSON.stringify(doc).slice(0, 200)}`);
  return doc[1][0];
}

async function verifyWorldMacro() {
  const ds = localDataset(SITE, "world-macro");

  // 1. Values from single-country queries.
  const gdp = await wbValue("USA", "NY.GDP.MKTP.CD", "date=2024");
  expectRow("world-macro usa 2024", published(ds, "annual/usa.json").get("2024"), { gdp_usd: gdp.value });
  const kosovo = await wbValue("XKX", "SP.POP.TOTL", "date=2024");
  expectRow("world-macro wb-xkx 2024", published(ds, "annual/wb-xkx.json").get("2024"), { population: kosovo.value });
  facts.push(`world-macro: US GDP 2024 $${(gdp.value / 1e12).toFixed(3)}T; Kosovo population 2024 ${kosovo.value}`);

  // 2. Count: every economy in the World Bank's list (aggregates excluded) plus the world.
  const economies = JSON.parse((await original(`${WB}country?format=json&per_page=500`, { cache: false })).toString("utf8"))[1];
  const real = economies.filter((c) => c.region.value.trim() !== "Aggregates");
  const locations = Object.keys(ds.locations());
  check(locations.length === real.length + 1, `world-macro: ${locations.length} locations published, the World Bank lists ${real.length} economies + world`);
  for (const c of real) {
    const id = { XKX: "wb-xkx", CHI: "wb-chi" }[c.id] ?? c.id.toLowerCase();
    check(locations.includes(id), `world-macro: economy ${c.id} (${c.name}) is not published as ${id}`);
  }
  facts.push(`world-macro: ${real.length} economies + world`);

  // 3. Extreme: the world's latest population is the largest population published, and matches the API.
  const world = await wbValue("WLD", "SP.POP.TOTL", "mrv=1");
  const rows = csvRows(ds.text("download/world-macro.csv"));
  const top = rows.reduce((a, b) => (Number(b.population || 0) > Number(a.population || 0) ? b : a));
  check(top.location === "world" && top.date === world.date && Number(top.population) === world.value,
    `world-macro: largest population published is ${top.location} ${top.date} ${top.population}, the API gives world ${world.date} ${world.value}`);
  facts.push(`world-macro: world population ${world.date} ${world.value}`);
}

// --- keyed datasets --------------------------------------------------------------------------------

function keyedStatus(dataset) {
  const manifest = join(ROOT, ".data", "r2", "sites", SITE, "datasets", dataset, "manifest.json");
  return existsSync(manifest) ? "published (not re-derived: needs a key)" : "needs credentials, not published";
}

await verifyLaus();
await verifyQcew();
await verifyWorldMacro();
const keyed = ["bea-county-income", "census-acs-income"].map((d) => `${d}: ${keyedStatus(d)}`);
console.log(`ECONOMIC VERIFIED (${facts.length} facts)\n  ${facts.join("\n  ")}\n  ${keyed.join("\n  ")}`);
