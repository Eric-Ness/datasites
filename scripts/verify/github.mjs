// G1: the GitHub repo is private and the Phase 0 PR is open from its branch into main.
import { check, json, originRepo } from "./lib.mjs";

const repo = originRepo();
const info = json("gh", ["repo", "view", repo, "--json", "visibility,defaultBranchRef,nameWithOwner"]);
check(info.nameWithOwner === "Eric-Ness/datasites", `origin is ${info.nameWithOwner}, expected Eric-Ness/datasites`);
check(info.visibility === "PRIVATE", `${repo} visibility is ${info.visibility}, expected PRIVATE`);
check(info.defaultBranchRef?.name === "main", `default branch is ${info.defaultBranchRef?.name}, expected main`);

const pr = json("gh", ["pr", "view", "phase-0/scaffold", "--repo", repo, "--json", "number,state,headRefName,baseRefName"]);
check(pr.number === 1, `the phase-0/scaffold PR is #${pr.number}, expected #1`);
check(pr.state === "OPEN", `PR #${pr.number} is ${pr.state}, expected OPEN`);
check(pr.baseRefName === "main", `PR #${pr.number} targets ${pr.baseRefName}, expected main`);

console.log(`GITHUB OK: ${repo} is private, PR #${pr.number} open ${pr.headRefName} -> ${pr.baseRefName}`);
