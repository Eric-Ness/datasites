import { sites } from "@datasites/registry";
import { durationMs } from "./duration";
import { readJson, TTL } from "./store";
import { keys, type Catalog, type CatalogEntry, type PipelineState } from "./types";

export type DatasetStatusValue =
  | "ok"
  | "pending"
  | "unpublished"
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

/**
 * How far past twice check_every a dataset may go before it counts as late. GitHub starts scheduled workflows
 * minutes to an hour after their cron time, and a run takes up to an hour more, so a schedule whose longest gap is
 * exactly twice check_every (healthdata: Mondays and Thursdays with P2D, a 4-day weekend gap) would page every
 * week the run started a little later than the one before (it did on 2026-11-16, for 15 minutes).
 */
export const LATE_GRACE_MS = 6 * 60 * 60 * 1000;

function ageMs(iso: string | null, now: number): number {
  return iso ? now - Date.parse(iso) : Number.POSITIVE_INFINITY;
}

function classify(entry: CatalogEntry, state: PipelineState | null, now: number): DatasetStatusValue {
  if (entry.discontinued) return "discontinued";
  // In the catalog but never published: waiting on credentials, or the source has been down since day one.
  // Repeated failures still page, so a pipeline that never worked doesn't sit quietly as "unpublished".
  if (!entry.current) return (state?.consecutive_failures ?? 0) >= 3 ? "failing" : "unpublished";
  if (!state?.checked_at) return "pending";
  if (state.consecutive_failures >= 3) return "failing";
  if (state.blocked_version) return "blocked";
  if (ageMs(state.checked_at, now) > 2 * durationMs(entry.check_every) + LATE_GRACE_MS) return "late";
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
  const healthy = datasets.every((d) => ["ok", "discontinued", "unpublished"].includes(d.status));
  return { site: siteId, generated_at: new Date(now).toISOString(), overall: healthy ? "ok" : "degraded", datasets };
}

/** Tries per ping, and how long each may take; tickharbor's grace (30 min) dwarfs the worst case (~16 s). */
export const PING_ATTEMPTS = 3;
const PING_TIMEOUT_MS = 5_000;

/**
 * POSTs to a tickharbor ping URL, retrying network errors and 5xx. Never throws: a broken ping must not stop the
 * check, and tickharbor pages on the missing ping anyway. Logs the path only, never the URL (it is a secret).
 */
export async function ping(base: string, suffix: "" | "/start" | "/fail", body?: string): Promise<boolean> {
  for (let attempt = 1; attempt <= PING_ATTEMPTS; attempt++) {
    try {
      const res = await fetch(`${base}${suffix}`, { method: "POST", body, signal: AbortSignal.timeout(PING_TIMEOUT_MS) });
      if (res.ok) return true;
      if (res.status < 500) {
        console.error(`healthcheck ping ${suffix || "/"}: HTTP ${res.status}, not retrying`);
        return false;
      }
      console.warn(`healthcheck ping ${suffix || "/"}: HTTP ${res.status} (attempt ${attempt})`);
    } catch (err) {
      console.warn(`healthcheck ping ${suffix || "/"}: ${err instanceof Error ? err.message : String(err)} (attempt ${attempt})`);
    }
  }
  console.error(`healthcheck ping ${suffix || "/"}: gave up after ${PING_ATTEMPTS} attempts`);
  return false;
}

/**
 * Waits before re-checking the sites whose reads threw. R2 answers "internal error (10001)" for seconds at a time:
 * on 2026-11-20 13:00 and 2026-11-22 00:15 UTC it failed eight sites' reads through readJson's own retries
 * (250 ms, 1 s), the check paged, and the next run 15 minutes later was fine. Cloudflare's advice for 10001 is to
 * retry and to treat it as an outage only if it persists, so a site is a problem only if it still fails about a
 * minute later. A real outage still pages in the same run. Request paths keep the short retry.
 */
export const RECHECK_DELAYS_MS = [15_000, 45_000];

/** Every site's status, re-checking those whose reads threw after each delay; a recovered site counts as checked. */
async function checkSites(
  bucket: R2Bucket,
  now: number,
  recheckDelaysMs: readonly number[],
): Promise<PromiseSettledResult<SiteStatus>[]> {
  const results = await Promise.allSettled(sites.map((site) => siteStatus(bucket, site.id, now)));
  for (const delay of recheckDelaysMs) {
    const failed = results.flatMap((r, i) => (r.status === "rejected" ? [i] : []));
    if (!failed.length) break;
    console.warn(`freshness: ${failed.map((i) => sites[i]!.id).join(", ")} failed to read; re-checking in ${delay} ms`);
    await new Promise((resolve) => setTimeout(resolve, delay));
    const again = await Promise.allSettled(failed.map((i) => siteStatus(bucket, sites[i]!.id, now)));
    failed.forEach((siteIndex, j) => {
      results[siteIndex] = again[j]!;
    });
  }
  return results;
}

/**
 * Cron: check every site and ping the tickharbor dead-man switch (a healthchecks.io-style ping URL) with the result.
 * Pings /start first so tickharbor records the run time, reads the sites in parallel, and always sends a final
 * ping: a site whose check throws (an R2 error, a bad duration in its catalog) is reported as a problem, not lost.
 */
export async function runFreshnessCheck(
  env: Env,
  now = Date.now(),
  recheckDelaysMs: readonly number[] = RECHECK_DELAYS_MS,
): Promise<string[]> {
  const hc = env.HEALTHCHECK_URL;
  // Sent alongside the reads, not before them; awaited before the final ping so tickharbor sees them in order.
  const started = hc ? ping(hc, "/start") : Promise.resolve(false);
  const problems: string[] = [];
  try {
    const results = await checkSites(env.DATA, now, recheckDelaysMs);
    results.forEach((result, i) => {
      const siteId = sites[i]!.id;
      if (result.status === "rejected") {
        const reason = result.reason instanceof Error ? result.reason.message : String(result.reason);
        problems.push(`${siteId}: check failed (${reason})`);
        return;
      }
      for (const d of result.value.datasets) {
        if (ALERTING.has(d.status)) {
          problems.push(`${siteId}/${d.id}: ${d.status}${d.last_error ? ` (${d.last_error})` : ""}`);
        }
      }
    });
  } catch (err) {
    problems.push(`freshness check crashed (${err instanceof Error ? err.message : String(err)})`);
  } finally {
    await started;
    if (hc) await ping(hc, problems.length ? "/fail" : "", problems.join("\n") || "all datasets ok");
  }
  return problems;
}
