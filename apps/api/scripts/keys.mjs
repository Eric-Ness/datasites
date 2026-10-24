#!/usr/bin/env node
// Admin tool for API keys. Keys are stored only as their SHA-256 in the KEYS KV namespace.
//
//   node scripts/keys.mjs issue --account <id> [--plan free|pro]   prints the new key ONCE
//   node scripts/keys.mjs revoke <key|hash|->                         status -> revoked (takes effect within ~5 min)
//   node scripts/keys.mjs delete <key|hash|->                         removes the record entirely
//   node scripts/keys.mjs show <key|hash|->
//   node scripts/keys.mjs list [--account <id>]
//   node scripts/keys.mjs set-plan <account> <free|pro> [--status active|suspended]
//
// Target: --local (default, wrangler's local state under apps/api/.wrangler) or --remote; --env staging for
// the staging namespace. --json prints machine-readable output. Pass "-" instead of a key to read it from
// stdin, so the key stays out of shell history and the process list.
import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const apiDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const wranglerBin = join(dirname(createRequire(join(apiDir, "package.json")).resolve("wrangler/package.json")), "bin", "wrangler.js");

const KEY_PATTERN = /^dk_live_[a-z2-7]{32}$/;
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const ACCOUNT_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const PLANS = ["free", "pro"];
const BASE32 = "abcdefghijklmnopqrstuvwxyz234567";

function die(message) {
  console.error(`keys: ${message}`);
  process.exit(1);
}

// ---- arguments ---------------------------------------------------------------------------------------

const argv = process.argv.slice(2);
const command = argv.shift();
const flags = {};
const positional = [];
for (let i = 0; i < argv.length; i++) {
  const arg = argv[i];
  if (arg === "--local" || arg === "--remote" || arg === "--json") flags[arg.slice(2)] = true;
  else if (arg.startsWith("--")) {
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) die(`${arg} needs a value`);
    flags[arg.slice(2)] = value;
    i++;
  } else positional.push(arg);
}
if (flags.local && flags.remote) die("pass --local or --remote, not both");
const target = flags.remote ? ["--remote"] : ["--local"];
const envArgs = flags.env ? ["--env", flags.env] : [];

// ---- wrangler ----------------------------------------------------------------------------------------

/** Runs wrangler without a shell, so JSON arguments need no quoting on any platform. */
function wrangler(args, { allowFailure = false } = {}) {
  const result = spawnSync(process.execPath, [wranglerBin, ...args], {
    cwd: apiDir,
    encoding: "utf8",
    env: { ...process.env, WRANGLER_SEND_METRICS: "false", FORCE_COLOR: "0" },
    maxBuffer: 64 * 1024 * 1024,
  });
  const out = { status: result.status ?? 1, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
  if (!allowFailure && out.status !== 0) die(`wrangler ${args[0]} ${args[1]} ${args[2]} failed:\n${(out.stderr || out.stdout).slice(-1500)}`);
  return out;
}

const kvArgs = ["--binding", "KEYS", ...target, ...envArgs];

function kvPut(name, value, metadata) {
  const dir = mkdtempSync(join(tmpdir(), "datasites-keys-"));
  const file = join(dir, "value.json");
  try {
    writeFileSync(file, JSON.stringify(value));
    wrangler(["kv", "key", "put", name, "--path", file, ...(metadata ? ["--metadata", JSON.stringify(metadata)] : []), ...kvArgs]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function kvGet(name) {
  const out = wrangler(["kv", "key", "get", name, "--text", ...kvArgs], { allowFailure: true });
  const text = out.stdout.trim();
  if (out.status !== 0 || !text.startsWith("{")) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function kvDelete(name) {
  wrangler(["kv", "key", "delete", name, ...kvArgs]);
}

function kvList(prefix) {
  const out = wrangler(["kv", "key", "list", ...(prefix ? ["--prefix", prefix] : []), ...kvArgs]).stdout;
  const start = out.indexOf("[");
  if (start === -1) die(`unexpected kv key list output:\n${out.slice(0, 500)}`);
  return JSON.parse(out.slice(start));
}

// ---- keys --------------------------------------------------------------------------------------------

function generateKey() {
  const bytes = randomBytes(20);
  let bits = 0;
  let value = 0;
  let out = "";
  for (const byte of bytes) {
    value = ((value << 8) | byte) & 0xffff;
    bits += 8;
    while (bits >= 5) {
      out += BASE32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  return `dk_live_${out}`;
}

const sha256 = (text) => createHash("sha256").update(text).digest("hex");

/** A key, its hash, or "-" (read the key or hash from stdin) -> the hash. Never echoes a key. */
function hashFrom(arg) {
  if (arg === undefined) die("give a key, its hash, or - to read it from stdin");
  const value = arg === "-" ? readFileSync(0, "utf8").trim() : arg.trim();
  if (KEY_PATTERN.test(value)) return sha256(value);
  if (HASH_PATTERN.test(value)) return value;
  die("that is neither a dk_live_ key nor a 64-character hex hash");
}

function print(human, data) {
  if (flags.json) console.log(JSON.stringify(data));
  else console.log(human);
}

function redact(hash, record) {
  return { hash, ...record };
}

function issue() {
  const account = flags.account;
  const plan = flags.plan ?? "free";
  if (!account || !ACCOUNT_PATTERN.test(account)) die("--account <id> is required: lowercase letters, digits, - and _, at most 64");
  if (!PLANS.includes(plan)) die(`--plan must be one of ${PLANS.join(", ")}`);
  const key = generateKey();
  const hash = sha256(key);
  const record = { account, plan, status: "active", created_at: new Date().toISOString() };
  kvPut(hash, record, { account, plan, status: "active" });
  if (flags.json) console.log(JSON.stringify({ key, hash, ...record }));
  else {
    console.log(`Issued a ${plan} key for ${account}. It is shown only now; store it safely:\n`);
    console.log(`  ${key}\n`);
    console.log(`hash ${hash}`);
  }
}

function revoke() {
  const hash = hashFrom(positional[0]);
  const record = kvGet(hash);
  if (!record) die(`no key with hash ${hash}`);
  const updated = { ...record, status: "revoked", revoked_at: new Date().toISOString() };
  kvPut(hash, updated, { account: record.account, plan: record.plan, status: "revoked" });
  print(`Revoked ${hash} (account ${record.account}). Isolates that cached it stop accepting it within 5 minutes.`, redact(hash, updated));
}

function remove() {
  const hash = hashFrom(positional[0]);
  kvDelete(hash);
  print(`Deleted ${hash}.`, { hash, deleted: true });
}

function show() {
  const hash = hashFrom(positional[0]);
  const record = kvGet(hash);
  if (!record) die(`no key with hash ${hash}`);
  const account = kvGet(`acct:${record.account}`);
  print(
    `${hash}\n  account ${record.account}\n  plan    ${account?.plan ?? record.plan}${account ? " (from the account record)" : ""}\n  status  ${record.status}\n  created ${record.created_at}${record.revoked_at ? `\n  revoked ${record.revoked_at}` : ""}`,
    { ...redact(hash, record), account_record: account },
  );
}

function list() {
  const rows = kvList()
    .filter((entry) => HASH_PATTERN.test(entry.name))
    .map((entry) => {
      const meta = entry.metadata ?? kvGet(entry.name) ?? {};
      return { hash: entry.name, account: meta.account ?? "?", plan: meta.plan ?? "?", status: meta.status ?? "?" };
    })
    .filter((row) => !flags.account || row.account === flags.account);
  if (flags.json) console.log(JSON.stringify(rows));
  else if (!rows.length) console.log("No keys.");
  else for (const r of rows) console.log(`${r.hash}  ${r.status.padEnd(7)}  ${r.plan.padEnd(4)}  ${r.account}`);
}

function setPlan() {
  const [account, plan] = positional;
  const status = flags.status ?? "active";
  if (!account || !ACCOUNT_PATTERN.test(account)) die("usage: set-plan <account> <free|pro> [--status active|suspended]");
  if (!PLANS.includes(plan)) die(`plan must be one of ${PLANS.join(", ")}`);
  if (!["active", "suspended"].includes(status)) die("--status must be active or suspended");
  const existing = kvGet(`acct:${account}`) ?? {};
  const record = {
    stripe_customer: null,
    stripe_subscription: null,
    ...existing,
    account,
    plan,
    status,
    updated_at: new Date().toISOString(),
  };
  kvPut(`acct:${account}`, record, { plan, status, stripe_customer: record.stripe_customer ?? null });
  print(`Account ${account}: plan ${plan}, ${status}. Cached lookups pick it up within 5 minutes.`, record);
}

const commands = { issue, revoke, delete: remove, show, list, "set-plan": setPlan };
if (!commands[command]) {
  console.error("usage: node scripts/keys.mjs <issue|revoke|delete|show|list|set-plan> [...] [--local|--remote] [--env staging] [--json]");
  process.exit(2);
}
commands[command]();
