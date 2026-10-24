// G8: CI uploads preview versions of both Workers on PRs (once deploys are enabled), the gated
// workflows are gated, and every secret/variable the workflows read is documented in docs/SETUP.md.
import { readdirSync, readFileSync } from "node:fs";
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
for (const app of ["coming-soon", "api"]) check(new RegExp(`@datasites/${app} exec wrangler deploy`).test(deployRun), `deploy does not deploy ${app}`);
const ingest = wf["ingest-climate.yml"]?.jobs?.ingest;
check(ingest && /vars\.INGEST_ENABLED == 'true'/.test(ingest.if), "ingest job is not gated on INGEST_ENABLED");
check(wf["ingest-climate.yml"].on?.schedule?.length > 0, "ingest-climate has no schedule");

// every secret and variable a workflow reads is explained in docs/SETUP.md
const setup = readFileSync(join(ROOT, "docs", "SETUP.md"), "utf8");
const names = new Set();
for (const text of Object.values(texts)) {
  for (const m of text.matchAll(/\b(?:secrets|vars)\.([A-Z][A-Z0-9_]*)/g)) if (m[1] !== "GITHUB_TOKEN") names.add(m[1]);
}
check(names.size > 0, "found no secrets or variables in the workflows (pattern broken?)");
const undocumented = [...names].filter((n) => !setup.includes(n)).sort();
check(undocumented.length === 0, `docs/SETUP.md does not mention: ${undocumented.join(", ")}`);

console.log(`WORKFLOWS OK (${Object.keys(wf).length} workflows; documented: ${[...names].sort().join(", ")})`);
