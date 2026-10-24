// Independent check of what `datasites run electiondata --target local` published.
// For every dataset it downloads the original source again, re-derives facts with its own code (its own
// CSV parser, no pipeline code) and compares them with the published files and with known certified results.
//   node scripts/verify/election.mjs                      prints ELECTION VERIFIED ... when everything matches
//   node scripts/verify/election.mjs --negative-control   corrupts one expected value; must fail with FAIL:
import { createHash } from "node:crypto";
import { check, localDataset } from "./lib.mjs";

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

const results = [await verifySenate()];
console.log(`ELECTION VERIFIED (${results.join("; ")})`);
