// Independent check of what `datasites run satellitedata --target local` published. It reads the original GCAT
// files that run downloaded from planet4589.org (the engine archives the bytes exactly as served, and this script
// checks their SHA-256 against the version document), parses them with its own code, and compares:
//   satellite-catalog  the ISS (Zarya, S25544 / SATCAT 25544) record, the total object count, the number of objects
//                      in Earth orbit, the highest catalog number and the first six-digit number (100000)
//   launches           Sputnik's launch time, the launch count, the orbital launch count and the latest orbital launch
//   gp-current         Space-Track needs an account: without SPACETRACK_USERNAME/PASSWORD it must be unpublished
//                      (needs-credentials); with them, the ISS element set, the row count and the highest number are
//                      checked against the archived GP response
// Reading the archive instead of downloading again spares GCAT, one person's site, a second 50 MB pull.
// Prints SATELLITE VERIFIED only after every assertion passes.
//   node scripts/verify/satellite.mjs [--negative-control]
// --negative-control corrupts this script's own copy of one expected value (the ISS catalog number), so the run
// must end in FAIL: (see negative-control.mjs).
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const SITE = "satellitedata";
const facts = [];

function equal(actual, expected, what) {
  check(actual === expected, `${what}: published ${JSON.stringify(actual)}, source says ${JSON.stringify(expected)}`);
  facts.push(what);
}

// --- the original files -----------------------------------------------------------------------------------------

/** The raw bytes a published version was built from, verified against the SHA-256 the version document records. */
function rawFiles(ds) {
  const files = {};
  for (const entry of ds.doc.source.raw) {
    check(entry.key, `${ds.doc.dataset}: raw file ${entry.name} was not archived`);
    const body = readFileSync(join(ROOT, ".data", "r2", entry.key));
    const sha = createHash("sha256").update(body).digest("hex");
    check(sha === entry.sha256, `${ds.doc.dataset}: archived ${entry.name} does not match its recorded SHA-256`);
    files[entry.name] = body.toString("utf8");
  }
  return files;
}

/** A GCAT TSV: '#'-prefixed header, '# Updated' line, then tab-separated records with padded fields. */
function tsv(text, name) {
  const lines = text.split("\n").map((l) => l.replace(/\r$/, ""));
  check(lines[0].startsWith("#") && lines[0].includes("\t"), `${name}: no column header`);
  const header = lines[0].slice(1).split("\t").map((h) => h.trim());
  const rows = [];
  for (const line of lines.slice(1)) {
    if (!line.trim() || line.startsWith("#")) continue;
    const cells = line.split("\t");
    check(cells.length === header.length, `${name}: a record has ${cells.length} fields, header has ${header.length}`);
    rows.push(Object.fromEntries(header.map((h, i) => [h, cells[i].trim()])));
  }
  return rows;
}

const MONTHS = { Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6, Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12 };
const pad = (n, w = 2) => String(n).padStart(w, "0");
/** 'YYYY Mon D' with nothing after, or with a time and no '?': the UTC day. Anything vaguer: null. */
function exactDay(text) {
  const m = /^(\d{4}) ([A-Z][a-z]{2}) +(\d{1,2})(?: (\d{4})(?::\d{2}(?:\.\d+)?)?)?$/.exec(text.trim());
  return m && MONTHS[m[2]] ? `${m[1]}-${pad(MONTHS[m[2]])}-${pad(Number(m[3]))}` : null;
}

/** All rows of a published table, from its partition files (table/all.* is skipped for big tables). */
function publishedRows(ds) {
  const partitions = ds.paths().filter((p) => /^table\/.+\.json$/.test(p) && p !== "table/all.json");
  const rows = [];
  const counts = {};
  for (const path of partitions) {
    const doc = ds.json(path);
    counts[path.slice(6, -5)] = doc.rows.length;
    rows.push(...doc.rows);
  }
  return { rows, counts };
}

// --- satellite-catalog ------------------------------------------------------------------------------------------

{
  const ds = localDataset(SITE, "satellite-catalog");
  const raw = rawFiles(ds);
  const MAIN = ["satcat", "satcat100k", "satcat270k", "auxcat", "tmpcat", "ftocat"];
  const objects = new Map(); // JCAT -> { catalog, row }
  for (const catalog of MAIN) {
    for (const row of tsv(raw[catalog], catalog)) {
      if (row.Type === "-") continue; // deleted entry
      check(!objects.has(row.JCAT), `${row.JCAT} is in two catalogs`);
      objects.set(row.JCAT, { catalog, row });
    }
  }
  const phases = new Map(); // JCAT -> its ecat phases in order, without lease/alias ('PA') records
  for (const row of tsv(raw.ecat, "ecat")) {
    if (row.Type.startsWith("PA")) continue;
    if (!phases.has(row.JCAT)) phases.set(row.JCAT, []);
    phases.get(row.JCAT).push(row);
  }
  const lastPhase = new Map([...phases].map(([jcat, list]) => [jcat, list[list.length - 1]]));
  const current = new Map(tsv(raw.currentcat, "currentcat").map((r) => [r.JCAT, r]));

  const { rows, counts } = publishedRows(ds);
  const byJcat = new Map(rows.map((r) => [r.jcat, r]));

  // 1. the ISS core module: Zarya, launched 1998 Nov 20, catalog number 25544, in orbit with currentcat's orbit
  const zarya = objects.get("S25544").row;
  const zaryaNow = lastPhase.get("S25544") ?? zarya;
  const zaryaCurrent = current.get("S25544");
  const iss = byJcat.get("S25544");
  check(iss, "S25544 (ISS Zarya) is not in the published catalog");
  const expectedNorad = NEGATIVE ? Number(zarya.Satcat) + 1 : Number(zarya.Satcat);
  equal(iss.norad_cat_id, expectedNorad, "ISS catalog number");
  equal(iss.piece, zarya.Piece, "ISS international designator");
  equal(iss.launch_date, exactDay(zarya.LDate), "ISS launch date");
  equal(iss.name, zaryaCurrent.Name, "ISS name");
  equal(iss.owner, zaryaNow.Owner, "ISS owner (latest phase)");
  equal(iss.status_code, zaryaNow.Status, "ISS status code (latest phase)");
  equal(iss.status, zaryaCurrent.ExpandedStatus, "ISS status");
  equal(iss.perigee_km, Number(zaryaCurrent.Perigee), "ISS perigee");
  equal(iss.apogee_km, Number(zaryaCurrent.Apogee), "ISS apogee");
  equal(iss.inclination_deg, Number(zaryaCurrent.Inc), "ISS inclination");
  equal(iss.decay_date, null, "ISS decay date");

  // 2. how many objects there are, and how many are in Earth orbit
  equal(rows.length, objects.size, "object count");
  // Where an object is: from its latest phase's status code (phases.html); objects that left for deep space are
  // followed through currentcat's text, and through their host when they travelled attached to or inside one.
  const IN_ORBIT = new Set(["O", "AO", "AO IN", "UDK", "REL", "N", "NA", "LEASE", "DK", "GRP", "ATT", "TFR", "TFR E", "TFR IN", "EVA DP", "EVA RP"]);
  const DOWN = new Set(["R", "D", "L", "LF", "AR", "AR IN", "AL", "AL IN"]);
  const DEEP = new Set(["DSO", "DSA", "DSA IN"]);
  const where = new Map();
  const locate = (jcat, depth = 0) => {
    if (where.has(jcat)) return where.get(jcat);
    const object = objects.get(jcat);
    if (!object || depth > 10) return "other";
    const { catalog, row } = object;
    let status = (lastPhase.get(jcat) ?? row).Status.replace(/\?$/, "");
    if (status === "REFLT") {
      // a zero-length phase after landing (STS-1's Columbia flew again as another number): the phase before decides
      const list = [row, ...(phases.get(jcat) ?? [])];
      status = list[list.length - 2].Status.replace(/\?$/, "");
    }
    let place = "other";
    if (catalog === "ftocat" || row.Type.startsWith("Z")) place = "other";
    else if (IN_ORBIT.has(status)) place = "orbit";
    else if (DOWN.has(status)) place = "down";
    else if (DEEP.has(status)) {
      const now = current.get(jcat)?.ExpandedStatus ?? "";
      const hosted = /^(Landed|Reentered|Deorbited|Crashed|Attached|Docked)(?: attached to| inside| to| with) ([A-Z]\d+)\b/.exec(now);
      if (now.startsWith("In Earth orbit")) place = "orbit";
      else if (/^((Reentered|Deorbited into) Earth atmosphere|(Landed|Crashed) on Earth)/.test(now)) place = "down";
      else if (hosted) {
        const host = locate(hosted[2], depth + 1);
        if (["Attached", "Docked"].includes(hosted[1]) && host !== "other") place = host;
        else if (!["Attached", "Docked"].includes(hosted[1]) && host === "down") place = "down";
      }
    }
    where.set(jcat, place);
    return place;
  };
  let inOrbit = 0;
  for (const jcat of objects.keys()) if (locate(jcat) === "orbit") inOrbit++;
  equal(counts["in-orbit"], inOrbit, "objects in Earth orbit");
  // GCAT's own words agree: currentcat calls these objects in Earth orbit, attached or docked in orbit
  const saysInOrbit = [...current.values()].filter((r) => objects.has(r.JCAT) && !r.Type.startsWith("Z") &&
    /^(In Earth orbit|Attached to |Docked with )/.test(r.ExpandedStatus) && !/^Attached to D\d/.test(r.ExpandedStatus)).length;
  check(Math.abs(saysInOrbit - inOrbit) <= inOrbit * 0.01, `in-orbit count ${inOrbit} is far from currentcat's ${saysInOrbit}`);

  // 3. the highest catalog number, and the first number past the five-digit range
  const numbers = [...objects.values()].map(({ row }) => row.Satcat).filter((s) => /^\d+$/.test(s)).map(Number);
  const highest = numbers.reduce((a, b) => Math.max(a, b), 0);
  const publishedHighest = rows.reduce((a, r) => Math.max(a, r.norad_cat_id ?? 0), 0);
  equal(publishedHighest, highest, "highest catalog number");
  const first6 = objects.get("S100000").row;
  equal(byJcat.get("S100000")?.norad_cat_id, Number(first6.Satcat), "first six-digit catalog number");
  equal(byJcat.get("S100000")?.name, current.get("S100000").Name, "first six-digit object's name");
  check(ds.doc.source.attribution === "data from GCAT (J. McDowell, planet4589.org/space/gcat)", "satellite-catalog attribution");
  check(ds.doc.source.license === "CC-BY-4.0" && ds.doc.source.commercial_ok === true, "satellite-catalog license");
  facts.push(`(${objects.size} objects, ${inOrbit} in orbit, highest ${highest})`);
}

// --- launches ---------------------------------------------------------------------------------------------------

{
  const ds = localDataset(SITE, "launches");
  const raw = rawFiles(ds);
  const launches = tsv(raw.launch, "launch");
  const { rows, counts } = publishedRows(ds);
  const byTag = new Map(rows.map((r) => [r.launch_tag, r]));

  // 1. Sputnik: GCAT gives first motion to the second
  const sputnik = launches.find((r) => r.Launch_Tag === "1957 ALP");
  const m = /^(\d{4}) ([A-Z][a-z]{2}) +(\d{1,2}) (\d{2})(\d{2}):(\d{2})$/.exec(sputnik.Launch_Date);
  check(m, `Sputnik's launch date is not to the second: ${sputnik.Launch_Date}`);
  const sputnikTime = `${m[1]}-${pad(MONTHS[m[2]])}-${pad(Number(m[3]))}T${m[4]}:${m[5]}:${m[6]}Z`;
  equal(byTag.get("1957 ALP")?.launch_time, sputnikTime, "Sputnik launch time");
  equal(byTag.get("1957 ALP")?.lv_type, sputnik.LV_Type, "Sputnik launch vehicle");

  // 2. how many launches, and how many orbital ones
  equal(rows.length, launches.length, "launch count");
  const orbital = launches.filter((r) => r.LaunchCode.startsWith("O"));
  equal(counts.orbital, orbital.length, "orbital launch count");

  // 3. the most recent orbital launch (by GCAT's Julian date)
  const latest = orbital.reduce((a, r) => (Number(r.Launch_JD) > Number(a.Launch_JD) ? r : a));
  const publishedLatest = rows.filter((r) => r.launch_class === "orbital").reduce((a, r) => (r.launch_jd > a.launch_jd ? r : a));
  equal(publishedLatest.launch_tag, latest.Launch_Tag, "latest orbital launch");
  equal(publishedLatest.launch_date, exactDay(latest.Launch_Date), "latest orbital launch date");
  facts.push(`(${launches.length} launches, ${orbital.length} orbital, latest ${latest.Launch_Tag})`);
}

// --- gp-current (Space-Track, credentialed) ---------------------------------------------------------------------

{
  const manifest = join(ROOT, ".data", "r2", "sites", SITE, "datasets", "gp-current", "manifest.json");
  const haveCredentials = Boolean(process.env.SPACETRACK_USERNAME && process.env.SPACETRACK_PASSWORD);
  if (!existsSync(manifest)) {
    check(!haveCredentials, "gp-current has credentials in the environment but was never published");
    facts.push("gp-current: needs credentials (not published, as expected without a Space-Track account)");
  } else {
    const ds = localDataset(SITE, "gp-current");
    const raw = rawFiles(ds);
    const elements = JSON.parse(raw.gp);
    const { rows } = publishedRows(ds);
    equal(rows.length, elements.length, "gp-current element set count");
    const issSet = elements.find((e) => String(e.NORAD_CAT_ID) === "25544");
    const issRow = rows.find((r) => r.norad_cat_id === 25544);
    check(issSet && issRow, "gp-current has no element set for 25544");
    equal(issRow.epoch, `${issSet.EPOCH}Z`, "ISS element set epoch");
    equal(issRow.mean_motion, Number(issSet.MEAN_MOTION), "ISS mean motion");
    const highest = elements.reduce((a, e) => Math.max(a, Number(e.NORAD_CAT_ID)), 0);
    equal(rows.reduce((a, r) => Math.max(a, r.norad_cat_id), 0), highest, "gp-current highest catalog number");
  }
}

console.log(`SATELLITE VERIFIED ${facts.length} facts: ${facts.join("; ")}`);
