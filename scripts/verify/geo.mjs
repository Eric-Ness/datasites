// G4: packages/geo/src/locations.json holds exactly the Census 2024 counties, the Census states and
// equivalents, and the ISO 3166-1 countries, re-measured here from the original sources with code
// independent of the Python generator (its own unzip, parsing and filtering).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { inflateRawSync } from "node:zlib";
import { ROOT, check, fail } from "./lib.mjs";

const CENSUS_COUNTIES = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip";
const CENSUS_STATES = "https://www2.census.gov/geo/docs/reference/state.txt";
const CLDR = "https://raw.githubusercontent.com/unicode-org/cldr-json/49.0.0/cldr-json/";
const ISO_3166_1_ASSIGNED = 249; // ISO's count of officially assigned alpha-2 codes

async function download(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(60_000) });
  check(res.ok, `${url} returned ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

/** Extracts the first file of a zip archive (stored or deflated). */
function unzipFirst(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65_557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  check(eocd >= 0, "gazetteer download is not a zip archive");
  const cd = buf.readUInt32LE(eocd + 16);
  check(buf.readUInt32LE(cd) === 0x02014b50, "zip central directory not found");
  const method = buf.readUInt16LE(cd + 10);
  const size = buf.readUInt32LE(cd + 20);
  const local = buf.readUInt32LE(cd + 42);
  check(buf.readUInt32LE(local) === 0x04034b50, "zip local header not found");
  const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
  const data = buf.subarray(start, start + size);
  if (method === 8) return inflateRawSync(data);
  if (method === 0) return data;
  fail(`unsupported zip compression method ${method}`);
}

// --- expected, from the sources -------------------------------------------------------------
const gazetteer = unzipFirst(await download(CENSUS_COUNTIES)).toString("utf8").split(/\r?\n/).filter((l) => l.trim());
const cols = gazetteer[0].split("\t").map((c) => c.trim());
const expCounties = new Map();
for (const line of gazetteer.slice(1)) {
  const row = Object.fromEntries(line.split("\t").map((v, i) => [cols[i], v.trim()]));
  expCounties.set(row.GEOID, row);
}

const stateLines = (await download(CENSUS_STATES)).toString("utf8").split(/\r?\n/).filter((l) => l.trim());
const expStates = new Map(stateLines.slice(1).map((l) => {
  const [fips, usps, name] = l.split("|");
  return [fips, { usps, name }];
}));

const mappings = JSON.parse(await download(CLDR + "cldr-core/supplemental/codeMappings.json")).supplemental.codeMappings;
const deprecated = JSON.parse(await download(CLDR + "cldr-core/supplemental/aliases.json")).supplemental.metadata.alias.territoryAlias;
const names = JSON.parse(await download(CLDR + "cldr-localenames-full/main/en/territories.json")).main.en.localeDisplayNames.territories;
const notCountries = (code) =>
  code === "AA" || code === "ZZ" || /^Q[M-Z]$/.test(code) || /^X[A-Z]$/.test(code) ||
  ["AC", "CP", "CQ", "DG", "EA", "EU", "EZ", "FX", "IC", "SU", "TA", "UK", "UN"].includes(code) || code in deprecated;
const expCountries = new Map(
  Object.entries(mappings).filter(([code, m]) => /^[A-Z]{2}$/.test(code) && m?._alpha3 && !notCountries(code)),
);
check(expCountries.size === ISO_3166_1_ASSIGNED, `CLDR filter yields ${expCountries.size} countries, ISO assigns ${ISO_3166_1_ASSIGNED}`);

// --- actual, from the committed registry ----------------------------------------------------
const doc = JSON.parse(readFileSync(join(ROOT, "packages", "geo", "src", "locations.json"), "utf8"));
const by = (level) => doc.locations.filter((l) => l.level === level);
const ids = doc.locations.map((l) => l.id);
check(new Set(ids).size === ids.length, "duplicate ids in locations.json");
check(by("world").length === 1 && by("world")[0].id === "world", "expected exactly one 'world' location");

const counties = new Map(by("us-county").map((c) => [c.fips, c]));
check(counties.size === expCounties.size, `${counties.size} counties in the registry, ${expCounties.size} in the gazetteer`);
for (const [geoid, row] of expCounties) {
  const c = counties.get(geoid);
  check(c, `county ${geoid} (${row.NAME}) missing`);
  check(c.id === `us-county-${geoid}` && c.name === row.NAME && c.parent === `us-${row.USPS.toLowerCase()}`, `county ${geoid} differs from the gazetteer`);
  check(Math.abs(c.lat - Number(row.INTPTLAT)) < 1e-9 && Math.abs(c.lon - Number(row.INTPTLONG)) < 1e-9, `county ${geoid} coordinates differ`);
}

const states = new Map(by("us-state").map((s) => [s.fips, s]));
check(states.size === expStates.size, `${states.size} states in the registry, ${expStates.size} in state.txt`);
for (const [fips, row] of expStates) {
  const s = states.get(fips);
  const kind = row.usps === "DC" ? "district" : ["AS", "GU", "MP", "PR", "UM", "VI"].includes(row.usps) ? "territory" : "state";
  check(s && s.usps === row.usps && s.name === row.name && s.kind === kind && s.id === `us-${row.usps.toLowerCase()}`, `state ${fips} differs`);
}

const countries = new Map(by("country").map((c) => [c.iso2, c]));
check(countries.size === expCountries.size, `${countries.size} countries in the registry, ${expCountries.size} expected`);
for (const [iso2, m] of expCountries) {
  const c = countries.get(iso2);
  check(c && c.iso3 === m._alpha3 && c.id === m._alpha3.toLowerCase() && (c.numeric ?? undefined) === m._numeric && c.name === names[iso2], `country ${iso2} differs from CLDR`);
}
check(doc.locations.length === 1 + countries.size + states.size + counties.size, "locations.json has entries outside the four levels");

// controls: a known county, Connecticut's 2022 planning regions present and its old counties gone
check(counties.get("06037")?.name === "Los Angeles County", "control: Los Angeles County missing");
const ct = [...counties.keys()].filter((f) => f.startsWith("09")).sort();
check(ct.length === 9 && ct[0] === "09110", `control: Connecticut should have 9 planning regions, found ${ct.join(",")}`);
check(!counties.has("09001"), "control: old Fairfield County (09001) should be gone");

console.log(`GEO OK: ${counties.size} counties, ${states.size} states and equivalents, ${countries.size} countries match Census 2024 and CLDR 49.0.0`);
