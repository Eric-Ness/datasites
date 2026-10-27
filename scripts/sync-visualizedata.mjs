// Copies what visualizedata.org builds from each dataset into apps/web/src/lib/visualizedata.json, so the sites can
// link every dataset to its charts and give each indicator its own page. visualizedata.org keeps this in its own
// repository and doesn't publish it: src/data/site/sources.json (which pages and indicators use each dataset) and
// src/data/indicators/*.json and src/data/us-indicators/*.json (each indicator's definition and values). Run this
// from a checkout beside this one after visualizedata.org changes, then commit the result:
//   node scripts/sync-visualizedata.mjs [path to the visualizedata.org checkout]
// Per indicator it keeps the definition, one trend line (the national or world series, or the median place) and
// the latest year's highest and lowest places, not every place's values.
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./verify/lib.mjs";

const repo = process.argv[2] ?? join(ROOT, "..", "visualizedata.org");
const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const doc = readJson(join(repo, "src", "data", "site", "sources.json"));
const sites = readJson(join(ROOT, "packages", "registry", "src", "sites.json"));
const known = new Set(readJson(join(ROOT, "packages", "registry", "src", "datasets.json")).map((d) => `${d.site}/${d.id}`));
const siteByDomain = new Map(sites.map((s) => [s.domain, s.id]));

const href = (path) => {
  if (!/^\/[a-z0-9/_-]*$/.test(path)) throw new Error(`unexpected visualizedata.org path ${JSON.stringify(path)}`);
  return path;
};
const idOf = (path) => path.split("/").filter(Boolean).pop();

// --- which pages and indicators use each dataset ------------------------------------------------------

const datasets = {};
const indicatorOwner = new Map(); // indicator id -> { key, href }
for (const { site: domain, datasets: list } of doc.sites) {
  const site = siteByDomain.get(domain);
  if (!site) throw new Error(`sources.json: unknown site ${domain}`);
  for (const d of list) {
    const pages = (d.parts ?? []).map((p) => ({ label: p.label, href: href(p.href) }));
    const indicators = (d.indicators ?? []).map((i) => ({ id: idOf(href(i.href)), label: i.name, href: href(i.href) }));
    if (!pages.length && !indicators.length) continue;
    const key = `${site}/${d.id}`;
    if (!known.has(key)) throw new Error(`sources.json: ${key} is not in the registry`);
    datasets[key] = { pages, indicators };
    for (const i of indicators) indicatorOwner.set(i.id, { key, href: i.href });
  }
}

// --- each indicator: definition, trend and latest year --------------------------------------------------

const round = (v) => (v === null || v === undefined ? null : Math.round(v * 1e6) / 1e6);
const series = (pairs) => (pairs ?? []).filter(([, v]) => v !== null && Number.isFinite(v)).map(([y, v]) => [y, round(v)]);

function medianTrend(places) {
  const byYear = new Map();
  for (const pairs of Object.values(places)) for (const [y, v] of series(pairs)) (byYear.get(y) ?? byYear.set(y, []).get(y)).push(v);
  const most = Math.max(0, ...[...byYear.values()].map((v) => v.length));
  const enough = Math.max(5, Math.ceil(most / 4));
  return [...byYear.entries()]
    .filter(([, v]) => v.length >= enough)
    .sort(([a], [b]) => a - b)
    .map(([y, v]) => {
      const s = v.sort((a, b) => a - b);
      const m = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
      return [y, round(m)];
    });
}

/** The latest year most places have a value for, and its five highest and lowest places. */
function latest(places, names, levelLabel) {
  const counts = new Map();
  for (const pairs of Object.values(places)) for (const [y] of series(pairs)) counts.set(y, (counts.get(y) ?? 0) + 1);
  if (!counts.size) return null;
  const most = Math.max(...counts.values());
  const year = Math.max(...[...counts.entries()].filter(([, n]) => n >= most / 2).map(([y]) => y));
  const values = Object.entries(places)
    .map(([id, pairs]) => [id, series(pairs).find(([y]) => y === year)?.[1]])
    .filter(([, v]) => v !== undefined)
    .map(([id, v]) => ({ id, name: names[id] ?? id, value: v }));
  const sorted = [...values].sort((a, b) => b.value - a.value || a.name.localeCompare(b.name));
  const ties = (v) => values.filter((x) => x.value === v).length;
  return {
    year,
    level: levelLabel,
    count: values.length,
    highest: sorted.slice(0, 5),
    lowest: sorted.slice(-5).reverse(),
    // More than five places can share the extreme value (100% of days with good air); the page says how many.
    highestTies: ties(sorted[0].value),
    lowestTies: ties(sorted.at(-1).value),
  };
}

const indicators = {};
for (const [dir, scope] of [["indicators", "world"], ["us-indicators", "us"]]) {
  for (const file of readdirSync(join(repo, "src", "data", dir)).filter((f) => f.endsWith(".json")).sort()) {
    const j = readJson(join(repo, "src", "data", dir, file));
    const owner = indicatorOwner.get(j.id);
    if (!owner) continue; // not built from a .dev dataset (World Bank WDI and the like)
    const [site, dataset] = owner.key.split("/");
    let trend;
    let summary;
    if (scope === "us") {
      const level = Object.keys(j.levels).find((l) => l === "us-county" && Object.keys(j.levels[l].places).length) ?? "us-state";
      const { places, names } = j.levels[level];
      const unit = level === "us-county" ? "county" : "state";
      trend = series(j.national).length > 1 ? { label: "United States", points: series(j.national) } : { label: `Median ${unit}`, points: medianTrend(places) };
      summary = latest(places, names, unit);
    } else {
      trend = series(j.world).length > 1 ? { label: "World", points: series(j.world) } : { label: "Median country", points: medianTrend(j.places) };
      summary = latest(j.places, j.names, "country");
    }
    indicators[j.id] = {
      id: j.id,
      site,
      dataset,
      name: j.name,
      unit: j.unit,
      decimals: j.decimals ?? 1,
      format: j.format ?? null,
      better: j.better ?? null,
      scope,
      href: owner.href,
      method: j.source?.code ?? null,
      note: j.source?.note ?? null,
      trend,
      latest: summary,
    };
  }
}
const missing = [...indicatorOwner.keys()].filter((id) => !indicators[id]);
if (missing.length) throw new Error(`no indicator file for ${missing.join(", ")}`);

const out = {
  note: "Generated by scripts/sync-visualizedata.mjs from the visualizedata.org repository. Do not edit by hand.",
  generated_at: doc.generated_at,
  datasets: Object.fromEntries(Object.entries(datasets).sort(([a], [b]) => a.localeCompare(b))),
  indicators: Object.fromEntries(Object.entries(indicators).sort(([a], [b]) => a.localeCompare(b))),
};
const target = join(ROOT, "apps", "web", "src", "lib", "visualizedata.json");
writeFileSync(target, `${JSON.stringify(out, null, 1)}\n`);
console.log(
  `wrote ${target}: ${Object.keys(datasets).length} datasets, ${Object.keys(indicators).length} indicators (visualizedata.org data of ${doc.generated_at})`,
);
