// Independent check of what `datasites run waterdata --target local` published. For every waterdata dataset it
// re-derives facts straight from the original sources (USGS Water Data APIs, US Drought Monitor), with its own
// requests and parsing, and compares them with the published files:
//   streamflow-daily  a known gage-day (Potomac at Little Falls), the Maryland gages reporting that day, and the
//                     Potomac's highest and lowest daily mean over the published window
//   stream-gages      the Potomac gage's attributes, the number of Maryland gages, and their largest drainage area
//   drought-county    Montgomery County MD's latest week, the Texas county count that week, and Texas's highest D4
// Prints WATER VERIFIED only after every assertion passes.
//   node scripts/verify/water.mjs [--negative-control]
// --negative-control corrupts this script's own copy of one expected value (the Potomac's discharge), so the run
// must end in FAIL: (see negative-control.mjs).
import { check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const USGS = "https://api.waterdata.usgs.gov/ogcapi/v1/collections";
const USDM = "https://usdmdataservices.unl.edu/api/CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent";
const STREAM_TYPES = ["ST", "ST-CA", "ST-DCH", "ST-TS"];
const POTOMAC = "USGS-01646500";
const HEADERS = {
  "User-Agent": "datasites-verify (+mailto:hello@climatedata.dev)",
  ...(process.env.USGS_WATERDATA_API_KEY ? { "X-Api-Key": process.env.USGS_WATERDATA_API_KEY } : {}),
};
const facts = [];

// --- fetching and parsing (deliberately separate from the Python pipeline) ---------------------------------------

async function getText(url, params = {}) {
  const full = `${url}?${new URLSearchParams(params)}`;
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(full, { headers: HEADERS, signal: AbortSignal.timeout(120_000) });
    } catch (err) {
      if (attempt >= 3) fail(`${full}: ${err.message}`);
      await new Promise((r) => setTimeout(r, 2000 * 3 ** attempt));
      continue;
    }
    if (res.ok) return res.text();
    if (attempt >= 3 || ![429, 500, 502, 503, 504].includes(res.status)) fail(`${full}: HTTP ${res.status}`);
    const wait = Number(res.headers.get("retry-after")) || 2 * 3 ** attempt;
    await new Promise((r) => setTimeout(r, wait * 1000));
  }
}

/** RFC 4180 CSV into objects keyed by the header row. */
function parseCsv(text) {
  const records = [];
  let field = "";
  let record = [];
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') (field += '"'), i++;
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") record.push(field), (field = "");
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      record.push(field), records.push(record), (record = []), (field = "");
    } else field += c;
  }
  if (field || record.length) record.push(field), records.push(record);
  const [header, ...rows] = records.filter((r) => r.length > 1 || r[0] !== "");
  return header ? rows.map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""]))) : [];
}

const usgsCsv = async (collection, params) =>
  parseCsv(await getText(`${USGS}/${collection}/items`, { f: "csv", limit: "50000", skipGeometry: "true", ...params }));

const addDays = (iso, days) => new Date(Date.parse(`${iso}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
const usDate = (iso) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}/${iso.slice(0, 4)}`;
const num = (text) => (text === "" || text === undefined || text === null ? null : Number(text));
/** USGS `begin`/`end` are UTC timestamps of local midnight; +12 h recovers the local date for UTC-12..+11. */
const localDate = (ts) => new Date(Date.parse(ts.replace(" ", "T")) + 12 * 3_600_000).toISOString().slice(0, 10);

function equal(actual, expected, what) {
  check(actual === expected, `${what}: published ${JSON.stringify(actual)}, source says ${JSON.stringify(expected)}`);
  facts.push(what);
}

// --- streamflow-daily ---------------------------------------------------------------------------------------------

const daily = localDataset("waterdata", "streamflow-daily");
const gages = localDataset("waterdata", "stream-gages");
const drought = localDataset("waterdata", "drought-county");

const potomac = daily.json("discharge/usgs-01646500.json");
check(potomac.meta.location_name.includes("POTOMAC"), `unexpected name for usgs-01646500: ${potomac.meta.location_name}`);
const potomacRows = potomac.data;
const lastDay = potomacRows.at(-1).date;
// A day a week back: inside the window, and past the first days of provisional edits.
const day = [...potomacRows].reverse().find((r) => r.date <= addDays(lastDay, -7) && r.value !== null);
check(day, "no Potomac value a week before the newest day");

// The Potomac's daily mean series, chosen independently: its Primary daily mean discharge series.
const potomacSeries = (await usgsCsv("time-series-metadata", {
  monitoring_location_id: POTOMAC, parameter_code: "00060", statistic_id: "00003",
  properties: "id,primary,sublocation_identifier,web_description",
})).filter((s) => s.primary === "Primary" && !s.sublocation_identifier && !s.web_description);
check(potomacSeries.length === 1, `expected one primary daily discharge series at ${POTOMAC}, got ${potomacSeries.length}`);
const potomacSeriesId = potomacSeries[0].id;

// Fact 1: the value (and approval) on a known day.
const sourceDay = (await usgsCsv("daily", {
  monitoring_location_id: POTOMAC, parameter_code: "00060", statistic_id: "00003", time: `${day.date}/${day.date}`,
  properties: "time_series_id,time,value,approval_status,qualifier",
})).filter((r) => r.time_series_id === potomacSeriesId);
check(sourceDay.length === 1, `USGS returned ${sourceDay.length} Potomac values for ${day.date}`);
let expectedValue = num(sourceDay[0].value);
if (NEGATIVE) expectedValue += 1; // the deliberate corruption: this must now fail
equal(day.value, expectedValue, `streamflow-daily usgs-01646500 ${day.date} discharge (ft^3/s)`);
equal(day.provisional, sourceDay[0].approval_status !== "Approved", `streamflow-daily usgs-01646500 ${day.date} provisional`);

// Fact 2: how many Maryland stream gages have a daily record that day.
const mdDaily = await usgsCsv("daily", {
  country_code: "US", state_code: "24", parameter_code: "00060", statistic_id: "00003", time: `${day.date}/${day.date}`,
  properties: "monitoring_location_id,time_series_id,time",
});
const mdIds = [...new Set(mdDaily.map((r) => r.monitoring_location_id).filter((id) => /^USGS-\d{8,15}$/.test(id)))];
check(mdIds.length > 50, `only ${mdIds.length} Maryland daily records on ${day.date}`);
const mdSites = (await usgsCsv("monitoring-locations", { id: mdIds.join(","), properties: "id,agency_code,site_type_code" }))
  .filter((s) => s.agency_code === "USGS" && STREAM_TYPES.includes(s.site_type_code));
const publishedMd = gages.json("table/us-md.json").rows;
const reportingMd = publishedMd.filter((g) => daily.text(`discharge/${g.site_id}.csv`).includes(`\n${day.date},`)).length;
equal(reportingMd, mdSites.length, `streamflow-daily Maryland stream gages with a record on ${day.date}`);

// Fact 3: the Potomac's extremes over the whole published window.
const sourceWindow = (await usgsCsv("daily", {
  monitoring_location_id: POTOMAC, parameter_code: "00060", statistic_id: "00003",
  time: `${potomacRows[0].date}/${lastDay}`, properties: "time_series_id,time,value",
})).filter((r) => r.time_series_id === potomacSeriesId && r.value !== "");
const publishedValues = potomacRows.map((r) => r.value).filter((v) => v !== null);
const sourceValues = sourceWindow.map((r) => Number(r.value));
equal(Math.max(...publishedValues), Math.max(...sourceValues), `streamflow-daily usgs-01646500 highest daily mean ${potomacRows[0].date}..${lastDay}`);
equal(Math.min(...publishedValues), Math.min(...sourceValues), `streamflow-daily usgs-01646500 lowest daily mean ${potomacRows[0].date}..${lastDay}`);
equal(publishedValues.length, sourceValues.length, "streamflow-daily usgs-01646500 days with a value in the window");

// --- stream-gages -------------------------------------------------------------------------------------------------

// Fact 1: the Potomac gage's attributes.
const potomacGage = publishedMd.find((g) => g.site_id === "usgs-01646500");
check(potomacGage, "stream-gages table/us-md.json has no usgs-01646500");
const [site] = await parseCsv(await getText(`${USGS}/monitoring-locations/items`, {
  f: "csv", id: POTOMAC, properties: "id,state_code,county_code,hydrologic_unit_code,drainage_area",
}));
equal(potomacGage.drainage_area, num(site.drainage_area), "stream-gages usgs-01646500 drainage area (mi^2)");
equal(potomacGage.latitude, Number(site.y), "stream-gages usgs-01646500 latitude");
equal(potomacGage.longitude, Number(site.x), "stream-gages usgs-01646500 longitude");
equal(potomacGage.county_fips, site.state_code + site.county_code, "stream-gages usgs-01646500 county FIPS");
equal(potomacGage.huc, site.hydrologic_unit_code, "stream-gages usgs-01646500 HUC");

// Fact 2: the Maryland gages: USGS stream sites in Maryland whose daily discharge record reaches into the window.
const windowStart = addDays(gages.doc.source.retrieved_at.slice(0, 10), -399);
const recent = await usgsCsv("time-series-metadata", {
  parameter_code: "00060", statistic_id: "00003", properties: "monitoring_location_id,end",
  filter: `end >= '${addDays(windowStart, -2)}'`,
});
const reporting = new Set(recent.filter((s) => s.end && localDate(s.end) >= windowStart).map((s) => s.monitoring_location_id));
const mdStreams = (await usgsCsv("monitoring-locations", {
  country_code: "US", state_code: "24", agency_code: "USGS", properties: "id,site_type_code,drainage_area",
  filter: `site_type_code IN (${STREAM_TYPES.map((t) => `'${t}'`).join(",")})`,
})).filter((s) => reporting.has(s.id) && /^USGS-\d{8,15}$/.test(s.id));
equal(publishedMd.length, mdStreams.length, "stream-gages Maryland gage count");

// Fact 3: the largest drainage area among them.
const largest = (rows, key) => Math.max(...rows.map((r) => r[key]).filter((v) => v !== null && v !== ""));
equal(largest(publishedMd, "drainage_area"), largest(mdStreams.map((s) => ({ a: num(s.drainage_area) })), "a"), "stream-gages Maryland largest drainage area (mi^2)");

// --- drought-county -----------------------------------------------------------------------------------------------

const montgomery = drought.json("area/us-county-24031.json").data;
const week = montgomery.at(-1).date;
const categories = { none: "None", d0: "D0", d1: "D1", d2: "D2", d3: "D3", d4: "D4" };
const usdm = async (aoi) =>
  parseCsv(await getText(USDM, { aoi, startdate: usDate(week), enddate: usDate(week), statisticsType: "2" })).filter(
    (r) => r.MapDate === week.replaceAll("-", ""),
  );

// Fact 1: Montgomery County, MD for the latest map.
const [county] = await usdm("24031");
check(county, `USDM has no map for 24031 dated ${week}`);
for (const [col, src] of Object.entries(categories)) equal(montgomery.at(-1)[col], Number(county[src]), `drought-county us-county-24031 ${week} ${col} (%)`);

// Fact 2: Texas county count for that map.
const texas = await usdm("TX");
const texasIds = Object.keys(drought.locations()).filter((id) => id.startsWith("us-county-48"));
const texasRows = texasIds.map((id) => ({ id, row: drought.json(`area/${id}.json`).data.at(-1) })).filter((t) => t.row.date === week);
equal(texasRows.length, texas.length, `drought-county Texas counties on the ${week} map`);

// Fact 3: the most severe category anywhere in Texas that week, its largest share of a county, and which counties.
const severest = ["D4", "D3", "D2", "D1", "D0", "None"].find((c) => texas.some((r) => Number(r[c]) > 0));
const col = severest === "None" ? "none" : severest.toLowerCase();
const peak = Math.max(...texas.map((r) => Number(r[severest])));
equal(Math.max(...texasRows.map((t) => t.row[col])), peak, `drought-county Texas highest ${severest} on ${week} (%)`);
const sortedIds = (ids) => ids.sort().join(",");
equal(
  sortedIds(texasRows.filter((t) => t.row[col] === peak).map((t) => t.id.slice(-5))),
  sortedIds(texas.filter((r) => Number(r[severest]) === peak).map((r) => r.FIPS)),
  `drought-county Texas counties at the highest ${severest} on ${week}`,
);

console.log(
  `WATER VERIFIED (${facts.length} facts re-derived from USGS and USDM: Potomac ${day.date} = ${day.value} ft^3/s, ` +
    `${reportingMd} Maryland gages reporting, ${publishedMd.length} Maryland gages listed, ${texas.length} Texas counties on ${week})`,
);
