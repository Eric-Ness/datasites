// G4/G5: every wildfiredata dataset published to the local bucket matches facts re-derived here, with
// code written independently of the Python pipelines.
//
// Live fire data changes every few minutes, so the source snapshot to compare against is the one the run
// itself fetched: each version document lists the raw files it was built from (with their SHA-256), and the
// run archived those exact bytes in the bucket. This script reads them, checks their hashes, and re-derives
// the published facts with its own parsers (CSV, ArcGIS JSON, zip, shapefile, dBASE), its own clustering
// (a breadth-first search over detection pairs, not the pipeline's union-find over cell runs) and its own
// point-in-polygon test. A few facts that are stable for hours or days are also checked against the live
// services (an incident's name and discovery time; a finalized HMS day file).
//   node scripts/verify/wildfire.mjs                     prints WILDFIRE VERIFIED ... when everything matches
//   node scripts/verify/wildfire.mjs --negative-control  corrupts one expected value, so it must FAIL
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { inflateRawSync } from "node:zlib";
import { ROOT, check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const SITE = "wildfiredata";
const UA = "datasites-verify (+mailto:hello@climatedata.dev)";
const WFIGS = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services";
const facts = [];

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const close = (a, b, tol) => typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= tol;
const isoMs = (ms) => (ms === null || ms === undefined ? null : new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z"));
const maxOf = (xs, f) => xs.reduce((m, x) => Math.max(m, f(x)), -Infinity); // no spread: arrays can be huge
const minOf = (xs, f) => xs.reduce((m, x) => Math.min(m, f(x)), Infinity);
const irwin = (v) => (v ? String(v).trim().replace(/^\{|\}$/g, "").toUpperCase() : null);

async function live(url) {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, { headers: { "user-agent": UA }, signal: AbortSignal.timeout(120_000) });
      if ([429, 500, 502, 503, 504].includes(res.status) && attempt < 3) {
        await new Promise((r) => setTimeout(r, 4000 * (attempt + 1)));
        continue;
      }
      check(res.ok, `${url} answered HTTP ${res.status}`);
      return Buffer.from(await res.arrayBuffer());
    } catch (err) {
      if (attempt < 3) {
        await new Promise((r) => setTimeout(r, 4000 * (attempt + 1)));
        continue;
      }
      fail(`${url}: ${err.cause?.code ?? err.message}`);
    }
  }
}

/** The raw source files a published version was built from, checked against the hashes in its document. */
function rawFiles(ds) {
  const out = {};
  for (const entry of ds.doc.source.raw) {
    check(entry.key, `${ds.doc.dataset}: raw ${entry.name} was not archived, nothing to verify against`);
    const body = readFileSync(join(ROOT, ".data", "r2", entry.key));
    check(sha256(body) === entry.sha256, `${ds.doc.dataset}: archived raw ${entry.name} does not match its recorded SHA-256`);
    out[entry.name] = body;
  }
  return out;
}

/** Every row of a table dataset, read from its partition files (which must not overlap). */
function tableRows(ds) {
  const rows = [];
  for (const path of ds.paths().filter((p) => /^table\/.+\.json$/.test(p) && p !== "table/all.json")) rows.push(...ds.json(path).rows);
  return rows;
}

function arcgisFeatures(raw, prefix) {
  const seen = new Map();
  for (const name of Object.keys(raw).filter((n) => n.startsWith(prefix)).sort()) {
    const page = JSON.parse(raw[name].toString("utf8"));
    check(!page.error && Array.isArray(page.features), `${name}: not a feature page`);
    for (const f of page.features) {
      const a = f.attributes ?? f.properties;
      if (!seen.has(a.OBJECTID)) seen.set(a.OBJECTID, f);
    }
  }
  return [...seen.values()];
}

// --- zip, shapefile and dBASE readers ---------------------------------------------------------------

function unzip(buf) {
  let eocd = buf.length - 22;
  while (eocd >= 0 && buf.readUInt32LE(eocd) !== 0x06054b50) eocd--;
  check(eocd >= 0, "zip: no end of central directory");
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const files = {};
  for (let i = 0; i < count; i++) {
    check(buf.readUInt32LE(p) === 0x02014b50, "zip: bad central directory entry");
    const method = buf.readUInt16LE(p + 10);
    const size = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.subarray(p + 46, p + 46 + nameLen).toString("utf8");
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const data = buf.subarray(start, start + size);
    files[name.split("/").pop()] = method === 8 ? inflateRawSync(data) : Buffer.from(data);
    p += 46 + nameLen + extraLen + commentLen;
  }
  return files;
}

function dbfRecords(buf) {
  const n = buf.readUInt32LE(4);
  const headerLen = buf.readUInt16LE(8);
  const recordLen = buf.readUInt16LE(10);
  const fields = [];
  for (let off = 32, pos = 1; buf[off] !== 0x0d; off += 32) {
    const name = buf.subarray(off, off + 11).toString("latin1").replace(/\0.*$/, "");
    const len = buf[off + 16];
    fields.push({ name, pos, len });
    pos += len;
  }
  const out = [];
  for (let i = 0; i < n; i++) {
    const base = headerLen + i * recordLen;
    if (buf[base] === 0x2a) continue; // deleted
    const rec = {};
    for (const f of fields) rec[f.name] = buf.subarray(base + f.pos, base + f.pos + f.len).toString("latin1").trim();
    out.push(rec);
  }
  return out;
}

/** First point of each polygon record of a .shp file. */
function shpFirstPoints(buf) {
  const points = [];
  for (let p = 100; p < buf.length; ) {
    const contentBytes = buf.readUInt32BE(p + 4) * 2;
    const type = buf.readInt32LE(p + 8);
    if (type === 5) {
      const numParts = buf.readInt32LE(p + 8 + 36);
      const first = p + 8 + 44 + 4 * numParts;
      points.push([buf.readDoubleLE(first), buf.readDoubleLE(first + 8)]);
    }
    p += 8 + contentBytes;
  }
  return points;
}

// --- geometry ------------------------------------------------------------------------------------------

const R = 6371.0088;
const rad = (d) => (d * Math.PI) / 180;
const xyz = (lat, lon) => [R * Math.cos(rad(lat)) * Math.cos(rad(lon)), R * Math.cos(rad(lat)) * Math.sin(rad(lon)), R * Math.sin(rad(lat))];
const haversine = (lat1, lon1, lat2, lon2) => {
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
};
function inRings(rings, x, y) {
  let inside = false;
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}
const ringsOf = (g) => (!g ? [] : g.type === "Polygon" ? g.coordinates : g.type === "MultiPolygon" ? g.coordinates.flat() : []);

// --- active-fires ----------------------------------------------------------------------------------

const SAT_NAMES = { N: "S-NPP", N20: "NOAA-20", N21: "NOAA-21" };
const SAT_RANK = { N: 0, N20: 1, N21: 2 };

function firmsDetections(raw) {
  const byKey = new Map();
  for (const name of Object.keys(raw).filter((n) => n.startsWith("firms-")).sort()) {
    const [header, ...lines] = raw[name].toString("utf8").trim().split(/\r?\n/);
    const cols = header.split(",");
    const at = (c) => cols.indexOf(c);
    for (const line of lines) {
      if (!line.trim()) continue;
      const v = line.split(",");
      const sat = v[at("satellite")];
      const time = v[at("acq_time")].padStart(4, "0");
      const iso = `${v[at("acq_date")]}T${time.slice(0, 2)}:${time.slice(2)}:00Z`;
      const lat = Number(v[at("latitude")]);
      const lon = Number(v[at("longitude")]);
      const key = `${sat}|${iso}|${lat}|${lon}`;
      const frp = Number(v[at("frp")]);
      const prev = byKey.get(key);
      if (!prev || frp < prev.frp) byKey.set(key, { sat, iso, minute: Date.parse(iso) / 60000, lat, lon, frp, conf: v[at("confidence")].trim().toLowerCase() });
    }
  }
  const all = [...byKey.values()];
  const newest = maxOf(all, (d) => d.minute);
  return all.filter((d) => d.minute > newest - 7 * 24 * 60).map((d) => ({ ...d, p: xyz(d.lat, d.lon) }));
}

function markKept(dets) {
  const cells = new Map();
  for (const d of dets) {
    const k = d.p.map((c) => Math.floor(c / 0.5)).join(",");
    if (!cells.has(k)) cells.set(k, []);
    cells.get(k).push(d);
  }
  for (const members of cells.values()) {
    members.sort((a, b) => a.minute - b.minute || SAT_RANK[a.sat] - SAT_RANK[b.sat] || a.lat - b.lat || a.lon - b.lon);
    let seq = [];
    const flush = () => {
      if (!seq.length) return;
      const counts = {};
      for (const d of seq) counts[d.sat] = (counts[d.sat] ?? 0) + 1;
      const best = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || SAT_RANK[a] - SAT_RANK[b])[0];
      for (const d of seq) d.kept = d.sat === best;
      seq = [];
    };
    let anchor = null;
    for (const d of members) {
      if (anchor === null || d.minute - anchor > 75) {
        flush();
        anchor = d.minute;
      }
      seq.push(d);
    }
    flush();
  }
}

/** Fires: detections in the same or adjacent 1 km cells at most 48 h apart, searched breadth-first. */
function clusters(dets) {
  const cells = new Map();
  for (const d of dets) {
    d.cell = d.p.map((c) => Math.floor(c / 1.0));
    const k = d.cell.join(",");
    if (!cells.has(k)) cells.set(k, []);
    cells.get(k).push(d);
  }
  for (const list of cells.values()) list.sort((a, b) => a.minute - b.minute);
  const lowerBound = (list, minute) => {
    let lo = 0;
    let hi = list.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (list[mid].minute < minute) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  const out = [];
  for (const seed of dets) {
    if (seed.group !== undefined) continue;
    const group = [];
    seed.group = out.length;
    const queue = [seed];
    while (queue.length) {
      const d = queue.pop();
      group.push(d);
      for (let i = -1; i <= 1; i++)
        for (let j = -1; j <= 1; j++)
          for (let k = -1; k <= 1; k++) {
            const list = cells.get(`${d.cell[0] + i},${d.cell[1] + j},${d.cell[2] + k}`);
            if (!list) continue;
            for (let n = lowerBound(list, d.minute - 2880); n < list.length && list[n].minute <= d.minute + 2880; n++) {
              if (list[n].group === undefined) {
                list[n].group = out.length;
                queue.push(list[n]);
              }
            }
          }
    }
    out.push(group);
  }
  return out;
}

function summarize(group) {
  const first = [...group].sort((a, b) => a.minute - b.minute || SAT_RANK[a.sat] - SAT_RANK[b.sat] || a.lat - b.lat || a.lon - b.lon)[0];
  const id = "vf-" + sha256(`${first.sat}|${first.iso}|${first.lat.toFixed(5)}|${first.lon.toFixed(5)}`).slice(0, 16);
  const kept = group.filter((d) => d.kept);
  const s = kept.reduce((acc, d) => [acc[0] + d.p[0], acc[1] + d.p[1], acc[2] + d.p[2]], [0, 0, 0]);
  return {
    id,
    group,
    raw_detections: group.length,
    detections: kept.length,
    low: group.filter((d) => d.conf === "low").length,
    first: group.reduce((m, d) => (d.iso < m ? d.iso : m), group[0].iso),
    last: group.reduce((m, d) => (d.iso > m ? d.iso : m), group[0].iso),
    max_frp: maxOf(group, (d) => d.frp),
    min_lat: minOf(group, (d) => d.lat),
    max_lat: maxOf(group, (d) => d.lat),
    days: new Set(group.map((d) => d.iso.slice(0, 10))).size,
    satellites: [...new Set(group.map((d) => d.sat))].sort().map((c) => SAT_NAMES[c]).join(","),
    lat: (Math.atan2(s[2], Math.hypot(s[0], s[1])) * 180) / Math.PI,
    lon: (Math.atan2(s[1], s[0]) * 180) / Math.PI,
  };
}

async function verifyActiveFires() {
  const ds = localDataset(SITE, "active-fires");
  const raw = rawFiles(ds);
  const rows = tableRows(ds);
  check(rows.length >= 25, `active-fires: only ${rows.length} events`);
  check(new Set(rows.map((r) => r.event_id)).size === rows.length, "active-fires: duplicate event ids");
  for (const r of rows) check(r.country === "usa" ? /^us-[a-z]{2}$/.test(r.region) : r.country === "can" && /^ca-[a-z]{2}$/.test(r.region), `active-fires: bad region ${r.country}/${r.region}`);

  const dets = firmsDetections(raw);
  markKept(dets);
  const events = new Map(clusters(dets).map(summarize).map((e) => [e.id, e]));
  check(events.size >= rows.length, `active-fires: ${rows.length} published events but only ${events.size} clusters in the raw FIRMS files`);

  // Every published event, recomputed from the FIRMS rows.
  let rawTotal = 0;
  for (const r of rows) {
    const e = events.get(r.event_id);
    check(e, `active-fires: event ${r.event_id} (${r.region}, first ${r.first_detection}) has no matching cluster in the raw FIRMS files`);
    const where = `active-fires ${r.event_id} (${r.region_name})`;
    check(r.raw_detections === e.raw_detections, `${where}: raw_detections ${r.raw_detections}, recomputed ${e.raw_detections}`);
    check(r.detections === e.detections, `${where}: detections ${r.detections}, recomputed ${e.detections}`);
    check(r.low_confidence_detections === e.low, `${where}: low_confidence_detections ${r.low_confidence_detections}, recomputed ${e.low}`);
    check(r.first_detection === e.first && r.last_detection === e.last, `${where}: span ${r.first_detection}..${r.last_detection}, recomputed ${e.first}..${e.last}`);
    check(r.max_frp === e.max_frp, `${where}: max_frp ${r.max_frp}, recomputed ${e.max_frp}`);
    check(r.min_latitude === e.min_lat && r.max_latitude === e.max_lat, `${where}: latitude range differs`);
    check(r.days_detected === e.days, `${where}: days_detected ${r.days_detected}, recomputed ${e.days}`);
    check(r.satellites === e.satellites, `${where}: satellites ${r.satellites}, recomputed ${e.satellites}`);
    check(close(r.latitude, e.lat, 2e-5) && close(r.longitude, e.lon, 2e-5), `${where}: centroid ${r.latitude},${r.longitude} vs ${e.lat},${e.lon}`);
    rawTotal += r.raw_detections;
  }
  check(rawTotal <= dets.length, "active-fires: more detections published than the FIRMS files hold");

  // The biggest event, as the headline fact (with the negative control's corruption).
  const biggest = [...rows].sort((a, b) => b.raw_detections - a.raw_detections || a.event_id.localeCompare(b.event_id))[0];
  const expected = events.get(biggest.event_id).raw_detections + (NEGATIVE ? 1 : 0);
  check(biggest.raw_detections === expected, `active-fires: biggest event ${biggest.event_id} has ${biggest.raw_detections} detections, the FIRMS files give ${expected}`);
  facts.push(`active-fires ${rows.length} events, ${rawTotal}/${dets.length} detections; biggest ${biggest.region} ${biggest.raw_detections} (max FRP ${biggest.max_frp} MW)`);

  // Incident links against the WFIGS pages of the same run.
  const incidents = new Map(arcgisFeatures(raw, "incidents-page-").map((f) => [irwin(f.attributes.IrwinID), f]));
  const perimeters = arcgisFeatures(raw, "perimeters-page-");
  const linked = rows.filter((r) => r.incident_irwin_id);
  let checkedLinks = 0;
  for (const r of linked) {
    const e = events.get(r.event_id);
    const inc = incidents.get(r.incident_irwin_id);
    if (inc) {
      const a = inc.attributes;
      check(r.incident_name === a.IncidentName, `active-fires ${r.event_id}: incident name ${r.incident_name}, WFIGS says ${a.IncidentName}`);
      check(r.incident_acres === (a.IncidentSize ?? null), `active-fires ${r.event_id}: incident acres ${r.incident_acres}, WFIGS says ${a.IncidentSize}`);
      check(r.incident_percent_contained === (a.PercentContained ?? null), `active-fires ${r.event_id}: containment ${r.incident_percent_contained}, WFIGS says ${a.PercentContained}`);
      check(r.incident_discovered === isoMs(a.FireDiscoveryDateTime), `active-fires ${r.event_id}: discovered ${r.incident_discovered}`);
    }
    if (r.incident_link === "perimeter") {
      const rings = perimeters.filter((f) => irwin(f.properties.poly_IRWINID ?? f.properties.attr_IrwinID) === r.incident_irwin_id).flatMap((f) => [ringsOf(f.geometry)]);
      check(e.group.some((d) => rings.some((rs) => inRings(rs, d.lon, d.lat))), `active-fires ${r.event_id}: linked by perimeter but no detection is inside ${r.incident_name}'s perimeter`);
    } else if (r.incident_link === "origin") {
      const g = inc.geometry;
      const km = minOf(e.group, (d) => haversine(d.lat, d.lon, g.y, g.x));
      check(close(km, r.incident_distance_km, 0.0015), `active-fires ${r.event_id}: origin distance ${r.incident_distance_km} km, recomputed ${km.toFixed(4)}`);
    }
    checkedLinks++;
  }
  check(checkedLinks > 0 || incidents.size === 0, "active-fires: no event is linked to any WFIGS incident");
  facts.push(`${checkedLinks} incident links`);

  // Live and stable for the duration of a fire: the name and discovery time of the biggest linked incident.
  const top = [...linked].filter((r) => incidents.has(r.incident_irwin_id)).sort((a, b) => b.raw_detections - a.raw_detections)[0];
  if (top) await liveIncident(top.incident_irwin_id, { name: top.incident_name, discovered: top.incident_discovered }, "active-fires");
}

async function liveIncident(id, expected, label) {
  const url = `${WFIGS}/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=${encodeURIComponent(`IrwinID='{${id}}'`)}&outFields=IrwinID,IncidentName,FireDiscoveryDateTime&f=json`;
  const page = JSON.parse((await live(url)).toString("utf8"));
  check(!page.error, `live WFIGS query failed: ${JSON.stringify(page.error)}`);
  if (!page.features.length) {
    facts.push(`${label}: ${id} has since left the current layer (fall-off), live check skipped`);
    return;
  }
  const a = page.features[0].attributes;
  check(a.IncidentName === expected.name, `${label}: live WFIGS names ${id} "${a.IncidentName}", published "${expected.name}"`);
  check(isoMs(a.FireDiscoveryDateTime) === expected.discovered, `${label}: live discovery ${isoMs(a.FireDiscoveryDateTime)}, published ${expected.discovered}`);
  facts.push(`${label}: live WFIGS agrees on ${a.IncidentName}`);
}

// --- fire-incidents ----------------------------------------------------------------------------------

async function verifyIncidents() {
  const ds = localDataset(SITE, "fire-incidents");
  const raw = rawFiles(ds);
  const rows = tableRows(ds);
  const features = arcgisFeatures(raw, "page-");
  const byId = new Map();
  for (const f of features) {
    const id = irwin(f.attributes.IrwinID);
    const prev = byId.get(id);
    if (!prev || (f.attributes.ModifiedOnDateTime_dt ?? 0) > (prev.attributes.ModifiedOnDateTime_dt ?? 0)) byId.set(id, f);
  }
  check(rows.length === byId.size, `fire-incidents: ${rows.length} rows, the WFIGS pages hold ${byId.size} incidents`);
  check(ds.json("table/all.json").rows.length === rows.length, "fire-incidents: partitions and all.json disagree");
  const published = new Map(rows.map((r) => [r.irwin_id, r]));
  const compare = (id, extra = 0) => {
    const a = byId.get(id).attributes;
    const r = published.get(id);
    check(r, `fire-incidents: ${id} missing`);
    const acres = a.IncidentSize === null || a.IncidentSize === undefined ? null : a.IncidentSize + extra;
    check(r.acres === acres, `fire-incidents ${a.IncidentName}: acres ${r.acres}, WFIGS says ${acres}`);
    check(r.percent_contained === (a.PercentContained ?? null), `fire-incidents ${a.IncidentName}: contained ${r.percent_contained}%, WFIGS says ${a.PercentContained}`);
    check(r.name === (a.IncidentName ? a.IncidentName.split(/\s+/).filter(Boolean).join(" ") : null), `fire-incidents ${id}: name ${r.name} vs ${a.IncidentName}`);
    check(r.discovered === isoMs(a.FireDiscoveryDateTime), `fire-incidents ${a.IncidentName}: discovered ${r.discovered}`);
    const state = a.POOState && /^US-[A-Z]{2}$/.test(a.POOState) ? a.POOState.toLowerCase() : "unknown";
    check(r.state === state, `fire-incidents ${a.IncidentName}: state ${r.state}, WFIGS ${a.POOState}`);
    const g = byId.get(id).geometry;
    if (g) check(close(r.latitude, g.y, 1e-6) && close(r.longitude, g.x, 1e-6), `fire-incidents ${a.IncidentName}: location differs`);
  };
  const largest = [...byId.keys()].sort((x, y) => (byId.get(y).attributes.IncidentSize ?? -1) - (byId.get(x).attributes.IncidentSize ?? -1) || x.localeCompare(y))[0];
  compare(largest, NEGATIVE ? 1 : 0);
  const sample = [...byId.keys()].sort().filter((_, i) => i % 25 === 0);
  for (const id of sample) compare(id);
  const top = published.get(largest);
  facts.push(`fire-incidents ${rows.length} incidents; largest ${top.name} (${top.state}) ${top.acres} acres, ${top.percent_contained}% contained; ${sample.length} sampled`);
  await liveIncident(largest, { name: top.name, discovered: top.discovered }, "fire-incidents");
}

// --- fire-perimeters ---------------------------------------------------------------------------------

function verifyPerimeters() {
  const ds = localDataset(SITE, "fire-perimeters");
  const raw = rawFiles(ds);
  const features = arcgisFeatures(raw, "page-").filter((f) => f.geometry && ringsOf(f.geometry).length && irwin(f.properties.poly_IRWINID ?? f.properties.attr_IrwinID));
  const current = ds.json("current.geojson");
  check(current.features.length === features.length, `fire-perimeters: ${current.features.length} current perimeters, WFIGS returned ${features.length}`);
  const paths = new Set(ds.paths());
  for (const f of current.features) {
    const path = f.properties.perimeter_path;
    check(paths.has(path), `fire-perimeters: ${path} is not in the version`);
    check(JSON.stringify(ds.json(path).geometry) === JSON.stringify(f.geometry), `fire-perimeters: archived ${path} differs from the current shape`);
  }
  const largest = [...features].sort((a, b) => (b.properties.poly_GISAcres ?? 0) - (a.properties.poly_GISAcres ?? 0))[0];
  const id = irwin(largest.properties.poly_IRWINID ?? largest.properties.attr_IrwinID);
  const out = current.features.find((f) => f.properties.irwin_id === id);
  check(out, `fire-perimeters: largest perimeter ${id} not published`);
  const acres = Math.round(largest.properties.poly_GISAcres * 100) / 100 + (NEGATIVE ? 1 : 0);
  check(out.properties.gis_acres === acres, `fire-perimeters ${out.properties.incident_name}: ${out.properties.gis_acres} acres, WFIGS says ${acres}`);
  const srcRing = ringsOf(largest.geometry)[0].map(([x, y]) => [Number(x.toFixed(5)), Number(y.toFixed(5))]);
  const outRing = ringsOf(out.geometry)[0];
  check(srcRing.length === outRing.length && srcRing.every((p, i) => close(p[0], outRing[i][0], 6e-6) && close(p[1], outRing[i][1], 6e-6)), `fire-perimeters ${out.properties.incident_name}: ring differs from WFIGS`);
  const snapshots = ds.json("snapshots/index.json").snapshots;
  const newest = snapshots.at(-1);
  if (features.length) check(newest && newest.perimeters === features.length && paths.has(newest.path), "fire-perimeters: newest snapshot does not hold the current perimeters");
  const index = ds.json("perimeters/index.json").perimeters;
  for (const e of index) {
    if (e.retained) check(paths.has(e.path), `fire-perimeters: retained ${e.path} is not in the version`);
    else {
      const body = readFileSync(join(ROOT, ".data", "r2", "blobs", "sha256", e.blob.slice(0, 2), e.blob));
      check(sha256(body) === e.blob, `fire-perimeters: archived ${e.path} blob does not match its hash`);
    }
  }
  facts.push(`fire-perimeters ${current.features.length} current; largest ${out.properties.incident_name} ${out.properties.gis_acres} acres; ${snapshots.length} snapshots, ${index.length} shapes archived`);
}

// --- smoke -----------------------------------------------------------------------------------------

async function verifySmoke() {
  const ds = localDataset(SITE, "smoke");
  const raw = rawFiles(ds);
  const index = ds.json("index.json");
  const days = Object.keys(raw).filter((n) => n.startsWith("smoke-")).sort();
  check(index.days.length === days.length && days.length >= 1 && days.length <= 7, `smoke: ${index.days.length} days published, ${days.length} fetched`);
  let total = 0;
  let checkedVertex = false;
  for (const name of days) {
    const stamp = name.slice(6);
    const date = `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6)}`;
    const files = unzip(raw[name]);
    const ext = (e) => files[Object.keys(files).find((f) => f.toLowerCase().endsWith(e))];
    const records = dbfRecords(ext(".dbf"));
    const shx = ext(".shx");
    const shxCount = (shx.readUInt32BE(24) * 2 - 100) / 8;
    check(shxCount === records.length, `smoke ${date}: .shx lists ${shxCount} shapes, .dbf ${records.length}`);
    const entry = index.days.find((d) => d.date === date);
    check(entry, `smoke: ${date} not in index.json`);
    const extra = NEGATIVE && name === days[0] ? 1 : 0;
    check(entry.polygons === records.length + extra, `smoke ${date}: ${entry.polygons} polygons published, the HMS file has ${records.length + extra}`);
    for (const d of ["Light", "Medium", "Heavy"]) {
      const n = records.filter((r) => r.Density === d).length;
      check(entry[d.toLowerCase()] === n, `smoke ${date}: ${entry[d.toLowerCase()]} ${d} polygons, HMS ${n}`);
    }
    const fc = ds.json(`days/${date}.geojson`);
    check(fc.features.length === records.length, `smoke ${date}: GeoJSON has ${fc.features.length} features`);
    const firsts = shpFirstPoints(ext(".shp"));
    if (firsts.length) {
      const [x, y] = firsts[0];
      const ring = fc.features[0].geometry.coordinates[0];
      check(close(ring[0][0], Number(x.toFixed(4)), 1e-9) && close(ring[0][1], Number(y.toFixed(4)), 1e-9), `smoke ${date}: first vertex ${ring[0]} vs HMS ${x},${y}`);
      checkedVertex = true;
    }
    total += records.length;
  }
  check(checkedVertex || total === 0, "smoke: no vertex compared");
  // A day two or more days old is final at NOAA: the live file must still be the one published.
  const oldest = ds.doc.source.raw.find((r) => r.name === days[0]);
  const url = ds.state()?.upstream?.[days[0]]?.url;
  if (url && Date.now() - Date.parse(`${days[0].slice(6, 10)}-${days[0].slice(10, 12)}-${days[0].slice(12)}T00:00:00Z`) > 2 * 86400_000) {
    const body = await live(url);
    if (sha256(body) === oldest.sha256) facts.push(`smoke: live ${days[0]} unchanged`);
    else {
      const n = dbfRecords(unzip(body)[Object.keys(unzip(body)).find((f) => f.endsWith(".dbf"))]).length;
      check(n === index.days[0].polygons, `smoke: live ${days[0]} now has ${n} polygons, published ${index.days[0].polygons}`);
      facts.push(`smoke: live ${days[0]} reissued with the same polygon count`);
    }
  }
  facts.push(`smoke ${days.length} days, ${total} polygons, latest ${index.latest}`);
}

await verifyActiveFires();
await verifyIncidents();
verifyPerimeters();
await verifySmoke();
console.log(`WILDFIRE VERIFIED (${facts.join("; ")})`);
