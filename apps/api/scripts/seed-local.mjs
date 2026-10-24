// Copies local pipeline output (<repo>/.data/r2, written by `datasites run ... --target local`)
// into wrangler's local R2 emulator, so `pnpm dev` serves real data on http://localhost:8787.
import { readdirSync } from "node:fs";
import { join, relative, sep, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const apiDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = join(apiDir, "..", "..", ".data", "r2");
const BUCKET = "datasites-data";
const SERVED_PREFIXES = ["sites/", "blobs/", "ops/"]; // raw/ is an archive the API never reads

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else if (!entry.name.endsWith(".tmp")) yield path;
  }
}

let files;
try {
  files = [...walk(dataDir)];
} catch {
  console.error(`Nothing to seed: ${dataDir} does not exist. Run a pipeline first:\n  cd pipelines && uv run datasites run climatedata --target local`);
  process.exit(1);
}

const keys = files
  .map((file) => ({ file, key: relative(dataDir, file).split(sep).join("/") }))
  .filter(({ key }) => SERVED_PREFIXES.some((prefix) => key.startsWith(prefix)));

for (const { file, key } of keys) {
  const result = spawnSync("pnpm", ["exec", "wrangler", "r2", "object", "put", `${BUCKET}/${key}`, "--file", file, "--local"], {
    cwd: apiDir,
    stdio: ["ignore", "ignore", "inherit"],
    shell: process.platform === "win32",
  });
  if (result.status !== 0) {
    console.error(`failed to upload ${key}`);
    process.exit(result.status ?? 1);
  }
  console.log(`seeded ${key}`);
}
console.log(`\n${keys.length} objects in local R2 bucket ${BUCKET}. Start the API with: pnpm dev:api`);
