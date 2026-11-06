// G10/G11: checks the deployed sites. Fails until docs/SETUP.md has been worked through.
//   node scripts/verify/live.mjs dns    all ten domains on Cloudflare nameservers, apex serving HTTPS
//   node scripts/verify/live.mjs prod   every site's home page and api.<domain> status, votingdata.net redirect
//   ... --sites climatedata,electiondata   only these sites (and skip votingdata.net), for a partial launch
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, fail } from "./lib.mjs";

const allSites = JSON.parse(readFileSync(join(ROOT, "packages", "registry", "src", "sites.json"), "utf8"));
const mode = process.argv[2];
const sitesArg = process.argv.indexOf("--sites");
const only = sitesArg === -1 ? null : (process.argv[sitesArg + 1] ?? "").split(",").filter(Boolean);
// By default only launched sites; a coming-soon site (status "coming-soon" in its site.yaml) has no domain yet.
const sites = only ? allSites.filter((s) => only.includes(s.id)) : allSites.filter((s) => s.status === "live");
if (only) check(only.length > 0 && sites.length === only.length, `unknown site in --sites ${only.join(",")}`);
// Statuses that are fine at launch: unpublished means a dataset waits on credentials (BEA, Census, Space-Track).
const HEALTHY = new Set(["ok", "discontinued", "unpublished"]);
const problems = [];

async function nameservers(domain) {
  const res = await fetch(`https://cloudflare-dns.com/dns-query?name=${domain}&type=NS`, {
    headers: { accept: "application/dns-json" },
    signal: AbortSignal.timeout(15_000),
  });
  const body = await res.json();
  return (body.Answer ?? []).filter((a) => a.type === 2).map((a) => a.data.toLowerCase());
}

async function get(url, init = {}) {
  try {
    return await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(20_000), ...init });
  } catch (err) {
    return { status: 0, error: err.cause?.code ?? err.message, text: async () => "", headers: new Headers() };
  }
}

if (mode === "dns") {
  for (const domain of [...sites.map((s) => s.domain), ...(only ? [] : ["votingdata.net"])]) {
    const ns = await nameservers(domain);
    if (!ns.length || !ns.every((n) => n.endsWith(".ns.cloudflare.com."))) {
      problems.push(`${domain}: nameservers ${ns.join(", ") || "(none: not registered or not delegated)"}`);
      continue;
    }
    const res = await get(`https://${domain}/`);
    if (!(res.status >= 200 && res.status < 500)) problems.push(`${domain}: HTTPS ${res.status || res.error}`);
  }
  check(problems.length === 0, `not live yet:\n  ${problems.join("\n  ")}`);
  console.log(`LIVE DNS OK (${sites.length + (only ? 0 : 1)} domains on Cloudflare, HTTPS answering)`);
} else if (mode === "prod") {
  for (const site of sites) {
    const res = await get(`https://${site.domain}/`);
    const body = res.status === 200 ? await res.text() : "";
    if (res.status !== 200 || !body.includes(site.name)) problems.push(`https://${site.domain}/: ${res.status || res.error}`);
  }
  for (const site of sites) {
    const api = await get(`https://api.${site.domain}/v1/status`);
    if (api.status !== 200) {
      problems.push(`api.${site.domain}/v1/status: ${api.status || api.error}`);
      continue;
    }
    const status = await api.json();
    const notOk = status.datasets.filter((d) => !HEALTHY.has(d.status));
    const published = status.datasets.filter((d) => d.status !== "unpublished");
    if (published.length === 0 || notOk.length) {
      problems.push(`api.${site.domain} status: ${published.length} published, not ok: ${notOk.map((d) => `${d.id}=${d.status}`).join(", ") || "none"}`);
    }
  }
  if (!only) {
    const vote = await get("https://votingdata.net/some/path?x=1");
    const location = vote.headers.get("location") ?? "";
    if (vote.status !== 302 || !location.startsWith("https://electiondata.dev/some/path")) {
      problems.push(`votingdata.net: ${vote.status || vote.error} -> ${location || "(no redirect)"}`);
    }
  }
  check(problems.length === 0, `not live yet:\n  ${problems.join("\n  ")}`);
  console.log(`LIVE PROD OK (${sites.length} sites: home page and API status ok${only ? "" : "; votingdata.net redirect"})`);
} else {
  fail("usage: node scripts/verify/live.mjs dns|prod");
}
