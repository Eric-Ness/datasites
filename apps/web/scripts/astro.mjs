// Runs the Astro CLI with telemetry off, so builds make no network requests.
//   node scripts/astro.mjs build | dev | check | preview
import { spawn } from "node:child_process";
import { astroBin } from "./lib.mjs";

const child = spawn(process.execPath, [astroBin, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, ASTRO_TELEMETRY_DISABLED: "1" },
});
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
