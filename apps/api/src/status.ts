import { sites } from "@datasites/registry";
import { durationMs } from "./duration";
import { readJson, TTL } from "./store";
import { keys, type Catalog, type CatalogEntry, type PipelineState } from "./types";

export type DatasetStatusValue =
  | "ok"
  | "pending"
  | "late"
  | "stale-upstream"
  | "failing"
  | "blocked"
  | "discontinued";

export interface DatasetStatus {
  id: string;
  status: DatasetStatusValue;
  version: string | null;
  checked_at: string | null;
  source_updated_at: string | null;
  sla: string;
  consecutive_failures: number;
  last_error: string | null;
}

export interface SiteStatus {
  site: string;
  generated_at: string;
  overall: "ok" | "degraded";
  datasets: DatasetStatus[];
}

/** Statuses that are our problem and should page someone. "stale-upstream" is the source's problem. */
const ALERTING = new Set<DatasetStatusValue>(["late", "failing", "blocked"]);

function ageMs(iso: string | null, now: number): number {
  return iso ? now - Date.parse(iso) : Number.POSITIVE_INFINITY;
}

function classify(entry: CatalogEntry, state: PipelineState | null, now: number): DatasetStatusValue {
  if (entry.discontinued) return "discontinued";
  if (!state?.checked_at) return "pending";
  if (state.consecutive_failures >= 3) return "failing";
  if (state.blocked_version) return "blocked";
  if (ageMs(state.checked_at, now) > 2 * durationMs(entry.check_every)) return "late";
  if (state.source_updated_at && ageMs(state.source_updated_at, now) > durationMs(entry.sla)) return "stale-upstream";
  return "ok";
}

export function datasetStatus(entry: CatalogEntry, state: PipelineState | null, now: number): DatasetStatus {
  return {
    id: entry.id,
    status: classify(entry, state, now),
    version: entry.current,
    checked_at: state?.checked_at ?? null,
    source_updated_at: state?.source_updated_at ?? null,
    sla: entry.sla,
    consecutive_failures: state?.consecutive_failures ?? 0,
    last_error: state?.last_error ?? null,
  };
}

export async function siteStatus(bucket: R2Bucket, siteId: string, now: number): Promise<SiteStatus> {
  const catalog = await readJson<Catalog>(bucket, keys.catalog(siteId), TTL.catalog);
  const entries = catalog?.datasets ?? [];
  const states = await Promise.all(
    entries.map((entry) => readJson<PipelineState>(bucket, keys.state(siteId, entry.id), TTL.state)),
  );
  const datasets = entries.map((entry, i) => datasetStatus(entry, states[i] ?? null, now));
  const healthy = datasets.every((d) => d.status === "ok" || d.status === "discontinued");
  return { site: siteId, generated_at: new Date(now).toISOString(), overall: healthy ? "ok" : "degraded", datasets };
}

/** Cron: check every site and ping the healthchecks.io dead-man switch with the result. */
export async function runFreshnessCheck(env: Env, now = Date.now()): Promise<string[]> {
  const problems: string[] = [];
  for (const site of sites) {
    const status = await siteStatus(env.DATA, site.id, now);
    for (const d of status.datasets) {
      if (ALERTING.has(d.status)) {
        problems.push(`${site.id}/${d.id}: ${d.status}${d.last_error ? ` (${d.last_error})` : ""}`);
      }
    }
  }
  if (env.HEALTHCHECK_URL) {
    const url = problems.length ? `${env.HEALTHCHECK_URL}/fail` : env.HEALTHCHECK_URL;
    await fetch(url, { method: "POST", body: problems.join("\n") || "all datasets ok" });
  }
  return problems;
}
