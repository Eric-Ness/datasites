// Shared helpers for the verification scripts in this folder. Each script prints one success marker
// only after every assertion passes, and exits nonzero (printing FAIL: ...) otherwise.
import { spawn, spawnSync } from "node:child_process";
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

export function originRepo() {
  const url = run("git", ["remote", "get-url", "origin"]).stdout.trim();
  const m = /github\.com[/:]([^/]+)\/([^/.]+)(\.git)?$/.exec(url);
  if (!m) fail(`origin is not a GitHub repo: ${url}`);
  return `${m[1]}/${m[2]}`;
}
