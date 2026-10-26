// Independent check of what `datasites run electiondata --target local` published.
// For every dataset it downloads the original source again, re-derives facts with its own code (its own
// CSV parser, no pipeline code) and compares them with the published files and with known certified results.
//   node scripts/verify/election.mjs                      prints ELECTION VERIFIED ... when everything matches
//   node scripts/verify/election.mjs --negative-control   corrupts one expected value; must fail with FAIL:
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const UA = "datasites-verify/1 (+mailto:hello@climatedata.dev)";
const DATAVERSE = "https://dataverse.harvard.edu";

// Known results, from the states' certified canvasses (MEDSL's file must agree).
const EXPECTED = {
  senate: {
    az2024: { "RUBEN GALLEGO": 1676335, "KARI LAKE": 1595761, "EDUARDO QUINTANA": 75868, total: 3347964 },
    md2024: { "ANGELA ALSOBROOKS": 1650912, "LARRY HOGAN": 1294344 },
    wa2024: { "MARIA CANTWELL": 2252577, "DR RAUL GARCIA": 1549187 },
    largestRace: { year: "2024", state_po: "CA", special: false, totalvotes: 15348846 },
    firstYear: "1976",
    lastYear: "2024",
  },
  county: {
    // 2020 certified canvasses: Los Angeles County Registrar-Recorder, Maricopa County Elections Department.
    la2020: { fips: "06037", "JOSEPH R BIDEN JR": 3028885, "DONALD J TRUMP": 1145530 },
    maricopa2020: { fips: "04013", "JOSEPH R BIDEN JR": 1040774, "DONALD J TRUMP": 995665 },
    largestCounty: { year: "2020", fips: "06037", totalvotes: 4264365 },
    years: ["2000", "2004", "2008", "2012", "2016", "2020", "2024"],
  },
  house: {
    // Alaska's 2024 at-large seat, first-choice votes in the Division of Elections' certified results.
    ak2024: { "NICK BEGICH": 159550, "MARY SATTLER PELTOLA": 152828 },
    firstYear: "1976",
    lastYear: "2024",
  },
};
if (NEGATIVE) EXPECTED.senate.az2024["RUBEN GALLEGO"] += 1; // a wrong expectation must be caught

const md5 = (buf) => createHash("md5").update(buf).digest("hex");
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

async function download(url) {
  let res;
  try {
    res = await fetch(url, { headers: { "User-Agent": UA }, redirect: "follow", signal: AbortSignal.timeout(120_000) });
  } catch (err) {
    check(false, `could not fetch ${url}: ${err.message}`);
  }
  check(res.ok, `${url} answered HTTP ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

/** RFC 4180 CSV (quoted fields, doubled quotes, commas and newlines inside quotes) -> array of objects. */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  const [header, ...body] = rows;
  return body.map((cells) => Object.fromEntries(header.map((name, i) => [name, cells[i]])));
}

const count = (text) => (text === "" || text === undefined ? null : Math.round(Number(text)));
const isTrue = (text) => text.toLowerCase() === "true";
const sum = (values) => values.reduce((a, b) => a + b, 0);

function groupBy(items, keyOf) {
  const groups = new Map();
  for (const item of items) {
    const key = keyOf(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

// --- senate-results ----------------------------------------------------------------------------------

async function verifySenate() {
  const ds = localDataset("electiondata", "senate-results");
  const doi = "doi:10.7910/DVN/PEJ5QU";
  const meta = JSON.parse((await download(`${DATAVERSE}/api/datasets/:persistentId/?persistentId=${doi}`)).toString("utf8"));
  const latest = meta.data.latestVersion;
  check(latest.versionState === "RELEASED", `${doi}: latest version is ${latest.versionState}`);
  check(latest.license?.rightsIdentifier === "CC0-1.0", `${doi}: license is ${JSON.stringify(latest.license)}, expected CC0-1.0`);
  check(
    ds.doc.source.attribution.includes(`Harvard Dataverse, V${latest.versionNumber},`) && latest.versionMinorNumber === 0,
    `published attribution cites another version than Dataverse's V${latest.versionNumber}.${latest.versionMinorNumber}: ${ds.doc.source.attribution}`,
  );
  const file = latest.files.find((f) => f.dataFile.originalFileName === "1976-2024-senate-state.csv");
  check(file, `${doi}: no 1976-2024-senate-state.csv in V${latest.versionNumber}`);
  const body = await download(`${DATAVERSE}/api/access/datafile/${file.dataFile.id}?format=original`);
  check(md5(body) === file.dataFile.md5, `downloaded file's md5 ${md5(body)} != Dataverse's ${file.dataFile.md5}`);
  const raw = ds.doc.source.raw.find((r) => r.name === "senate");
  check(raw && raw.sha256 === sha256(body), "the published version was built from different bytes than the source serves now (re-run the pipeline)");

  const source = parseCsv(body.toString("utf8")).map((r) => ({
    ...r,
    stage: r.stage.toUpperCase() === "GEN RUNOFF" ? "RUNOFF" : r.stage.toUpperCase(),
    special: isTrue(r.special),
    candidate: r.candidate.trim() || null,
    votes: count(r.candidatevotes),
    total: count(r.totalvotes),
  }));
  check(source.length > 3900, `source has only ${source.length} rows`);

  // Fact 1: rows per year (and in all), source vs published partitions.
  const published = [];
  const sourceYears = groupBy(source, (r) => r.year);
  const partitions = ds.paths().filter((p) => /^table\/\d{4}\.json$/.test(p));
  check(partitions.length === sourceYears.size, `${partitions.length} published year partitions, source has ${sourceYears.size} years`);
  for (const [year, rows] of sourceYears) {
    const doc = ds.json(`table/${year}.json`);
    check(doc.meta.rows === rows.length && doc.rows.length === rows.length, `${year}: published ${doc.rows.length} rows, source has ${rows.length}`);
    published.push(...doc.rows);
  }
  check(ds.entry("download/senate-results.csv").rows === source.length, "download/senate-results.csv row count differs from the source");
  const years = [...sourceYears.keys()].sort();
  check(years[0] === EXPECTED.senate.firstYear && years.at(-1) === EXPECTED.senate.lastYear, `source covers ${years[0]}-${years.at(-1)}`);
  check(ds.doc.coverage.start === years[0] && ds.doc.coverage.end === years.at(-1), `published coverage ${JSON.stringify(ds.doc.coverage)}`);

  // Fact 2: known races, certified figures vs source vs published.
  const raceKey = (r) => `${r.year}|${r.state_po}|${r.special}|${r.stage}`;
  const sourceRaces = groupBy(source, raceKey);
  const publishedRaces = groupBy(published, raceKey);
  const compareRace = (year, state, expected) => {
    const key = `${year}|${state}|false|GEN`;
    const src = sourceRaces.get(key) ?? [];
    const pub = publishedRaces.get(key) ?? [];
    for (const [candidate, votes] of Object.entries(expected)) {
      if (candidate === "total") {
        check(src.every((r) => r.total === votes), `${state} ${year}: source totalvotes is not ${votes}`);
        check(pub.every((r) => r.totalvotes === votes), `${state} ${year}: published totalvotes is not ${votes}`);
        continue;
      }
      const s = src.filter((r) => r.candidate === candidate);
      const p = pub.filter((r) => r.candidate === candidate);
      check(s.length === 1 && s[0].votes === votes, `${state} ${year} ${candidate}: expected ${votes}, source has ${s.map((r) => r.votes)}`);
      check(p.length === 1 && p[0].candidatevotes === votes, `${state} ${year} ${candidate}: expected ${votes}, published ${p.map((r) => r.candidatevotes)}`);
    }
  };
  compareRace("2024", "AZ", EXPECTED.senate.az2024);
  compareRace("2024", "MD", EXPECTED.senate.md2024);
  compareRace("2024", "WA", EXPECTED.senate.wa2024);

  // Fact 3: the extreme, the race with the most votes (MEDSL's "1" no-count marker excluded).
  const counted = source.filter((r) => r.total !== null && r.total > 1);
  const largest = counted.reduce((a, b) => (b.total > a.total ? b : a));
  const want = EXPECTED.senate.largestRace;
  check(
    largest.year === want.year && largest.state_po === want.state_po && largest.special === want.special && largest.total === want.totalvotes,
    `largest race in the source is ${largest.state_po} ${largest.year} (${largest.total})`,
  );
  const publishedMax = Math.max(...published.map((r) => r.totalvotes ?? 0));
  check(publishedMax === want.totalvotes, `largest published totalvotes is ${publishedMax}, expected ${want.totalvotes}`);

  // Fact 4: every race's votes, summed independently, match the published rows (and their totalvotes).
  check(sourceRaces.size === publishedRaces.size, `${publishedRaces.size} published races, source has ${sourceRaces.size}`);
  let checkedRaces = 0;
  for (const [key, src] of sourceRaces) {
    const pub = publishedRaces.get(key);
    check(pub && pub.length === src.length, `race ${key}: ${pub?.length} published rows, source has ${src.length}`);
    if (src.some((r) => r.total === 1)) {
      check(pub.every((r) => r.candidatevotes === null && r.totalvotes === null), `race ${key}: MEDSL's no-count marker (1) was not published as null`);
      continue;
    }
    const sourceSum = sum(src.map((r) => r.votes));
    const publishedSum = sum(pub.map((r) => r.candidatevotes));
    check(sourceSum === publishedSum, `race ${key}: votes add up to ${publishedSum} published, ${sourceSum} in the source`);
    check(pub.every((r) => r.totalvotes === src[0].total), `race ${key}: published totalvotes differs from the source`);
    checkedRaces++;
  }

  // Fact 5: fusion voting, a candidate's ballot lines added up (New York 2016, Schumer on four lines).
  const schumer = (sourceRaces.get("2016|NY|false|GEN") ?? []).filter((r) => r.candidate === "CHARLES E. SCHUMER");
  check(schumer.length === 4, `NY 2016: Schumer has ${schumer.length} ballot lines in the source`);
  const schumerTotal = sum(schumer.map((r) => r.votes));
  const published2016 = (publishedRaces.get("2016|NY|false|GEN") ?? []).filter((r) => r.candidate === "CHARLES E. SCHUMER");
  check(
    published2016.length === 4 && published2016.every((r) => r.candidate_total_votes === schumerTotal),
    `NY 2016: published candidate_total_votes for Schumer is not ${schumerTotal}`,
  );

  return `senate-results ${ds.doc.version}: ${source.length} rows in ${sourceYears.size} years, ${checkedRaces} race totals, AZ/MD/WA 2024 certified figures, largest race CA 2024 (${want.totalvotes}), fusion total ${schumerTotal}`;
}

// --- MEDSL files behind Dataverse's guestbook --------------------------------------------------------

/** The hand-downloaded copy in data/sources/medsl/, after checking it is byte for byte the cited version. */
async function guestbookFile(ds, doi, localName, dataverseName, rawName) {
  const meta = JSON.parse((await download(`${DATAVERSE}/api/datasets/:persistentId/?persistentId=${doi}`)).toString("utf8"));
  const latest = meta.data.latestVersion;
  check(latest.versionState === "RELEASED", `${doi}: latest version is ${latest.versionState}`);
  check(latest.license?.rightsIdentifier === "CC0-1.0", `${doi}: license is ${JSON.stringify(latest.license)}, expected CC0-1.0`);
  check(
    ds.doc.source.attribution.includes(`Harvard Dataverse, V${latest.versionNumber},`) && latest.versionMinorNumber === 0,
    `published attribution cites another version than Dataverse's V${latest.versionNumber}.${latest.versionMinorNumber}: ${ds.doc.source.attribution}`,
  );
  const file = latest.files.find((f) => f.dataFile.filename === dataverseName || f.dataFile.originalFileName === dataverseName);
  check(file, `${doi}: no ${dataverseName} in V${latest.versionNumber}`);
  const body = readFileSync(join(ROOT, "data", "sources", "medsl", localName));
  check(md5(body) === file.dataFile.md5, `data/sources/medsl/${localName}: md5 ${md5(body)} != Dataverse's ${file.dataFile.md5}`);
  const raw = ds.doc.source.raw.find((r) => r.name === rawName);
  check(raw && raw.sha256 === sha256(body), "the published version was built from different bytes than data/sources/medsl/ holds (re-run the pipeline)");
  return body;
}

function publishedRows(ds) {
  const rows = [];
  for (const path of ds.paths().filter((p) => /^table\/\d{4}\.json$/.test(p))) rows.push(...ds.json(path).rows);
  return rows;
}

// --- county-president --------------------------------------------------------------------------------

async function verifyCountyPresident() {
  const ds = localDataset("electiondata", "county-president");
  const body = await guestbookFile(ds, "doi:10.7910/DVN/VOQCHQ", "countypres_2000-2024.csv", "countypres_2000-2024.csv", "countypres");
  const notCandidates = new Set(["TOTAL VOTES CAST", "UNDERVOTES", "OVERVOTES", "SPOILED"]);
  const source = parseCsv(body.toString("utf8")).filter((r) => !notCandidates.has(r.candidate));
  check(source.length > 80000, `source has only ${source.length} candidate rows`);

  // A county's votes for a candidate: its TOTAL rows if it has any, otherwise all its vote-mode rows.
  // Units are told apart by name and MEDSL's own code, so a city and a county with one name stay apart.
  const units = groupBy(source, (r) => `${r.year}|${r.state_po}|${r.county_name}|${r.county_fips}`);
  const lineKey = (r) => `${r.year}|${r.state_po}|${r.county_name}|${r.candidate}|${r.party}`;
  const derived = new Map(); // year|state|name|candidate|party -> votes (null if any is NA)
  for (const rows of units.values()) {
    const hasTotal = rows.some((r) => r.mode === "TOTAL");
    for (const r of rows.filter((x) => !hasTotal || x.mode === "TOTAL")) {
      const votes = r.candidatevotes === "NA" ? null : count(r.candidatevotes);
      const before = derived.has(lineKey(r)) ? derived.get(lineKey(r)) : 0;
      derived.set(lineKey(r), before === null || votes === null ? null : before + votes);
    }
  }

  // Fact 1: every county's votes per candidate, derived independently, equal the published ones.
  const published = publishedRows(ds);
  check(ds.entry("download/county-president.csv").rows === published.length, "download/county-president.csv row count differs from the year partitions");
  const publishedVotes = new Map();
  for (const r of published) {
    const before = publishedVotes.has(lineKey(r)) ? publishedVotes.get(lineKey(r)) : 0;
    publishedVotes.set(lineKey(r), before === null || r.candidatevotes === null ? null : before + r.candidatevotes);
  }
  check(publishedVotes.size === derived.size, `${publishedVotes.size} published county/candidate lines, source has ${derived.size}`);
  for (const [key, votes] of derived) check(publishedVotes.get(key) === votes, `${key}: published ${publishedVotes.get(key)}, source ${votes}`);

  // Fact 2: certified 2020 figures for two large counties.
  for (const want of [EXPECTED.county.la2020, EXPECTED.county.maricopa2020]) {
    const rows = published.filter((r) => r.year === "2020" && r.county_fips === want.fips);
    for (const [candidate, votes] of Object.entries(want).filter(([k]) => k !== "fips")) {
      const got = rows.filter((r) => r.candidate === candidate).map((r) => r.candidatevotes);
      check(got.length === 1 && got[0] === votes, `${want.fips} 2020 ${candidate}: expected ${votes}, published ${got}`);
    }
  }

  // Fact 3: years, reporting units per year and the largest county.
  const years = [...new Set(published.map((r) => r.year))].sort();
  check(JSON.stringify(years) === JSON.stringify(EXPECTED.county.years), `published years ${years}`);
  const sourceUnits = groupBy([...units.keys()], (k) => k.split("|")[0]);
  for (const year of years) {
    const n = new Set(published.filter((r) => r.year === year).map((r) => r.unit_id)).size;
    check(n === sourceUnits.get(year).length && n > 3100, `${year}: ${n} published units, source has ${sourceUnits.get(year).length}`);
  }
  const largest = published.reduce((a, b) => (b.totalvotes > a.totalvotes ? b : a));
  const want = EXPECTED.county.largestCounty;
  check(
    largest.year === want.year && largest.county_fips === want.fips && largest.totalvotes === want.totalvotes,
    `largest county is ${largest.county_name} ${largest.year} (${largest.totalvotes})`,
  );
  const fromModes = published.filter((r) => r.from_modes).length;
  return `county-president ${ds.doc.version}: ${derived.size} county/candidate lines re-derived (${fromModes} summed from vote modes), LA and Maricopa 2020 certified figures, largest county LA 2020 (${want.totalvotes})`;
}

// --- house-results -----------------------------------------------------------------------------------

async function verifyHouse() {
  const ds = localDataset("electiondata", "house-results");
  const body = await guestbookFile(ds, "doi:10.7910/DVN/IG0UN2", "1976-2024-house.csv", "1976-2024-house.tab", "house");
  const source = parseCsv(body.toString("utf8")).map((r) => ({ ...r, votes: count(r.candidatevotes), total: count(r.totalvotes) }));
  check(source.length > 33000, `source has only ${source.length} rows`);
  const published = publishedRows(ds);
  check(published.length === source.length, `${published.length} published rows, source has ${source.length}`);
  check(ds.entry("download/house-results.csv").rows === source.length, "download/house-results.csv row count differs from the source");

  // Fact 1: every race's votes, summed independently, match the published rows.
  const raceKey = (r) => `${r.year}|${r.state_po}|${Number(r.district)}|${r.stage.toUpperCase()}|${String(r.special).toUpperCase()}`;
  const sourceRaces = groupBy(source, raceKey);
  const publishedRaces = groupBy(published, raceKey);
  check(sourceRaces.size === publishedRaces.size, `${publishedRaces.size} published races, source has ${sourceRaces.size}`);
  let noCount = 0;
  for (const [key, src] of sourceRaces) {
    const pub = publishedRaces.get(key);
    check(pub && pub.length === src.length, `race ${key}: ${pub?.length} published rows, source has ${src.length}`);
    if (src.length === 1 && [1, 0, -1].includes(src[0].total) && [1, 0, -1].includes(src[0].votes)) {
      check(pub[0].candidatevotes === null && pub[0].totalvotes === null, `race ${key}: MEDSL's no-count marker was not published as null`);
      noCount++;
      continue;
    }
    check(sum(src.map((r) => r.votes)) === sum(pub.map((r) => r.candidatevotes)), `race ${key}: published votes differ from the source`);
  }

  // Fact 2: a known race.
  const ak = publishedRaces.get("2024|AK|0|GEN|FALSE");
  check(ak, "no published AK 2024 at-large race");
  for (const [candidate, votes] of Object.entries(EXPECTED.house.ak2024)) {
    const got = ak.filter((r) => r.candidate === candidate).map((r) => r.candidatevotes);
    check(got.length === 1 && got[0] === votes, `AK 2024 ${candidate}: expected ${votes}, published ${got}`);
  }

  // Fact 3: coverage, and all 435 seats in every election year (DC's delegate, in 2020 and 2024, is not a seat).
  const years = [...new Set(published.map((r) => r.year))].sort();
  check(years[0] === EXPECTED.house.firstYear && years.at(-1) === EXPECTED.house.lastYear, `published years ${years[0]}-${years.at(-1)}`);
  for (const year of years) {
    const seats = new Set(published.filter((r) => r.year === year && r.state_po !== "DC").map((r) => `${r.state_po}|${r.district}`)).size;
    check(seats === 435, `${year}: ${seats} House seats, expected 435`);
  }
  return `house-results ${ds.doc.version}: ${source.length} rows in ${sourceRaces.size} races (${noCount} uncontested with no count), 435 seats in each of ${years.length} years, AK 2024 figures`;
}

const results = [await verifySenate(), await verifyCountyPresident(), await verifyHouse()];
console.log(`ELECTION VERIFIED (${results.join("; ")})`);
