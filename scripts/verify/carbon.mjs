// Independent check of carbondata: re-derives facts from the original source files and compares them with
// what `uv run datasites run carbondata --target local` published. Shares no code with the Python pipeline:
// the XLSX files are read with the small zip/XML reader below, the CSV files with the CSV parser below.
//
// The source files are the raw downloads the run archived in .data/r2 (sha256-checked against the version
// document). The pinned Zenodo files are also checked against the MD5 checksums Zenodo publishes, so they
// are byte-identical to the Cornerstone releases.
//
//   node scripts/verify/carbon.mjs                      prints CARBON VERIFIED ... when every check passes
//   node scripts/verify/carbon.mjs --negative-control   corrupts one expected value; must print FAIL:
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { inflateRawSync } from "node:zlib";
import { ROOT, check, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const SITE = "carbondata";
let facts = 0;

// MD5 checksums from the Zenodo records (https://zenodo.org/api/records/<id>, files[].checksum).
const ZENODO_MD5 = {
  "epa-hub": "5b182fd5288eee05a0102bbfb5fb58e5", // 20403776 Emission Factors Hub_2026_machine.xlsx
  egrid: "fb8e79ee783e0b2e86c63d61726d950a", // 18968658 egrid2024_data_metric.xlsx
  useeio: "6dc23409c31157647e92b69809b5c8ee", // 17202747 SupplyChainGHGEmissionFactorsv1.4.0.xlsx
};

function same(actual, expected, label) {
  const ok = typeof actual === "number" && typeof expected === "number" && Math.abs(actual - expected) <= 1e-9 * Math.max(1, Math.abs(expected));
  check(ok, `${label}: published ${actual}, source says ${expected}`);
  facts += 1;
}

function equal(actual, expected, label) {
  check(JSON.stringify(actual) === JSON.stringify(expected), `${label}: published ${JSON.stringify(actual)}, source says ${JSON.stringify(expected)}`);
  facts += 1;
}

const digest = (algo, buf) => createHash(algo).update(buf).digest("hex");

/** A raw download recorded in the published version document, read from the local bucket and hash-checked. */
function rawFile(ds, match) {
  const entry = ds.doc.source.raw.find((r) => match(r.name));
  check(entry, `${ds.doc.dataset}: no raw file matching ${match} in version ${ds.doc.version}`);
  check(entry.key, `${ds.doc.dataset}: raw file ${entry.name} was not archived`);
  const body = readFileSync(join(ROOT, ".data", "r2", entry.key));
  check(digest("sha256", body) === entry.sha256, `${entry.key}: sha256 does not match the version document`);
  return { name: entry.name, body };
}

// --- XLSX: zip central directory + inflate + a regex XML reader ----------------------------------------

function unzip(buf) {
  let end = buf.length - 22;
  while (end >= 0 && buf.readUInt32LE(end) !== 0x06054b50) end -= 1;
  check(end >= 0, "not a zip file");
  const entries = new Map();
  let p = buf.readUInt32LE(end + 16);
  for (let i = 0; i < buf.readUInt16LE(end + 10); i += 1) {
    check(buf.readUInt32LE(p) === 0x02014b50, "corrupt zip central directory");
    const [method, size, nameLen, extraLen, commentLen, local] = [10, 20, 28, 30, 32, 42].map((o, j) => (j === 1 || j === 5 ? buf.readUInt32LE(p + o) : buf.readUInt16LE(p + o)));
    entries.set(buf.toString("utf8", p + 46, p + 46 + nameLen), { method, size, local });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return (name) => {
    const e = entries.get(name);
    if (!e) return null;
    const start = e.local + 30 + buf.readUInt16LE(e.local + 26) + buf.readUInt16LE(e.local + 28);
    const data = buf.subarray(start, start + e.size);
    return (e.method === 0 ? data : inflateRawSync(data)).toString("utf8");
  };
}

const unescapeXml = (s) =>
  s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, e) =>
    e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : Number(e.slice(1))) : { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" }[e.toLowerCase()],
  );
const textOf = (xml) => unescapeXml([...xml.replace(/<rPh\b[\s\S]*?<\/rPh>/g, "").matchAll(/<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g)].map((m) => m[1]).join(""));

function workbook(buf) {
  const read = unzip(buf);
  const shared = [...(read("xl/sharedStrings.xml") ?? "").matchAll(/<si>([\s\S]*?)<\/si>/g)].map((m) => textOf(m[1]));
  const targets = Object.fromEntries([...read("xl/_rels/workbook.xml.rels").matchAll(/<Relationship\b[^>]*>/g)].map((m) => [/Id="([^"]+)"/.exec(m[0])[1], /Target="([^"]+)"/.exec(m[0])[1]]));
  const sheets = Object.fromEntries(
    [...read("xl/workbook.xml").matchAll(/<sheet\b[^>]*>/g)].map((m) => [unescapeXml(/name="([^"]*)"/.exec(m[0])[1]).trim(), targets[/r:id="([^"]+)"/.exec(m[0])[1]]]),
  );
  /** Rows of a sheet as arrays of cell text (undefined where blank), in sheet order. */
  return (name) => {
    check(sheets[name], `workbook has no sheet ${name} (has ${Object.keys(sheets)})`);
    const path = sheets[name].startsWith("/") ? sheets[name].slice(1) : `xl/${sheets[name]}`;
    const rows = [];
    for (const row of read(path).matchAll(/<row\b[^>]*?(?:\/>|>([\s\S]*?)<\/row>)/g)) {
      const cells = [];
      for (const c of (row[1] ?? "").matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
        const ref = /r="([A-Z]+)\d+"/.exec(c[1])[1];
        const col = [...ref].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
        const type = /t="([^"]+)"/.exec(c[1])?.[1];
        const v = /<v>([\s\S]*?)<\/v>/.exec(c[2] ?? "")?.[1];
        cells[col] = type === "s" ? shared[Number(v)] : type === "inlineStr" ? textOf(c[2] ?? "") : v === undefined ? undefined : unescapeXml(v);
      }
      rows.push(cells);
    }
    return rows;
  };
}

/** The records below the row whose first cell is `first`, keyed by that row's (trimmed) cells. */
function records(rows, first) {
  const at = rows.findIndex((r) => (r[0] ?? "").trim() === first);
  check(at >= 0, `no header row starting with ${first}`);
  const names = Array.from(rows[at], (c) => (c ?? "").trim()); // rows are sparse where cells are blank
  return rows.slice(at + 1).map((r) => Object.fromEntries(names.map((n, i) => [n, r[i] === undefined ? undefined : r[i].trim()])));
}

// --- CSV ------------------------------------------------------------------------------------------------

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') (field += '"'), (i += 1);
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") row.push(field), (field = "");
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field), rows.push(row), (row = []), (field = "");
    } else field += ch;
  }
  if (field || row.length) row.push(field), rows.push(row);
  const [header, ...data] = rows;
  return data.filter((r) => r.length > 1).map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

const num = (s) => (s === undefined || s === "" ? null : Number(s));

// --- emission-factors -----------------------------------------------------------------------------------

const ef = localDataset(SITE, "emission-factors");
const rows = ef.json("table/all.json").rows;
check(rows.length === ef.json("table/all.json").meta.rows, "table/all.json row count does not match its meta");
const part = (source) => rows.filter((r) => r.source === source);
const only = (list, label) => {
  check(list.length === 1, `${label}: expected exactly one published row, found ${list.length}`);
  return list[0];
};
equal(new Set(rows.map((r) => r.factor_id)).size, rows.length, "factor_id is unique");
for (const source of ["epa-hub", "egrid", "uk-desnz", "ember", "useeio"]) {
  equal(ef.json(`table/${source}.json`).rows.length, part(source).length, `table/${source}.json holds that source's rows`);
}

const raw = Object.fromEntries(["epa-hub", "egrid", "useeio", "uk-desnz", "ember"].map((n) => [n, rawFile(ef, (name) => name === n).body]));
for (const [name, md5] of Object.entries(ZENODO_MD5)) equal(digest("md5", raw[name]), md5, `${name}: archived file matches Zenodo's MD5`);

// Hub: natural gas CO2 per mmBtu, biogenic CO2 outside the scopes, total-output electricity only, AR4 waste
{
  const hub = workbook(raw["epa-hub"]);
  const stationary = records(hub("Stationary Combustion"), "Emission Category");
  const ng = only(stationary.filter((r) => r["Emission Source"] === "Natural Gas" && r["Greenhouse Gas"] === "CO2" && r["UOM Denominator"] === "mmBtu"), "Hub natural gas");
  let expected = Number(ng["Emission Factor"]);
  equal(expected, 53.06, "Hub 2026 natural gas CO2 (kg/mmBtu) as pinned");
  if (NEGATIVE) expected *= 1.01; // the negative control corrupts this one expected value
  const published = only(part("epa-hub").filter((r) => r.activity === "Natural Gas" && r.gas === "CO2" && r.activity_unit === "mmBtu"), "published Hub natural gas");
  same(published.value, expected, "Hub natural gas CO2 kg/mmBtu");
  equal([published.unit, published.gwp_set, published.scope], ["kg CO2/mmBtu", "none", "1"], "Hub natural gas labels");

  const biogenic = stationary.filter((r) => r["Emission Source Category"].startsWith("Biomass Fuels") && r["Emission Source"] !== "Peat" && r["Greenhouse Gas"] === "CO2").length;
  const mobile = records(hub("Mobile Combustion CO2"), "Emission Category").filter((r) => /\(100%\)$/.test(r["Fuel Type"]) && r["Greenhouse Gas"] === "CO2").length;
  equal(part("epa-hub").filter((r) => r.scope === "outside").length, biogenic + mobile, "Hub biogenic CO2 rows reported outside the scopes");

  const electricity = records(hub("Electricity"), "Emission Category");
  const total = electricity.filter((r) => r["Emission Source"] === "Total Output");
  equal(part("epa-hub").filter((r) => r.category === "electricity").length, total.length, "Hub electricity: total output rows only");
  const camxHub = Number(only(total.filter((r) => r["eGRID Subregion Acronym"] === "CAMX" && r["Greenhouse Gas"] === "CO2"), "Hub CAMX")["Emission Factor"]);
  same(only(part("epa-hub").filter((r) => r.region === "CAMX" && r.gas === "CO2"), "published Hub CAMX").value, camxHub, "Hub CAMX CO2 kg/MWh");

  const waste = records(hub("Scope 3 Cat 5 Waste"), "Emission Category");
  check(waste.every((r) => /AR4/.test(r.Notes)), "Hub waste notes no longer say AR4");
  const publishedWaste = part("epa-hub").filter((r) => r.category === "waste");
  equal([publishedWaste.length, [...new Set(publishedWaste.map((r) => r.gwp_set))]], [waste.length, ["AR4"]], "Hub waste factors labelled AR4");
}

// eGRID2024: CAMX rates, subregion and state counts, the highest state CO2 rate
{
  const egrid = workbook(raw.egrid);
  const srl = records(egrid("SRL24"), "YEAR");
  const st = records(egrid("ST24"), "YEAR");
  const camx = only(srl.filter((r) => r.SUBRGN === "CAMX"), "eGRID CAMX");
  const pub = (region, gas) => only(part("egrid").filter((r) => r.region === region && r.gas === gas), `published eGRID ${region} ${gas}`);
  same(pub("CAMX", "CO2").value, Number(camx.SRCO2RTA), "eGRID2024 CAMX CO2 kg/MWh");
  same(pub("CAMX", "CO2e").value, Number(camx.SRC2ERTA), "eGRID2024 CAMX CO2e kg/MWh");
  equal(pub("CAMX", "CO2e").gwp_set, "AR5", "eGRID CO2e GWP set");
  equal(new Set(part("egrid").filter((r) => r.region_type === "egrid-subregion").map((r) => r.region)).size, srl.length, "eGRID subregion count");
  equal(new Set(part("egrid").filter((r) => r.region_type === "us-state").map((r) => r.region)).size, st.length, "eGRID state count");
  const top = st.reduce((a, b) => (Number(b.STCO2RTA) > Number(a.STCO2RTA) ? b : a));
  const topPub = part("egrid").filter((r) => r.region_type === "us-state" && r.gas === "CO2").reduce((a, b) => (b.value > a.value ? b : a));
  equal(topPub.region, `us-${top.PSTATABB.toLowerCase()}`, "eGRID state with the highest CO2 rate");
  same(topPub.value, Number(top.STCO2RTA), "eGRID highest state CO2 rate");
}

// UK DESNZ 2026: UK grid electricity, emission-factor row count, largest factor
{
  const desnz = records(workbook(raw["uk-desnz"])("Factors by Category"), "ID");
  const valueColumn = Object.keys(desnz[0]).find((k) => /^GHG Conversion Factor \d{4}$/.test(k));
  const emission = desnz.filter((r) => r.ID && /^kg CO2e/.test(r["GHG/Unit"] ?? "") && num(r[valueColumn]) !== null);
  const uk = only(desnz.filter((r) => r.ID === "7_400_4000_5_1"), "DESNZ UK electricity");
  same(only(part("uk-desnz").filter((r) => r.source_ref === "7_400_4000_5_1"), "published DESNZ UK electricity").value, Number(uk[valueColumn]), "DESNZ 2026 UK electricity kg CO2e/kWh");
  equal(part("uk-desnz").length, emission.length, "DESNZ rows with a published emission factor");
  same(Math.max(...part("uk-desnz").map((r) => r.value)), Math.max(...emission.map((r) => Number(r[valueColumn]))), "DESNZ largest factor");
}

// Ember: US grid intensity, number of country/world factors, highest intensity
{
  const ember = parseCsv(raw.ember.toString("utf8")).filter((r) => r["Electricity source"] === "Total generation");
  const intensity = (r) => num(r["Emissions intensity (gCO2e/kWh)"]);
  const kept = ember.filter((r) => (r["Area type"] === "Country or economy" || r.Area === "World") && r["ISO 3 code"] !== "XKX" && intensity(r) !== null && intensity(r) >= 0);
  const newest = String(Math.max(...kept.filter((r) => r["ISO 3 code"] === "USA").map((r) => Number(r.Year))));
  const us = only(kept.filter((r) => r["ISO 3 code"] === "USA" && r.Year === newest), "Ember USA");
  same(only(part("ember").filter((r) => r.region === "usa" && r.valid_year === newest), "published Ember USA").value, intensity(us) / 1000, `Ember USA ${newest} kg CO2e/kWh`);
  equal(part("ember").length, kept.length, "Ember country and world intensities");
  same(Math.max(...part("ember").map((r) => r.value)), Math.max(...kept.map(intensity)) / 1000, "Ember highest intensity");
}

// USEEIO v1.4: soybean farming with margins, commodity count, largest factor
{
  const sef = records(workbook(raw.useeio)("CO2e"), "2017 NAICS Code");
  const soy = only(sef.filter((r) => r["2017 NAICS Code"] === "111110"), "USEEIO 111110");
  same(only(part("useeio").filter((r) => r.activity === "Soybean Farming (NAICS 111110), with margins"), "published soybean").value, Number(soy["Supply Chain Emission Factors with Margins"]), "USEEIO soybean farming kg CO2e/USD");
  equal(part("useeio").length, sef.length * 2, "USEEIO factors (with and without margins per commodity)");
  same(Math.max(...part("useeio").map((r) => r.value)), Math.max(...sef.map((r) => Number(r["Supply Chain Emission Factors with Margins"]))), "USEEIO largest factor");
}

// --- national-fossil-co2 ----------------------------------------------------------------------------------

const co2 = localDataset(SITE, "national-fossil-co2");
const gcbFile = rawFile(co2, (name) => /^GCB\d{4}v\d+_MtCO2_flat\.csv$/.test(name));
const gcb = parseCsv(gcbFile.body.toString("utf8"));
const year = String(Math.max(...gcb.filter((r) => r.Country === "Global" && num(r.Total) !== null).map((r) => Number(r.Year))));
{
  const series = (s, loc) => Object.fromEntries(co2.json(`${s}/${loc}.json`).data.map((d) => [d.date, d.value]));
  const usa = only(gcb.filter((r) => r["ISO 3166-1 alpha-3"] === "USA" && r.Year === year), "GCB USA");
  same(series("total", "usa")[year], num(usa.Total), `GCB ${gcbFile.name}: USA ${year} total MtCO2`);
  same(series("coal", "chn")[year], num(only(gcb.filter((r) => r["ISO 3166-1 alpha-3"] === "CHN" && r.Year === year), "GCB China").Coal), `China ${year} coal MtCO2`);

  const world = only(gcb.filter((r) => r.Country === "Global" && r.Year === year), "GCB Global");
  const others = gcb.filter((r) => r.Country !== "Global" && r.Year === year).reduce((sum, r) => sum + (num(r.Total) ?? 0), 0);
  check(Math.abs(others - num(world.Total)) < 0.01, `GCB entities sum to ${others}, Global is ${world.Total}`);
  same(series("total", "world")[year], num(world.Total), `world ${year} total (= sum of all entities)`);
  const worldSeries = series("total", "world");
  const peak = gcb.filter((r) => r.Country === "Global" && num(r.Total) !== null).reduce((a, b) => (num(b.Total) > num(a.Total) ? b : a));
  same(Math.max(...Object.values(worldSeries)), num(peak.Total), `world peak total (${peak.Year})`);

  const columns = ["Total", "Coal", "Oil", "Gas", "Cement", "Flaring", "Other"];
  const entities = new Set(gcb.filter((r) => columns.some((c) => num(r[c]) !== null)).map((r) => r.Country));
  equal(Object.keys(co2.locations()).length, entities.size, "locations with data (countries, world and GCB entities)");
  const cells = gcb.reduce((n, r) => n + columns.filter((c) => num(r[c]) !== null).length, 0);
  equal(co2.entry("download/national-fossil-co2.csv").rows, cells, "published rows = non-blank source cells");
}

console.log(
  `CARBON VERIFIED ${facts} facts: emission-factors ${ef.doc.version} (${rows.length} factors, ${["epa-hub", "egrid", "uk-desnz", "ember", "useeio"].map((s) => `${s} ${part(s).length}`).join(", ")}); ` +
    `national-fossil-co2 ${co2.doc.version} (${gcbFile.name}, ${Object.keys(co2.locations()).length} locations, world ${year} ${co2.json("total/world.json").data.at(-1).value} MtCO2)`,
);
