// A2: API keys under `wrangler dev` with local bindings (KV, rate limits, Durable Object).
//   node scripts/verify/api-keys.mjs
// Issues keys into local KV with apps/api/scripts/keys.mjs, checks a free key, an invalid key, a revoked key
// and anonymous access, then deletes its test keys and stops the server. Port 8799.
import { join } from "node:path";
import { ROOT, check, fail, run, startServer, waitForHttp } from "./lib.mjs";

const apiDir = join(ROOT, "apps", "api");
const base = "http://127.0.0.1:8799";
const q = "site=climatedata";
const TEST_ACCOUNT = "verify-api-keys";

function keys(args) {
  const { stdout } = run("node", [join(apiDir, "scripts", "keys.mjs"), ...args, "--local", "--json"], { cwd: apiDir });
  try {
    return JSON.parse(stdout.trim().split(/\r?\n/).pop());
  } catch {
    fail(`keys.mjs ${args[0]} did not print JSON:\n${stdout.slice(0, 500)}`);
  }
}

/** Remove every key of the test account, including ones an interrupted earlier run left behind. */
function cleanup() {
  for (const row of keys(["list", "--account", TEST_ACCOUNT])) keys(["delete", row.hash]);
}

async function get(path, headers = {}) {
  const res = await fetch(`${base}${path}${path.includes("?") ? "&" : "?"}${q}`, { headers });
  const text = await res.text();
  return { res, text, json: () => JSON.parse(text) };
}

cleanup();
const server = startServer("pnpm", ["exec", "wrangler", "dev", "--port", "8799", "--ip", "127.0.0.1", "--var", "ENV:development"], { cwd: apiDir });
const stop = () => server.stop();
process.on("exit", stop);

try {
  await waitForHttp(`${base}/v1?${q}`, { server });

  // 1. an issued free key is accepted with its plan
  const free = keys(["issue", "--account", TEST_ACCOUNT, "--plan", "free"]);
  check(/^dk_live_[a-z2-7]{32}$/.test(free.key), "issued key has the wrong format");
  check(/^[0-9a-f]{64}$/.test(free.hash), "issued key has no sha256 hash");
  const stored = keys(["show", free.hash]);
  check(stored.account === TEST_ACCOUNT && stored.plan === "free" && stored.status === "active", `stored record: ${JSON.stringify(stored)}`);
  check(!JSON.stringify(stored).includes(free.key), "the stored record contains the key itself");

  const bearer = await get("/v1", { authorization: `Bearer ${free.key}` });
  check(bearer.res.status === 200, `Bearer key: HTTP ${bearer.res.status} ${bearer.text.slice(0, 300)}`);
  check(bearer.res.headers.get("x-plan") === "free", `Bearer key: X-Plan ${bearer.res.headers.get("x-plan")}`);
  check(bearer.json().site === "climatedata", "Bearer key: wrong site");
  const header = await get("/v1", { "x-api-key": free.key });
  check(header.res.status === 200 && header.res.headers.get("x-plan") === "free", `X-API-Key: HTTP ${header.res.status}, X-Plan ${header.res.headers.get("x-plan")}`);

  // 2. invalid keys get a 401 problem document
  for (const [label, key] of [
    ["malformed", "dk_live_short"],
    ["unknown", `dk_live_${"a".repeat(32)}`],
  ]) {
    const bad = await get("/v1", { "x-api-key": key });
    check(bad.res.status === 401, `${label} key: HTTP ${bad.res.status}, expected 401`);
    check(bad.res.headers.get("content-type") === "application/problem+json", `${label} key: content-type ${bad.res.headers.get("content-type")}`);
    const body = bad.json();
    check(body.status === 401 && /#invalid-key$/.test(body.type) && body.title, `${label} key: problem ${bad.text}`);
    check((bad.res.headers.get("www-authenticate") ?? "").startsWith("Bearer"), `${label} key: no WWW-Authenticate`);
    check(!bad.text.includes(key), `${label} key: the response echoes the key`);
  }

  // 3. a revoked key gets 401 (revoked before first use, so no isolate has it cached)
  const revoked = keys(["issue", "--account", TEST_ACCOUNT, "--plan", "free"]);
  const after = keys(["revoke", revoked.hash]);
  check(after.status === "revoked", `revoke: ${JSON.stringify(after)}`);
  const rev = await get("/v1", { authorization: `Bearer ${revoked.key}` });
  check(rev.res.status === 401 && rev.res.headers.get("content-type") === "application/problem+json", `revoked key: HTTP ${rev.res.status}`);
  check(/#invalid-key$/.test(rev.json().type), `revoked key: ${rev.text}`);

  // 4. anonymous requests still work, data paths included
  const anon = await get("/v1");
  check(anon.res.status === 200 && anon.res.headers.get("x-plan") === "anonymous", `anonymous: HTTP ${anon.res.status}, X-Plan ${anon.res.headers.get("x-plan")}`);
  const catalog = await get("/v1/catalog");
  check(catalog.res.status === 200 && catalog.res.headers.get("x-plan") === "anonymous", `anonymous catalog: HTTP ${catalog.res.status}`);

  // 5. preflight for a keyed browser request
  const pre = await fetch(`${base}/v1?${q}`, {
    method: "OPTIONS",
    headers: { origin: "https://example.org", "access-control-request-method": "GET", "access-control-request-headers": "x-api-key" },
  });
  check(pre.status === 204 && /x-api-key/i.test(pre.headers.get("access-control-allow-headers") ?? ""), `preflight: HTTP ${pre.status}`);

  cleanup();
  check(keys(["list", "--account", TEST_ACCOUNT]).length === 0, "test keys were not cleaned up");
  stop();
  console.log(`API KEYS OK (free key accepted with X-Plan: free; malformed, unknown and revoked keys 401 problem+json; anonymous 200 with X-Plan: anonymous; test keys removed)`);
} catch (err) {
  try {
    cleanup();
  } catch {
    /* best effort */
  }
  fail(`${err instanceof Error ? err.stack : err}\n${server.log().slice(-2000)}`);
} finally {
  stop();
}
