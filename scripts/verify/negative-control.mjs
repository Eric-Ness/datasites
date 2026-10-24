// Proves a domain's verification script can fail: runs `node scripts/verify/<domain>.mjs --negative-control`,
// which must corrupt its own copy of one expected value, and passes only if that run fails with FAIL:.
//   node scripts/verify/negative-control.mjs <domain>
import { existsSync } from "node:fs";
import { join } from "node:path";
import { ROOT, check, run } from "./lib.mjs";

const domain = process.argv[2];
check(/^[a-z]+$/.test(domain ?? ""), "usage: node scripts/verify/negative-control.mjs <domain>");
const script = join(ROOT, "scripts", "verify", `${domain}.mjs`);
check(existsSync(script), `scripts/verify/${domain}.mjs does not exist`);

const normal = run("node", [script], { allowFailure: true });
check(normal.status === 0, `the normal run must pass first (exit ${normal.status}):\n${(normal.stderr || normal.stdout).slice(-1500)}`);

const corrupted = run("node", [script, "--negative-control"], { allowFailure: true });
const output = corrupted.stdout + corrupted.stderr;
check(corrupted.status !== 0, `--negative-control exited 0, so the check cannot detect a wrong value:\n${output.slice(-1500)}`);
check(/FAIL:/.test(output), `--negative-control failed without a FAIL: message (crash rather than a detected mismatch?):\n${output.slice(-1500)}`);
check(!/VERIFIED/.test(output), "--negative-control still printed its success marker");

console.log(`NEGATIVE CONTROL OK (${domain}: normal run passes, corrupted expectation fails: ${output.match(/FAIL:[^\n]*/)[0].slice(0, 160)})`);
