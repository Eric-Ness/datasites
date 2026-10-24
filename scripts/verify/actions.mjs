// G7: every `uses:` in .github/workflows resolves to a real ref on GitHub.
// Judged by the exit status of `gh api`, because gh prints a 404 body to stdout on failure.
//   node scripts/verify/actions.mjs                       check every workflow
//   node scripts/verify/actions.mjs --only owner/repo@ref check one ref (negative-control runs)
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { ROOT, check, fail, run } from "./lib.mjs";

function refsFromWorkflows() {
  const dir = join(ROOT, ".github", "workflows");
  const refs = new Set();
  for (const file of readdirSync(dir).filter((f) => /\.ya?ml$/.test(f))) {
    const wf = parse(readFileSync(join(dir, file), "utf8"));
    for (const job of Object.values(wf.jobs ?? {})) {
      for (const step of job.steps ?? []) if (step.uses) refs.add(step.uses);
      if (job.uses) refs.add(job.uses);
    }
  }
  return [...refs].sort();
}

const only = process.argv.indexOf("--only");
const refs = only === -1 ? refsFromWorkflows() : [process.argv[only + 1]];
check(refs.length > 0 && refs.every(Boolean), "no action references found");

const missing = [];
for (const uses of refs) {
  if (uses.startsWith("./") || uses.startsWith("docker://")) continue;
  const m = /^([^/@]+)\/([^/@]+)(?:\/[^@]*)?@(.+)$/.exec(uses);
  if (!m) fail(`cannot parse uses: ${uses}`);
  const [, owner, repo, ref] = m;
  const res = run("gh", ["api", `repos/${owner}/${repo}/commits/${encodeURIComponent(ref)}`, "--jq", ".sha"], { allowFailure: true });
  if (res.status !== 0 || !/^[0-9a-f]{40}$/.test(res.stdout.trim())) missing.push(uses);
}
check(missing.length === 0, `these action refs do not resolve on GitHub: ${missing.join(", ")}`);
console.log(`ACTIONS RESOLVE OK (${refs.length} refs: ${refs.join(", ")})`);
