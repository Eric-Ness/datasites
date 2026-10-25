// G8: CI uploads preview versions of both Workers on PRs (once deploys are enabled), the gated
// workflows are gated, and every secret/variable the workflows read is documented in docs/SETUP.md.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { ROOT, check } from "./lib.mjs";

const dir = join(ROOT, ".github", "workflows");
const texts = Object.fromEntries(readdirSync(dir).filter((f) => /\.ya?ml$/.test(f)).map((f) => [f, readFileSync(join(dir, f), "utf8")]));
const wf = Object.fromEntries(Object.entries(texts).map(([f, t]) => [f, parse(t)]));
const runText = (job) => (job.steps ?? []).map((s) => s.run ?? "").join("\n");

// CI: tests on every PR, preview uploads once deploys are enabled
const ci = wf["ci.yml"];
check(ci, "ci.yml is missing");
check(ci.on?.pull_request !== undefined, "ci.yml does not run on pull_request");
for (const job of ["workers", "pipelines", "preview"]) check(ci.jobs?.[job], `ci.yml has no '${job}' job`);
const preview = ci.jobs.preview;
check(/pull_request/.test(preview.if) && /DEPLOY_ENABLED/.test(preview.if), "preview job is not gated on pull_request and DEPLOY_ENABLED");
check(/head\.repo\.full_name == github\.repository/.test(preview.if), "preview job would run for fork PRs");
check([preview.needs].flat().includes("workers"), "preview job does not wait for the workers tests");
const previewRun = runText(preview);
check(/wrangler versions upload/.test(previewRun) && /--preview-alias/.test(previewRun), "preview job does not upload preview versions");
check(/for app in api coming-soon/.test(previewRun), "preview job does not cover both Workers");
check(/gh pr comment/.test(previewRun), "preview job does not comment the links on the PR");
check(preview.permissions?.["pull-requests"] === "write", "preview job lacks pull-requests: write");
check(!/wrangler deploy\b/.test(previewRun), "preview job must not deploy to production");

// deploy and ingest are switched off until the repository variables say otherwise
const deploy = wf["deploy.yml"]?.jobs?.deploy;
check(deploy && /vars\.DEPLOY_ENABLED == 'true'/.test(deploy.if), "deploy job is not gated on DEPLOY_ENABLED");
const deployRun = runText(deploy);
check(/@datasites\/api exec wrangler deploy/.test(deployRun), "deploy does not deploy the API Worker");
check(/@datasites\/web exec wrangler deploy -c "wrangler\/\$site\.jsonc"/.test(deployRun), "deploy does not deploy the websites in WEB_SITES");
// Every launched website has a Worker config and an api.<domain> route, and every API route is a launched site.
const webSites = String(deploy.env?.WEB_SITES ?? "").split(/\s+/).filter(Boolean);
check(webSites.length > 0, "deploy.yml WEB_SITES lists no sites");
const apiRoutes = [...readFileSync(join(ROOT, "apps", "api", "wrangler.jsonc"), "utf8").matchAll(/"pattern":\s*"api\.([a-z]+)\.dev"/g)].map((m) => m[1]);
for (const site of webSites) {
  check(existsSync(join(ROOT, "apps", "web", "wrangler", `${site}.jsonc`)), `WEB_SITES lists ${site}, but apps/web/wrangler/${site}.jsonc is missing`);
  check(apiRoutes.includes(site), `WEB_SITES lists ${site}, but apps/api/wrangler.jsonc has no api.${site}.dev route`);
}
const unlaunched = apiRoutes.filter((s) => !webSites.includes(s));
check(unlaunched.length === 0, `apps/api/wrangler.jsonc routes api.<domain> for sites not in WEB_SITES: ${unlaunched.join(", ")}`);

// every ingest workflow: gated, scheduled at odd minutes, runs the pipelines, fits the private-repo minutes budget
// (measured minutes per run below, not a flat guess)
const ingestFiles = Object.keys(wf).filter((f) => /^ingest-[a-z]+\.ya?ml$/.test(f)).sort();
check(ingestFiles.includes("ingest-climate.yml"), "ingest-climate.yml is missing");
const runsPerDay = (cron) => {
  const [minute, hour, dom, month, dow] = cron.trim().split(/\s+/);
  const count = (field, size) => {
    if (field === "*") return size;
    const step = /^\*\/(\d+)$/.exec(field);
    if (step) return Math.ceil(size / Number(step[1]));
    return field.split(",").length;
  };
  const daysFraction = dom === "*" && month === "*" && dow === "*" ? 1 : dow !== "*" ? count(dow, 7) / 7 : 1 / 30;
  return count(minute, 60) * count(hour, 24) * daysFraction;
};
// Billed Actions minutes per run, measured 2026-10-24: a forced local run of each site (the daily datasets are due
// every day anyway) plus ~30 s of job setup, rounded up to whole minutes the way GitHub bills each job. Re-measure
// when a site gains a dataset or a schedule changes. Water assumes USGS_WATERDATA_API_KEY: keyless, the anonymous
// rate limit can stretch a run to its 60-minute timeout, which alone would use most of the free plan.
const MINUTES_PER_RUN = {
  "ingest-air.yml": 7, // 389 s: AirNow days and the AirData yearly file
  "ingest-carbon.yml": 5, // 268 s
  "ingest-climate.yml": 7, // 342 s, including the weekly world-capitals pass
  "ingest-economic.yml": 2, // 26 s with QCEW unreachable; the LAUS county file is 337 MB on days it changes
  "ingest-election.yml": 1, // 3 s
  "ingest-satellite.yml": 2, // 8 s keyless; Space-Track elements add a few requests
  "ingest-visualize.yml": 1, // 6 s
  "ingest-water.yml": 11, // ~10 min with an API key (65 min measured keyless and throttled)
  "ingest-wildfire.yml": 2, // 52 s: just over a billed minute
};
// Ingest gets 1,500 of the free plan's 2,000 private-repo minutes; CI (2 billed minutes a push) and deploys share the rest.
const INGEST_BUDGET = 1500;
let monthlyRuns = 0;
let monthlyMinutes = 0;
const perWorkflow = [];
for (const file of ingestFiles) {
  const job = wf[file].jobs?.ingest;
  check(job && /vars\.INGEST_ENABLED == 'true'/.test(job.if ?? ""), `${file}: ingest job is not gated on INGEST_ENABLED`);
  const crons = (wf[file].on?.schedule ?? []).map((s) => s.cron);
  check(crons.length > 0, `${file} has no schedule`);
  const perRun = MINUTES_PER_RUN[file];
  check(perRun, `${file}: no measured minutes per run in MINUTES_PER_RUN (time a forced run of its site and add it)`);
  let runs = 0;
  for (const cron of crons) {
    const minute = cron.trim().split(/\s+/)[0];
    check(minute !== "0" && minute !== "*", `${file}: schedule "${cron}" runs at minute ${minute}; use a specific odd minute`);
    runs += runsPerDay(cron) * 30;
  }
  monthlyRuns += runs;
  monthlyMinutes += runs * perRun;
  perWorkflow.push(`${file.replace(/^ingest-|\.ya?ml$/g, "")}=${Math.round(runs * perRun)}`);
  check(/datasites run /.test(runText(job)), `${file}: does not run a pipeline`);
}
// storage-gc: weekly, gated like ingest, and counted in the same budget (listing the bucket takes a few minutes).
const gcFile = "storage-gc.yml";
const gcJob = wf[gcFile]?.jobs?.gc;
check(gcJob && /vars\.INGEST_ENABLED == 'true'/.test(gcJob.if ?? ""), `${gcFile}: missing, or its gc job is not gated on INGEST_ENABLED`);
check(/datasites gc /.test(runText(gcJob)), `${gcFile}: does not run datasites gc`);
const gcRuns = (wf[gcFile].on?.schedule ?? []).reduce((n, s) => n + runsPerDay(s.cron) * 30, 0);
check(gcRuns > 0 && gcRuns <= 5, `${gcFile}: expected a weekly schedule, got ~${gcRuns} runs/month`);
monthlyMinutes += gcRuns * 5;
perWorkflow.push(`gc=${Math.round(gcRuns * 5)}`);

const stale = Object.keys(MINUTES_PER_RUN).filter((f) => !ingestFiles.includes(f));
check(stale.length === 0, `MINUTES_PER_RUN lists workflows that no longer exist: ${stale.join(", ")}`);
check(
  monthlyMinutes <= INGEST_BUDGET,
  `ingest schedules need ~${Math.round(monthlyMinutes)} Actions minutes/month (${perWorkflow.join(", ")}); budget is ${INGEST_BUDGET}`,
);

// keepalive: public repos lose scheduled workflows after 60 idle days, so every scheduled workflow is re-enabled monthly
const keep = wf["keepalive.yml"]?.jobs?.keepalive;
check(keep && /vars\.INGEST_ENABLED == 'true'/.test(keep.if ?? ""), "keepalive.yml: missing, or not gated on INGEST_ENABLED");
check(wf["keepalive.yml"].permissions?.actions === "write", "keepalive.yml needs permissions: actions: write");
const keepRuns = (wf["keepalive.yml"].on?.schedule ?? []).reduce((n, s) => n + runsPerDay(s.cron) * 30, 0);
check(keepRuns >= 1, "keepalive.yml must run at least monthly (GitHub disables idle schedules after 60 days)");
const keepText = runText(keep);
const scheduled = Object.keys(wf).filter((f) => (wf[f].on?.schedule ?? []).length > 0);
const keepWords = new Set(keepText.split(/[\s;\\]+/));
const unkept = scheduled.filter((f) => !keepWords.has(f.replace(/\.ya?ml$/, "")));
check(unkept.length === 0, `keepalive.yml does not re-enable: ${unkept.join(", ")}`);

// every secret and variable a workflow reads is explained in docs/SETUP.md
const setup = readFileSync(join(ROOT, "docs", "SETUP.md"), "utf8");
const names = new Set();
for (const text of Object.values(texts)) {
  for (const m of text.matchAll(/\b(?:secrets|vars)\.([A-Z][A-Z0-9_]*)/g)) if (m[1] !== "GITHUB_TOKEN") names.add(m[1]);
}
check(names.size > 0, "found no secrets or variables in the workflows (pattern broken?)");
const undocumented = [...names].filter((n) => !setup.includes(n)).sort();
check(undocumented.length === 0, `docs/SETUP.md does not mention: ${undocumented.join(", ")}`);

console.log(
  `WORKFLOWS OK (${Object.keys(wf).length} workflows, ${ingestFiles.length} ingest, ~${Math.round(monthlyRuns)} scheduled runs/month, ~${Math.round(monthlyMinutes)} of ${INGEST_BUDGET} ingest minutes: ${perWorkflow.join(", ")}; documented: ${[...names].sort().join(", ")})`,
);
