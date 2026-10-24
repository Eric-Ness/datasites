// G9: the latest `ci` workflow run for the current HEAD commit finished successfully on GitHub.
// One read of GitHub's state; it never waits or polls.
import { check, json, originRepo, run } from "./lib.mjs";

const repo = originRepo();
const head = run("git", ["rev-parse", "HEAD"]).stdout.trim();
const upstream = run("git", ["rev-parse", "@{u}"], { allowFailure: true });
check(upstream.status === 0 && upstream.stdout.trim() === head, `HEAD ${head.slice(0, 7)} is not pushed to its upstream branch`);

const runs = json("gh", ["run", "list", "--repo", repo, "--workflow", "ci.yml", "--commit", head, "--limit", "5", "--json", "databaseId,status,conclusion,event,createdAt"]);
check(runs.length > 0, `no ci run exists yet for ${head.slice(0, 7)}`);
const latest = runs.sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
check(latest.status === "completed", `ci run ${latest.databaseId} for ${head.slice(0, 7)} is still ${latest.status}`);
check(latest.conclusion === "success", `ci run ${latest.databaseId} for ${head.slice(0, 7)} concluded ${latest.conclusion}`);

const { jobs } = json("gh", ["run", "view", String(latest.databaseId), "--repo", repo, "--json", "jobs"]);
const bad = jobs.filter((j) => !["success", "skipped"].includes(j.conclusion));
check(bad.length === 0, `jobs not successful: ${bad.map((j) => `${j.name}=${j.conclusion}`).join(", ")}`);
const ran = jobs.filter((j) => j.conclusion === "success").map((j) => j.name);
check(ran.includes("Workers (TypeScript)") && ran.includes("Pipelines (Python)"), `expected both test jobs to run, got: ${ran.join(", ")}`);

console.log(`CI GREEN ${head.slice(0, 7)} (run ${latest.databaseId}: ${jobs.map((j) => `${j.name}=${j.conclusion}`).join(", ")})`);
