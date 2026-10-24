// G4/G5: visualizedata/boundaries, as published to the local bucket, matches facts re-derived here from the
// original source files, with readers written independently of the Python pipeline (no pyshp, no shared code):
//   - its own zip reader (central directory + raw inflate), .shp polygon reader and .dbf reader for the
//     Census 2025 cartographic boundary files; its own reading of the Natural Earth GeoJSON;
//   - per layer: the feature count from the source records (Island Area county-equivalents excluded from the
//     county layers, as they have no registry id), checked against the published file and index.json;
//   - Los Angeles County: its id and its bounding box, from the source .shp record and an approximate box;
//   - every US id exists in packages/geo/src/locations.json, every registry county has a boundary, and the
//     country ids follow ISO_A3_EH / ADM0_A3 (ISO_A3 = -99) with ne-* for the ones not in the registry;
//   - every published ring is closed and follows the RFC 7946 right-hand rule.
//   node scripts/verify/visualize.mjs                     prints VISUALIZE VERIFIED ... when everything matches
//   node scripts/verify/visualize.mjs --negative-control  corrupts one expected value, so it must FAIL
// Source files are cached under the OS temp folder and revalidated with conditional GETs.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { inflateRawSync } from "node:zlib";
import { ROOT, check, fail, localDataset } from "./lib.mjs";

const NEGATIVE = process.argv.includes("--negative-control");
const UA = "datasites-verify (+mailto:hello@visualizedata.dev)";
const CACHE = join(tmpdir(), "datasites-verify-visualize");
const CENSUS = "https://www2.census.gov/geo/tiger/GENZ2025/shp/";
const NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/";
const ISLAND_AREAS = new Set(["60", "66", "69", "78"]);

// Los Angeles County's extent (west, south, east, north): San Clemente Island to the Mojave edge.
const EXPECT = { laId: "us-county-06037", laBbox: [-118.95, 32.8, -117.65, 34.82], laTolerance: 0.05 };
if (NEGATIVE) EXPECT.laBbox[0] = -120.5; // corrupted on purpose: Los Angeles does not reach 120.5 W

const LAYERS = [
  { path: "us/states-20m.geojson", raw: "state-20m", url: `${CENSUS}cb_2025_us_state_20m.zip`, kind: "state" },
  { path: "us/counties-20m.geojson", raw: "county-20m", url: `${CENSUS}cb_2025_us_county_20m.zip`, kind: "county" },
  { path: "us/congressional-districts-20m.geojson", raw: "cd119-20m", url: `${CENSUS}cb_2025_us_cd119_20m.zip`, kind: "cd" },
  { path: "us/states-5m.geojson", raw: "state-5m", url: `${CENSUS}cb_2025_us_state_5m.zip`, kind: "state" },
  { path: "us/counties-5m.geojson", raw: "county-5m", url: `${CENSUS}cb_2025_us_county_5m.zip`, kind: "county" },
  { path: "us/congressional-districts-5m.geojson", raw: "cd119-5m", url: `${CENSUS}cb_2025_us_cd119_5m.zip`, kind: "cd" },
  { path: "world/countries-110m.geojson", raw: "ne-countries-110m", url: `${NE}ne_110m_admin_0_countries.geojson`, kind: "country" },
  { path: "world/countries-50m.geojson", raw: "ne-countries-50m", url: `${NE}ne_50m_admin_0_countries.geojson`, kind: "country" },
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

async function original(url) {
  mkdirSync(CACHE, { recursive: true });
  const path = join(CACHE, sha256(url).slice(0, 32));
  const metaPath = `${path}.json`;
  const headers = { "user-agent": UA };
  let meta = null;
  if (existsSync(path) && existsSync(metaPath)) {
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
    writeFileSync(path, body);
    writeFileSync(metaPath, JSON.stringify({ url, etag: res.headers.get("etag"), lastModified: res.headers.get("last-modified") }));
    return body;
  }
}

// --- zip, dbf, shp ------------------------------------------------------------------------------

function unzip(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65_557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  check(eocd >= 0, "zip: no end-of-central-directory record");
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const files = {};
  for (let n = 0; n < count; n++) {
    check(buf.readUInt32LE(p) === 0x02014b50, "zip: bad central directory entry");
    const method = buf.readUInt16LE(p + 10);
    const compSize = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.subarray(p + 46, p + 46 + nameLen).toString("utf8");
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const data = buf.subarray(start, start + compSize);
    files[name] = method === 0 ? data : method === 8 ? inflateRawSync(data) : fail(`zip: ${name} uses method ${method}`);
    p += 46 + nameLen + extraLen + commentLen;
  }
  return files;
}

function readDbf(buf) {
  const count = buf.readUInt32LE(4);
  const headerLen = buf.readUInt16LE(8);
  const recordLen = buf.readUInt16LE(10);
  const fields = [];
  for (let p = 32; buf[p] !== 0x0d; p += 32) {
    fields.push({ name: buf.subarray(p, p + 11).toString("latin1").replace(/\0.*$/, ""), len: buf[p + 16] });
  }
  const rows = [];
  for (let r = 0; r < count; r++) {
    let p = headerLen + r * recordLen + 1; // skip the deletion flag
    const row = {};
    for (const f of fields) {
      row[f.name] = buf.subarray(p, p + f.len).toString("utf8").trim();
      p += f.len;
    }
    rows.push(row);
  }
  return rows;
}

/** Each record's bounding box [xmin, ymin, xmax, ymax] from a polygon .shp (the record header carries it). */
function readShpBoxes(buf) {
  const boxes = [];
  for (let p = 100; p < buf.length; ) {
    const words = buf.readInt32BE(p + 4);
    const type = buf.readInt32LE(p + 8);
    check(type === 5, `shp: record of type ${type}, expected polygon`);
    boxes.push([0, 1, 2, 3].map((k) => buf.readDoubleLE(p + 12 + 8 * k)));
    p += 8 + words * 2;
  }
  return boxes;
}

function census(buf) {
  const files = unzip(buf);
  const member = (ext) => files[Object.keys(files).find((n) => n.endsWith(`.${ext}`))];
  const rows = readDbf(member("dbf"));
  const boxes = readShpBoxes(member("shp"));
  check(rows.length === boxes.length, "shp and dbf record counts differ");
  return rows.map((row, i) => ({ ...row, bbox: boxes[i] }));
}

// --- published geometry -------------------------------------------------------------------------

const polygonsOf = (f) => (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates);
function shoelace(ring) {
  let s = 0;
  for (let i = 0; i < ring.length - 1; i++) s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  return s / 2;
}
function bboxOf(f) {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  for (const poly of polygonsOf(f)) for (const [x, y] of poly[0]) {
    b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y);
  }
  return b;
}

// --- checks ---------------------------------------------------------------------------------------

const ds = localDataset("visualizedata", "boundaries");
const index = ds.json("index.json");
const registry = new Map(JSON.parse(readFileSync(join(ROOT, "packages", "geo", "src", "locations.json"), "utf8")).locations.map((l) => [l.id, l]));
const stateByFips = new Map([...registry.values()].filter((l) => l.level === "us-state").map((l) => [l.fips, l]));
const registryCounties = [...registry.values()].filter((l) => l.level === "us-county").map((l) => l.id);
const facts = [];
let total = 0;

for (const layer of LAYERS) {
  const body = await original(layer.url);
  const raw = (ds.doc.source.raw ?? []).find((r) => r.name === layer.raw);
  check(raw, `version ${ds.doc.version} lists no raw input ${layer.raw}`);
  check(sha256(body) === raw.sha256, `${layer.url} changed after version ${ds.doc.version} was built; re-run the pipeline and verify again`);
  const published = JSON.parse(ds.text(layer.path));
  check(published.type === "FeatureCollection", `${layer.path} is not a FeatureCollection`);
  const feats = published.features;
  const ids = new Set(feats.map((f) => f.id));
  check(ids.size === feats.length, `${layer.path}: duplicate feature ids`);
  for (const f of feats) {
    check(f.type === "Feature" && f.properties?.id === f.id, `${layer.path}: ${f.id} lacks a matching properties.id`);
    check(["Polygon", "MultiPolygon"].includes(f.geometry?.type), `${layer.path}: ${f.id} is a ${f.geometry?.type}`);
    for (const poly of polygonsOf(f)) {
      poly.forEach((ring, k) => {
        check(ring.length >= 4, `${layer.path}: ${f.id} has a ring of ${ring.length} positions`);
        const [a, z] = [ring[0], ring[ring.length - 1]];
        check(a[0] === z[0] && a[1] === z[1], `${layer.path}: ${f.id} has an unclosed ring`);
        const area = shoelace(ring);
        check(k === 0 ? area > 0 : area < 0, `${layer.path}: ${f.id} ring ${k} breaks the right-hand rule (area ${area})`);
        for (const [x, y] of ring) {
          check(Math.abs(x) <= 180 && Math.abs(y) <= 90, `${layer.path}: ${f.id} has position ${x},${y}`);
          check(Math.round(x * 1e5) / 1e5 === x && Math.round(y * 1e5) / 1e5 === y, `${layer.path}: ${f.id} has more than 5 decimals`);
        }
      });
    }
  }

  let expectedIds;
  if (layer.kind === "country") {
    const src = JSON.parse(body.toString("utf8")).features;
    expectedIds = src.map((f) => {
      const p = f.properties;
      const code = (p.ISO_A3 === "-99" ? p.ADM0_A3 : p.ISO_A3_EH).toLowerCase();
      return registry.get(code)?.level === "country" ? code : `ne-${p.ADM0_A3.toLowerCase()}`;
    });
    for (const id of ids) check(id.startsWith("ne-") || registry.get(id)?.level === "country", `${layer.path}: ${id} is not a registry country`);
    for (const must of ["usa", "fra", "nor", "rus", "fji", "ne-kos"]) check(ids.has(must), `${layer.path}: no feature ${must}`);
  } else {
    const rows = census(body);
    if (layer.kind === "state") {
      expectedIds = rows.map((r) => `us-${r.STUSPS.toLowerCase()}`);
      for (const id of ids) check(registry.get(id)?.level === "us-state", `${layer.path}: ${id} is not in locations.json`);
    } else if (layer.kind === "county") {
      expectedIds = rows.filter((r) => !ISLAND_AREAS.has(r.STATEFP)).map((r) => `us-county-${r.STATEFP}${r.COUNTYFP}`);
      for (const id of ids) check(registry.get(id)?.level === "us-county", `${layer.path}: ${id} is not in locations.json`);
      const missing = registryCounties.filter((id) => !ids.has(id));
      check(missing.length === 0, `${layer.path}: registry counties with no boundary: ${missing.slice(0, 5).join(", ")}`);
      const la = rows.find((r) => r.GEOID === "06037");
      const pub = feats.find((f) => f.id === EXPECT.laId);
      check(la && pub, `${layer.path}: Los Angeles County (${EXPECT.laId}) is missing`);
      const got = bboxOf(pub);
      got.forEach((v, k) => {
        check(Math.abs(v - la.bbox[k]) <= 1e-5 + 1e-9, `${layer.path}: LA bbox[${k}] ${v} differs from the source .shp ${la.bbox[k]}`);
        check(Math.abs(v - EXPECT.laBbox[k]) <= EXPECT.laTolerance, `${layer.path}: LA bbox[${k}] ${v}, expected about ${EXPECT.laBbox[k]}`);
      });
      facts.push(`${layer.path.split("/")[1]} LA bbox ${got.map((v) => v.toFixed(2)).join(",")}`);
    } else {
      expectedIds = rows.map((r) => `us-cd-${r.STATEFP}${r.CD119FP}`);
      for (const f of feats) {
        const state = stateByFips.get(f.id.slice(6, 8));
        check(state && f.properties.state === state.id, `${layer.path}: ${f.id} has no registry state`);
      }
    }
  }
  const want = new Set(expectedIds);
  check(want.size === expectedIds.length, `${layer.url}: the source gives duplicate ids under the id rules`);
  check(feats.length === want.size, `${layer.path}: ${feats.length} features, the source gives ${want.size}`);
  for (const id of want) check(ids.has(id), `${layer.path}: no feature ${id}`);
  const entry = index.layers.find((e) => e.path === layer.path);
  check(entry && entry.feature_count === feats.length, `index.json: ${layer.path} feature_count ${entry?.feature_count} vs ${feats.length}`);
  check(entry.license && entry.vintage && entry.caveats?.length, `index.json: ${layer.path} lacks license, vintage or caveats`);
  total += feats.length;
  facts.push(`${layer.path.replace(/\.geojson$/, "")}=${feats.length}`);
}

check(index.layers.length === LAYERS.length, `index.json lists ${index.layers.length} layers, expected ${LAYERS.length}`);
check(index.feature_count === total, `index.json feature_count ${index.feature_count}, layers add up to ${total}`);
const cdEntry = index.layers.find((e) => e.path === "us/congressional-districts-20m.geojson");
check(cdEntry.caveats.some((c) => /redrew/.test(c) && /TX/.test(c)), "index.json: the 2026 redistricting caveat is missing");

console.log(`VISUALIZE VERIFIED (version ${ds.doc.version}; ${total} features; ${facts.join("; ")})`);
