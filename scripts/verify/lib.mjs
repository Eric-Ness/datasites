// Shared helpers for the verification scripts in this folder. Each script prints one success marker
// only after every assertion passes, and exits nonzero (printing FAIL: ...) otherwise.
import { spawn, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const WINDOWS = process.platform === "win32";

export function fail(message) {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

export function check(condition, message) {
  if (!condition) fail(message);
}

/** With shell: true, Node joins arguments unquoted, so cmd.exe would split "SELECT site FROM ..." at its spaces. */
function shellArgs(args) {
  if (!WINDOWS) return args;
  return args.map((arg) => (/[\s"&|<>^()]/.test(arg) ? `"${arg.replace(/(\\*)"/g, '$1$1\\"')}"` : arg));
}

/** Runs a command to completion. Judged by exit status, never by whether it printed something. */
export function run(command, args, { cwd = ROOT, allowFailure = false, input } = {}) {
  const result = spawnSync(command, shellArgs(args), {
    cwd,
    input,
    encoding: "utf8",
    shell: WINDOWS, // pnpm and friends are .cmd shims on Windows
    maxBuffer: 64 * 1024 * 1024,
  });
  const out = { status: result.status ?? 1, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
  if (!allowFailure && out.status !== 0) {
    fail(`${command} ${args.join(" ")} exited ${out.status}\n${(out.stderr || out.stdout).slice(-2000)}`);
  }
  return out;
}

export function json(command, args, opts) {
  const { stdout } = run(command, args, opts);
  try {
    return JSON.parse(stdout);
  } catch {
    fail(`${command} ${args.join(" ")} did not print JSON:\n${stdout.slice(0, 500)}`);
  }
}

/** Starts a long-running server and returns a handle whose stop() kills the whole process tree. */
export function startServer(command, args, { cwd }) {
  const child = spawn(command, shellArgs(args), { cwd, shell: WINDOWS, detached: !WINDOWS, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  const keep = (chunk) => {
    log = (log + chunk.toString()).slice(-20_000);
  };
  child.stdout.on("data", keep);
  child.stderr.on("data", keep);
  return {
    log: () => log,
    stop() {
      if (child.exitCode !== null) return;
      if (WINDOWS) spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      else {
        try {
          process.kill(-child.pid, "SIGTERM");
        } catch {
          /* already gone */
        }
      }
    },
  };
}

export async function waitForHttp(url, { timeoutMs = 120_000, server } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(3000) });
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  fail(`${url} did not respond within ${timeoutMs / 1000}s\n${server ? server.log().slice(-3000) : ""}`);
}

/**
 * Reads a dataset published to the local bucket (.data/r2) by `datasites run ... --target local`.
 * Handles sharded file maps and location blobs, so verification scripts never parse the layout themselves.
 *   const ds = localDataset("climatedata", "co2-monthly");
 *   ds.doc.version; ds.paths(); ds.text("average/world.csv"); ds.json("average/world.json"); ds.locations()
 */
export function localDataset(site, dataset) {
  const bucket = join(ROOT, ".data", "r2");
  const read = (key) => {
    try {
      return readFileSync(join(bucket, key));
    } catch {
      return null;
    }
  };
  const readJson = (key) => {
    const body = read(key);
    return body ? JSON.parse(body.toString("utf8")) : null;
  };
  const blob = (hash) => read(`blobs/sha256/${hash.slice(0, 2)}/${hash}`);
  const manifest = readJson(`sites/${site}/datasets/${dataset}/manifest.json`);
  check(manifest, `${site}/${dataset} has not been published to .data/r2 (run: uv run datasites run ${site}/${dataset} --target local)`);
  const doc = readJson(`sites/${site}/datasets/${dataset}/versions/${manifest.current}.json`);
  check(doc, `${site}/${dataset}: version document ${manifest.current} is missing`);
  let files = null;
  const allFiles = () => {
    if (!files) {
      files = { ...doc.files };
      for (const hash of Object.values(doc.file_shards ?? {})) Object.assign(files, JSON.parse(blob(hash).toString("utf8")));
    }
    return files;
  };
  const file = (path) => {
    const entry = allFiles()[path];
    check(entry, `${site}/${dataset}: no file ${path} in version ${doc.version}`);
    const body = blob(entry.blob);
    check(body, `${site}/${dataset}: blob for ${path} is missing`);
    return body;
  };
  return {
    manifest,
    doc,
    paths: () => Object.keys(allFiles()).sort(),
    entry: (path) => allFiles()[path],
    buffer: file,
    text: (path) => file(path).toString("utf8"),
    json: (path) => JSON.parse(file(path).toString("utf8")),
    locations: () => doc.locations ?? (doc.locations_blob ? JSON.parse(blob(doc.locations_blob).toString("utf8")) : {}),
    state: () => readJson(`ops/state/${site}/${dataset}.json`),
  };
}

export function originRepo() {
  const url = run("git", ["remote", "get-url", "origin"]).stdout.trim();
  const m = /github\.com[/:]([^/]+)\/([^/.]+)(\.git)?$/.exec(url);
  if (!m) fail(`origin is not a GitHub repo: ${url}`);
  return `${m[1]}/${m[2]}`;
}
