// Independent check of what `datasites run healthdata --target local` published.
// It downloads the sources again and re-derives every published value with its own code (the shared CSV and
// zip readers in lib.mjs; no pipeline code), and checks facts that hold between the published numbers.
//   node scripts/verify/health.mjs                      prints HEALTH VERIFIED ... when everything matches
//   node scripts/verify/health.mjs --negative-control   corrupts one expected value, so it must FAIL
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, localDataset, parseCsv, unzip } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const UA = "datasites-verify/1 (+https://healthdata.dev)";
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const SERIES = {
  "Number of Drug Overdose Deaths": "all-drugs",
  "Opioids (T40.0-T40.4,T40.6)": "opioids",
  "Synthetic opioids, excl. methadone (T40.4)": "synthetic-opioids",
  "Heroin (T40.1)": "heroin",
  "Natural & semi-synthetic opioids (T40.2)": "natural-opioids",
  "Methadone (T40.3)": "methadone",
  "Natural & semi-synthetic opioids, incl. methadone (T40.2, T40.3)": "natural-opioids-methadone",
  "Natural, semi-synthetic, & synthetic opioids, incl. methadone (T40.2-T40.4)": "all-opioids-excl-heroin",
  "Cocaine (T40.5)": "cocaine",
  "Psychostimulants with abuse potential (T43.6)": "psychostimulants",
  "Number of Deaths": "all-causes",
};
// Facts read from the source files on 2026-11-06 (they change only when CDC or Census revise them).
const EXPECTED = {
  usOverdose2024Dec: 80860, // VSRR: US, 12 months ending December 2024, reported
  laUninsured2024: 8.3, // SAHIE 2024: Los Angeles County, under 65, % uninsured
  usMmr2025: 92.4, // SchoolVaxView: United States, MMR, 2025-26
  lowestMmr2025: "us-id", // SchoolVaxView: lowest state MMR coverage in 2025-26 (Idaho, 75.2%)
};
if (NEGATIVE) EXPECTED.usOverdose2024Dec += 1;
if (process.argv.includes("--negative-control-kindergarten")) EXPECTED.usMmr2025 += 0.1;

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const num = (s) => (s === undefined || s === null || s.trim() === "" ? null : Number(s.replace(/,/g, "")));

async function download(url) {
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, { headers: { "user-agent": UA }, signal: AbortSignal.timeout(180_000) });
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

/** The bytes a version was built from: downloaded again, and checked against the hash the version recorded. */
async function source(ds, name, url) {
  const raw = ds.doc.source.raw.find((r) => r.name === name);
  check(raw, `${ds.doc.dataset}: no raw file ${name}`);
  const body = await download(url);
  check(sha256(body) === raw.sha256, `${url} changed since ${ds.doc.version} was built (re-run the pipeline)`);
  return body;
}

function publishedSeries(ds, prefix) {
  const out = new Map(); // "series|location" -> Map(date -> row)
  for (const path of ds.paths().filter((p) => /^[a-z0-9-]+\/[a-z0-9-]+\.json$/.test(p))) {
    const [series, file] = path.split("/");
    out.set(`${series}|${file.slice(0, -5)}`, new Map(ds.json(path).data.map((r) => [r.date, r])));
  }
  check(out.size > 0, `${prefix}: no published series files`);
  return out;
}

// --- overdose-deaths ---------------------------------------------------------------------------------------

async function verifyStates() {
  const ds = localDataset("healthdata", "overdose-deaths");
  const body = await source(ds, "states", "https://data.cdc.gov/api/views/xkb8-kh2a/rows.csv?accessType=DOWNLOAD");
  const rows = parseCsv(body.toString("utf8"));
  const pub = publishedSeries(ds, "overdose-deaths");
  const expected = new Map(); // "series|location|date" -> deaths
  for (const r of rows) {
    const series = SERIES[r.Indicator];
    if (!series) continue;
    const date = `${r.Year}-${String(MONTHS.indexOf(r.Month) + 1).padStart(2, "0")}`;
    const loc = r.State === "US" ? "usa" : ["NY", "YC"].includes(r.State) ? "us-ny" : `us-${r.State.toLowerCase()}`;
    const key = `${series}|${loc}|${date}`;
    const v = num(r["Data Value"]);
    if (loc === "us-ny") {
      const before = expected.get(key);
      expected.set(key, before === undefined ? { n: 1, v } : { n: before.n + 1, v: before.v === null || v === null ? null : before.v + v });
    } else expected.set(key, { n: 2, v });
  }
  let checked = 0;
  for (const [key, { n, v }] of expected) {
    check(n === 2, `${key}: New York or New York City is missing`);
    const [series, loc, date] = key.split("|");
    const got = pub.get(`${series}|${loc}`)?.get(date);
    check(got, `${key}: not published`);
    check(got.deaths === v, `${key}: published ${got.deaths}, VSRR ${v}`);
    checked++;
  }
  // The US total equals the states, DC, New York (with NYC) and nothing else (Puerto Rico is outside it).
  let sums = 0;
  for (const [key, row] of pub.get("all-drugs|usa")) {
    let total = 0;
    let complete = true;
    for (const [k, series] of pub) {
      if (!k.startsWith("all-drugs|us-") || k === "all-drugs|us-pr") continue;
      const v = series.get(key)?.deaths;
      if (v === null || v === undefined) complete = false;
      else total += v;
    }
    if (complete && row.deaths !== null) {
      check(total === row.deaths, `all-drugs ${key}: states add up to ${total}, US is ${row.deaths}`);
      sums++;
    }
  }
  const us = pub.get("all-drugs|usa").get("2024-12").deaths;
  check(us === EXPECTED.usOverdose2024Dec, `US overdose deaths, 12 months to December 2024: published ${us}, expected ${EXPECTED.usOverdose2024Dec}`);
  return `overdose-deaths ${ds.doc.version}: ${checked} series-place-months re-derived; the states add up to the US total in ${sums} months; US 2024 = ${us}`;
}

async function verifyCounties() {
  const ds = localDataset("healthdata", "overdose-deaths-county");
  const body = await source(ds, "counties", "https://data.cdc.gov/api/views/gb4e-yj24/rows.csv?accessType=DOWNLOAD");
  const rows = parseCsv(body.toString("utf8"));
  const pub = publishedSeries(ds, "overdose-deaths-county");
  let checked = 0;
  let withheld = 0;
  for (const r of rows) {
    const loc = `us-county-${r.FIPS.padStart(5, "0")}`;
    const date = `${r.Year}-${r.Month.padStart(2, "0")}`;
    const got = pub.get(`all-drugs|${loc}`)?.get(date);
    check(got, `${loc} ${date}: not published`);
    const v = num(r["Provisional Drug Overdose Deaths"]);
    check(got.deaths === v, `${loc} ${date}: published ${got.deaths}, VSRR ${v}`);
    if ((r.Footnote ?? "").includes("between 1-9")) {
      check(got.suppressed === true && got.deaths === null, `${loc} ${date}: a withheld count is not marked suppressed`);
      withheld++;
    }
    checked++;
  }
  return `overdose-deaths-county ${ds.doc.version}: ${checked} county-months re-derived (${withheld} withheld counts of 1-9 kept null)`;
}

// --- uninsured ---------------------------------------------------------------------------------------------

async function verifyUninsured() {
  const ds = localDataset("healthdata", "uninsured");
  const pub = publishedSeries(ds, "uninsured");
  const ages = { 0: "under-65", 1: "adults-18-64", 4: "children-under-19" };
  const geo = JSON.parse(readFileSync(join(ROOT, "packages", "geo", "src", "locations.json"), "utf8"));
  const stateIds = new Map(Object.values(geo.locations ?? geo).filter((l) => l.level === "us-state").map((l) => [l.fips, l.id]));
  check(stateIds.size >= 51, `geo registry: ${stateIds.size} states`);
  const years = ds.doc.source.raw.map((r) => Number(r.name.slice(6))).sort((a, b) => a - b);
  let checked = 0;
  let consistent = 0;
  for (const year of years) {
    const zip = await source(ds, `sahie-${year}`, `https://www2.census.gov/programs-surveys/sahie/datasets/time-series/estimates-acs/sahie-${year}-csv.zip`);
    const text = unzip(zip).get(`sahie_${year}.csv`).toString("latin1");
    const start = text.indexOf("\nyear,version,");
    check(start > 0, `sahie_${year}.csv: header not found`);
    // Only all-race, both-sex, all-income rows for the three age groups are published. Pick those lines by their
    // first nine fields (plain codes, never quoted) before the full CSV parse, which would be slow on 100+ MB.
    const lines = text.slice(start + 1).split(/\r?\n/);
    const wanted = lines.slice(1).filter((line) => {
      const f = line.split(",", 9).map((x) => x.trim());
      return ["40", "50"].includes(f[4]) && f[5] in ages && f[6] === "0" && f[7] === "0" && f[8] === "0";
    });
    const keep = parseCsv([lines[0], ...wanted].join("\n"));
    const versions = new Set(keep.map((r) => r.version.trim()));
    const rows = versions.has("Updated") ? keep.filter((r) => r.version.trim() === "Updated") : keep;
    for (const r of rows) {
      const loc = r.geocat.trim() === "40" ? stateIds.get(r.statefips.trim()) : `us-county-${r.statefips.trim()}${r.countyfips.trim()}`;
      if (loc === "us-county-15005") continue; // Kalawao: published as null by design (Census: N/A)
      const series = ages[r.agecat.trim()];
      const got = pub.get(`${series}|${loc}`)?.get(String(year));
      check(got, `${series} ${loc} ${year}: not published`);
      check(got.uninsured_pct === num(r.PCTUI) && got.uninsured === num(r.NUI) && got.population === num(r.NIPR) && got.uninsured_pct_moe === num(r.pctui_moe),
        `${series} ${loc} ${year}: published ${JSON.stringify(got)}, SAHIE ${r.PCTUI} ${r.NUI} ${r.NIPR}`);
      // Independent arithmetic: the rate is about the count over the population. SAHIE computes rates from
      // unrounded estimates, so in 2008-2012 about 500 rows a year (mostly tiny counties) miss by more than rounding.
      if (got.population > 0 && Math.abs((100 * got.uninsured) / got.population - got.uninsured_pct) <= 0.1 + 1e-9) consistent++;
      checked++;
    }
  }
  check(consistent / checked > 0.995, `only ${consistent} of ${checked} rows have uninsured_pct within 0.1 of uninsured / population`);
  const la = pub.get("under-65|us-county-06037").get("2024").uninsured_pct;
  check(la === EXPECTED.laUninsured2024, `Los Angeles 2024 uninsured under 65: published ${la}, expected ${EXPECTED.laUninsured2024}`);
  return `uninsured ${ds.doc.version}: ${checked} place-age-years re-derived from ${years.length} SAHIE files (${years[0]}-${years.at(-1)}); ${consistent} have a rate within 0.1 point of count / population; Los Angeles 2024 = ${la}%`;
}

// --- kindergarten-vaccination ------------------------------------------------------------------------------

const KV_SERIES = {
  "MMR|": "mmr", "MMR (PAC)|MMR (PAC)": "mmr-pac", "DTP, DTaP, or DT|": "dtap", "Polio|": "polio", "Hepatitis B|": "hepb",
  "Varicella|1 Dose (or disease history)": "varicella-1-dose", "Varicella|2 Doses (or disease history)": "varicella-2-doses",
  "Varicella|UTD (or disease history)": "varicella-utd", "Exemption|Any Exemption": "exemption-any",
  "Exemption|Medical Exemption": "exemption-medical", "Exemption|Non-Medical Exemption": "exemption-nonmedical",
};
const KV_STATUS = { NA: "not-available", NREQ: "not-required", NR: "not-reported" };

async function verifyKindergarten() {
  const ds = localDataset("healthdata", "kindergarten-vaccination");
  const body = await source(ds, "kindergarten", "https://data.cdc.gov/api/views/ijqb-a7ye/rows.csv?accessType=DOWNLOAD");
  const rows = parseCsv(body.toString("utf8"));
  const pub = publishedSeries(ds, "kindergarten-vaccination");
  const geo = JSON.parse(readFileSync(join(ROOT, "packages", "geo", "src", "locations.json"), "utf8"));
  const byName = new Map(Object.values(geo.locations ?? geo).filter((l) => l.level === "us-state").map((l) => [l.name, l.id]));
  byName.set("United States", "usa");
  const skipped = new Set(["NY-City of New York", "NY-Rest of state", "TX-City of Houston", "U.S. Median"]);
  let checked = 0;
  let dropped = 0;
  const marked = new Set();
  for (const r of rows) {
    const g = r.Geography.trim();
    if (skipped.has(g)) { dropped++; continue; }
    const loc = byName.get(g);
    check(loc, `kindergarten-vaccination: unknown place ${g}`);
    const series = KV_SERIES[`${r["Vaccine/Exemption"].trim()}|${r.Dose.trim()}`];
    check(series, `kindergarten-vaccination: unknown vaccine ${r["Vaccine/Exemption"]} ${r.Dose}`);
    const year = r["School Year"].trim().slice(0, 4);
    const got = pub.get(`${series}|${loc}`)?.get(year);
    check(got, `${series} ${loc} ${year}: not published`);
    const est = r["Estimate (%)"].trim();
    const want = /^[\d.]+$/.test(est) ? { estimate: Number(est), status: "reported" } : { estimate: null, status: KV_STATUS[est.toUpperCase()] };
    check(got.estimate === want.estimate && got.status === want.status, `${series} ${loc} ${year}: published ${got.estimate} ${got.status}, CDC ${est}`);
    check(got.kindergartners === num(r["Population Size"]) && got.percent_surveyed === num(r["Percent Surveyed"]) && got.exemptions === num(r["Number of Exemptions"]),
      `${series} ${loc} ${year}: counts differ from CDC (${r["Population Size"]}, ${r["Percent Surveyed"]}, ${r["Number of Exemptions"]})`);
    const allVaccines = /≥|>=/.test(r.Footnotes ?? "");
    check(got.all_vaccines === allVaccines, `${series} ${loc} ${year}: all_vaccines ${got.all_vaccines}, CDC's footnote "${r.Footnotes}"`);
    if (allVaccines && series === "mmr") marked.add(`${loc} ${year}`);
    checked++;
  }
  // CDC's own counts don't add up exactly: "any exemption" equals medical + nonmedical in most place-years, is
  // smaller where a child holds both, and larger where a state reports a total without the full split. Reported,
  // not asserted.
  let equal = 0;
  let pairs = 0;
  for (const [key, any] of pub) {
    if (!key.startsWith("exemption-any|")) continue;
    const loc = key.split("|")[1];
    for (const [year, row] of any) {
      const med = pub.get(`exemption-medical|${loc}`)?.get(year)?.exemptions;
      const non = pub.get(`exemption-nonmedical|${loc}`)?.get(year)?.exemptions;
      if ([row.exemptions, med, non].some((v) => v === null || v === undefined)) continue;
      pairs++;
      if (row.exemptions === med + non) equal++;
    }
  }
  const newest = String(Math.max(...[...pub.get("mmr|usa").keys()].map(Number)));
  const states = [...pub].filter(([k]) => k.startsWith("mmr|us-")).map(([k, s]) => [k.slice(4), s.get(newest)?.estimate]).filter(([, v]) => v !== null && v !== undefined);
  const lowest = states.reduce((a, b) => (b[1] < a[1] ? b : a));
  check(states.length >= 45, `only ${states.length} states have a ${newest} MMR figure`);
  check(lowest[0] === EXPECTED.lowestMmr2025, `lowest state MMR ${newest}: ${lowest[0]}, expected ${EXPECTED.lowestMmr2025}`);
  const us = pub.get("mmr|usa").get("2025").estimate;
  check(us === EXPECTED.usMmr2025, `US kindergarten MMR 2025-26: published ${us}, expected ${EXPECTED.usMmr2025}`);
  // CDC MMWR 66(40), 2017: these seven states counted a child up to date only with every required vaccine in 2016-17.
  const seven = ["us-al", "us-fl", "us-ga", "us-ia", "us-ms", "us-nh", "us-nj"];
  for (const loc of seven) check(marked.has(`${loc} 2016`), `${loc} 2016-17 MMR is not marked all-vaccines, though CDC's MMWR names it`);
  check(pub.get("mmr|us-mt").get("2025") === undefined, "Montana has a 2025-26 MMR row; CDC had none on 2026-11-12");
  return `kindergarten-vaccination ${ds.doc.version}: ${checked} rows re-derived (${dropped} city and median rows left out; ${marked.size} MMR state-years on the all-vaccines rule, the seven MMWR names for 2016-17 among them); any-exemption count equals medical + nonmedical in ${equal} of ${pairs} place-years; ${states.length} states with ${newest} MMR, lowest ${lowest[0]} ${lowest[1]}%; US 2025-26 = ${us}%`;
}

const results = [await verifyStates(), await verifyCounties(), await verifyUninsured(), await verifyKindergarten()];
console.log(`HEALTH VERIFIED (${results.join("; ")})`);
