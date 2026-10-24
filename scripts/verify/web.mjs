// W2: checks every built website in apps/web/dist/, independently of the web build code.
//   node scripts/verify/web.mjs
//   node scripts/verify/web.mjs --registry apps/web/test/fixtures/registry --dist apps/web/.fixtures-dist --no-wrangler
// For each registry site: the required pages and one page per registry dataset exist; every internal
// href and src resolves to a built file (and #fragments to an id); every page has <html lang>, a
// <title>, a meta description, one <h1> and a canonical URL on the site's domain; dataset pages carry
// schema.org Dataset JSON-LD; sitemap.xml lists every page; no external scripts, stylesheets or fonts;
// and apps/web/wrangler/<id>.jsonc is an assets-only Worker on the apex domain.
// Prints "WEB OK ..." only after every assertion passes.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { ROOT, check, fail } from "./lib.mjs";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const i = args.indexOf(name);
  return i === -1 ? fallback : resolve(ROOT, args[i + 1] ?? fail(`${name} needs a value`));
};
const registryDir = option("--registry", join(ROOT, "packages", "registry", "src"));
const distDir = option("--dist", join(ROOT, "apps", "web", "dist"));
const checkWrangler = !args.includes("--no-wrangler");
const wranglerDir = join(ROOT, "apps", "web", "wrangler");

const readJson = (file) => JSON.parse(readFileSync(file, "utf8"));
const sites = readJson(join(registryDir, "sites.json"));
const datasets = readJson(join(registryDir, "datasets.json"));
check(Array.isArray(sites) && sites.length > 0, `no sites in ${registryDir}/sites.json`);

const REQUIRED = [
  "index.html",
  "datasets/index.html",
  "docs/index.html",
  "docs/reference/index.html",
  "docs/errors/index.html",
  "status/index.html",
  "sources/index.html",
  "privacy/index.html",
  "404.html",
  "sitemap.xml",
  "robots.txt",
  "favicon.svg",
];
const EXTRA = { visualizedata: ["gallery/index.html"] };

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else yield path;
  }
}

const ENTITIES = { "&amp;": "&", "&quot;": '"', "&#39;": "'", "&lt;": "<", "&gt;": ">" };
const decode = (s) => s.replace(/&(amp|quot|#39|lt|gt);/g, (m) => ENTITIES[m]);

/** Attribute values of every tag named in `tags`. */
function attributes(html, tags) {
  const out = [];
  const tagRe = new RegExp(`<(${tags.join("|")})\\b([^>]*)>`, "gi");
  for (const tag of html.matchAll(tagRe)) {
    const attrs = {};
    for (const a of tag[2].matchAll(/([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/g)) {
      attrs[a[1].toLowerCase()] = decode(a[2] ?? a[3] ?? a[4] ?? "");
    }
    out.push({ tag: tag[1].toLowerCase(), attrs, raw: tag[0] });
  }
  return out;
}

function idsOf(html) {
  return new Set([...html.matchAll(/\sid\s*=\s*"([^"]+)"/g)].map((m) => decode(m[1])));
}

/** Strips // and /* comments outside strings, then trailing commas, so JSONC parses as JSON. */
function parseJsonc(text) {
  let out = "";
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    const next = text[i + 1];
    if (inString) {
      out += c;
      if (c === "\\") out += text[++i] ?? "";
      else if (c === '"') inString = false;
    } else if (c === '"') {
      inString = true;
      out += c;
    } else if (c === "/" && next === "/") {
      while (i < text.length && text[i] !== "\n") i++;
      out += "\n";
    } else if (c === "/" && next === "*") {
      i += 2;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) i++;
      i++;
    } else out += c;
  }
  return JSON.parse(out.replace(/,(\s*[}\]])/g, "$1"));
}

let pageCount = 0;
let linkCount = 0;
let datasetPageCount = 0;

for (const site of sites) {
  const dir = join(distDir, site.id);
  check(existsSync(dir) && statSync(dir).isDirectory(), `${site.id}: ${dir} does not exist (run pnpm --filter @datasites/web run build:all)`);
  const own = datasets.filter((d) => d.site === site.id);
  const required = [...REQUIRED, ...(EXTRA[site.id] ?? []), ...own.map((d) => `datasets/${d.id}/index.html`)];
  for (const file of required) check(existsSync(join(dir, file)), `${site.id}: missing ${file}`);

  const files = [...walk(dir)].map((f) => relative(dir, f).split(sep).join("/"));
  const htmlFiles = files.filter((f) => f.endsWith(".html"));
  const cache = new Map();
  const html = (file) => {
    if (!cache.has(file)) cache.set(file, readFileSync(join(dir, file), "utf8"));
    return cache.get(file);
  };
  const origin = `https://${site.domain}`;

  /** Maps a site path ("/docs/", "/favicon.svg") to the built file that serves it, or null. */
  const fileFor = (path) => {
    let p = decodeURIComponent(path).replace(/^\/+/, "");
    if (p === "" || p.endsWith("/")) p += "index.html";
    if (files.includes(p)) return p;
    if (files.includes(`${p}/index.html`)) return `${p}/index.html`;
    if (files.includes(`${p}.html`)) return `${p}.html`;
    return null;
  };

  for (const file of htmlFiles) {
    const page = html(file);
    const where = `${site.id}/${file}`;
    check(/<html\b[^>]*\blang="[a-z]{2}(-[A-Za-z]+)?"/i.test(page), `${where}: <html> has no lang`);
    check(/<title>[^<]+<\/title>/i.test(page), `${where}: empty or missing <title>`);
    const metas = attributes(page, ["meta"]);
    const description = metas.find((m) => m.attrs.name === "description");
    check(description && description.attrs.content?.trim(), `${where}: no meta description`);
    const links = attributes(page, ["link"]);
    const canonical = links.filter((l) => (l.attrs.rel ?? "").split(/\s+/).includes("canonical"));
    check(canonical.length === 1, `${where}: expected one canonical link, found ${canonical.length}`);
    check(canonical[0].attrs.href.startsWith(`${origin}/`), `${where}: canonical ${canonical[0].attrs.href} is not on ${origin}`);
    const h1s = (page.match(/<h1\b/gi) ?? []).length;
    check(h1s === 1, `${where}: expected one <h1>, found ${h1s}`);

    for (const s of attributes(page, ["script"])) {
      if (s.attrs.src === undefined) continue;
      check(!/^(https?:)?\/\//i.test(s.attrs.src), `${where}: external script ${s.attrs.src}`);
    }
    for (const l of links) {
      const rel = (l.attrs.rel ?? "").toLowerCase();
      if (/stylesheet|preload|modulepreload|preconnect/.test(rel) && /^(https?:)?\/\//i.test(l.attrs.href ?? "")) {
        check(l.attrs.href.startsWith(`${origin}/`), `${where}: external ${rel} ${l.attrs.href}`);
      }
    }

    const refs = attributes(page, ["a", "link", "script", "img", "source", "iframe", "use", "form"]);
    for (const ref of refs) {
      for (const key of ["href", "src", "action"]) {
        let value = ref.attrs[key];
        if (value === undefined) continue;
        check(!/^javascript:/i.test(value), `${where}: javascript: URL`);
        if (/^(mailto|tel|data):/i.test(value)) continue;
        if (/^https?:\/\//i.test(value) || value.startsWith("//")) {
          const url = new URL(value, origin);
          if (url.origin !== origin) continue; // external (sources, the API, sibling sites)
          value = url.pathname + url.hash;
        }
        linkCount++;
        const base = new URL(`/${file.replace(/index\.html$/, "")}`, origin);
        const url = new URL(value, base);
        if (value.startsWith("#")) {
          const frag = decodeURIComponent(value.slice(1));
          if (frag) check(idsOf(page).has(frag), `${where}: link to missing #${frag}`);
          continue;
        }
        const target = fileFor(url.pathname);
        check(target, `${where}: ${key}="${ref.attrs[key]}" does not resolve to a built file`);
        const frag = decodeURIComponent(url.hash.slice(1));
        if (frag && target.endsWith(".html")) {
          check(idsOf(html(target)).has(frag), `${where}: ${ref.attrs[key]} points to a missing #${frag} in ${target}`);
        }
      }
    }
    pageCount++;
  }

  for (const ds of own) {
    const page = html(`datasets/${ds.id}/index.html`);
    const blocks = [...page.matchAll(/<script type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)];
    check(blocks.length === 1, `${site.id}/datasets/${ds.id}: expected one JSON-LD block`);
    let ld;
    try {
      ld = JSON.parse(blocks[0][1]);
    } catch (err) {
      fail(`${site.id}/datasets/${ds.id}: JSON-LD does not parse: ${err.message}`);
    }
    check(ld["@type"] === "Dataset" && ld["@context"] === "https://schema.org", `${site.id}/${ds.id}: JSON-LD is not a schema.org Dataset`);
    check(ld.name === ds.title && typeof ld.description === "string" && ld.description.length > 0, `${site.id}/${ds.id}: JSON-LD name/description`);
    check(ld.license && (typeof ld.license === "string" || Array.isArray(ld.license)), `${site.id}/${ds.id}: JSON-LD has no license`);
    check(ld.creator?.name || ld.publisher?.name, `${site.id}/${ds.id}: JSON-LD has no creator or publisher`);
    check(Array.isArray(ld.distribution) && ld.distribution.length > 0 && ld.distribution.every((d) => /^https:\/\//.test(d.contentUrl)), `${site.id}/${ds.id}: JSON-LD distribution`);
    const text = page.replace(/&(#\d+|#x[0-9a-f]+|amp|lt|gt|quot);/gi, (m, code) =>
      code[0] === "#" ? String.fromCodePoint(Number.parseInt(code.slice(code[1] === "x" ? 2 : 1), code[1] === "x" ? 16 : 10)) : ENTITIES[m.toLowerCase()],
    );
    check(text.includes(ds.source.attribution), `${site.id}/datasets/${ds.id}: the exact attribution text is not on the page`);
    check(text.includes(ds.source.license) || text.includes(ds.source.name), `${site.id}/datasets/${ds.id}: source or license missing`);
    datasetPageCount++;
  }

  const sitemap = readFileSync(join(dir, "sitemap.xml"), "utf8");
  check(sitemap.includes('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'), `${site.id}: sitemap.xml has no sitemap namespace`);
  const locs = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => decode(m[1]));
  for (const loc of locs) {
    check(loc.startsWith(`${origin}/`), `${site.id}: sitemap entry ${loc} is not on ${origin}`);
    check(fileFor(new URL(loc).pathname), `${site.id}: sitemap entry ${loc} has no built page`);
  }
  const listed = new Set(locs.map((loc) => fileFor(new URL(loc).pathname)));
  for (const file of htmlFiles) {
    if (file === "404.html") continue;
    check(listed.has(file), `${site.id}: sitemap.xml does not list ${file}`);
  }
  check(readFileSync(join(dir, "robots.txt"), "utf8").includes(`Sitemap: ${origin}/sitemap.xml`), `${site.id}: robots.txt does not point to the sitemap`);
  check(/noindex/.test(html("404.html")), `${site.id}: 404.html should be noindex`);

  for (const css of files.filter((f) => f.endsWith(".css"))) {
    const text = readFileSync(join(dir, css), "utf8");
    check(!/@import\s+(url\()?["']?(https?:)?\/\//i.test(text), `${site.id}/${css}: external @import`);
    check(!/url\(\s*["']?(https?:)?\/\//i.test(text), `${site.id}/${css}: external url()`);
  }

  if (checkWrangler) {
    const file = join(wranglerDir, `${site.id}.jsonc`);
    check(existsSync(file), `missing apps/web/wrangler/${site.id}.jsonc`);
    let config;
    try {
      config = parseJsonc(readFileSync(file, "utf8"));
    } catch (err) {
      fail(`apps/web/wrangler/${site.id}.jsonc does not parse: ${err.message}`);
    }
    check(config.name === `web-${site.id}`, `${site.id}.jsonc: name should be web-${site.id}, got ${config.name}`);
    check(config.main === undefined, `${site.id}.jsonc: an assets-only Worker has no main script`);
    check(/^\d{4}-\d{2}-\d{2}$/.test(config.compatibility_date ?? ""), `${site.id}.jsonc: compatibility_date`);
    const assets = resolve(dirname(file), config.assets?.directory ?? "");
    check(config.assets?.directory && assets === resolve(distDir, site.id), `${site.id}.jsonc: assets.directory should point at dist/${site.id}`);
    check(existsSync(join(assets, "index.html")), `${site.id}.jsonc: assets directory ${assets} has no index.html`);
    check(config.assets.not_found_handling === "404-page", `${site.id}.jsonc: not_found_handling should be "404-page"`);
    const routes = config.routes ?? [];
    check(routes.some((r) => r.pattern === site.domain && r.custom_domain === true), `${site.id}.jsonc: no custom-domain route for ${site.domain}`);
  }
}

console.log(
  `WEB OK (${sites.length} sites, ${pageCount} pages, ${datasetPageCount} dataset pages, ${linkCount} internal links${checkWrangler ? `, ${sites.length} wrangler configs` : ""})`,
);
