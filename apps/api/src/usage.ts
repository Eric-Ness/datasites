// Usage metering.
//  - Analytics Engine (binding USAGE, optional): one data point per request, for dashboards. It samples at
//    high volume, so it is never used for billing.
//  - UsageCounter Durable Object (binding USAGE_COUNTER, optional): one instance per account holding exact
//    daily request counts. The nightly billing cron reads it and reports to Stripe.

export const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
/** Days of history a counter keeps. Stripe accepts meter events up to 35 days old; the rest is for support. */
export const RETAIN_DAYS = 400;

export function utcDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export interface DayUsage {
  day: string;
  count: number;
  reported: boolean;
}

const dayKey = (day: string) => `day:${day}`;
const reportedKey = (day: string) => `reported:${day}`;

function bad(message: string): Response {
  return Response.json({ error: message }, { status: 400 });
}

/**
 * Exact per-account daily counts. Talked to over fetch (not RPC) so the class needs no `cloudflare:workers`
 * import and the Worker stays testable in plain Node. Storage is SQLite-backed (see the v1 migration); the
 * key-value storage API is used because each operation touches one or two keys.
 *
 *   POST /increment {"day":"2026-10-24","n":1}   -> {"day","count"}
 *   GET  /usage?day=2026-10-24                   -> {"day","count","reported"}
 *   GET  /usage                                  -> {"days":[...]} (every retained day)
 *   POST /reported {"day":"2026-10-24"}          -> {"day","reported":true}
 *
 * The runtime's input gate serializes these handlers per object, so read-modify-write is safe.
 */
export class UsageCounter implements DurableObject {
  constructor(
    private readonly state: DurableObjectState,
    _env: Env,
  ) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const storage = this.state.storage;

    if (request.method === "POST" && url.pathname === "/increment") {
      const body = (await request.json().catch(() => null)) as { day?: unknown; n?: unknown } | null;
      const day = body?.day;
      const n = body?.n ?? 1;
      if (typeof day !== "string" || !DAY_PATTERN.test(day)) return bad("day must be YYYY-MM-DD");
      if (typeof n !== "number" || !Number.isInteger(n) || n < 1 || n > 1_000_000) return bad("n must be a positive integer");
      const previous = await storage.get<number>(dayKey(day));
      const count = (previous ?? 0) + n;
      await storage.put(dayKey(day), count);
      if (previous === undefined) await this.prune(day);
      return Response.json({ day, count });
    }

    if (request.method === "GET" && url.pathname === "/usage") {
      const day = url.searchParams.get("day");
      if (day !== null) {
        if (!DAY_PATTERN.test(day)) return bad("day must be YYYY-MM-DD");
        const [count, reported] = await Promise.all([storage.get<number>(dayKey(day)), storage.get<boolean>(reportedKey(day))]);
        return Response.json({ day, count: count ?? 0, reported: reported === true } satisfies DayUsage);
      }
      const [counts, reported] = await Promise.all([
        storage.list<number>({ prefix: "day:" }),
        storage.list<boolean>({ prefix: "reported:" }),
      ]);
      const days: DayUsage[] = [...counts].map(([key, count]) => {
        const d = key.slice("day:".length);
        return { day: d, count, reported: reported.get(reportedKey(d)) === true };
      });
      return Response.json({ days });
    }

    if (request.method === "POST" && url.pathname === "/reported") {
      const body = (await request.json().catch(() => null)) as { day?: unknown } | null;
      const day = body?.day;
      if (typeof day !== "string" || !DAY_PATTERN.test(day)) return bad("day must be YYYY-MM-DD");
      await storage.put(reportedKey(day), true);
      return Response.json({ day, reported: true });
    }

    return Response.json({ error: "not found" }, { status: 404 });
  }

  /** Runs once per account per day (on the day's first increment): drop days older than RETAIN_DAYS. */
  private async prune(today: string): Promise<void> {
    const cutoff = utcDay(Date.parse(`${today}T00:00:00Z`) - RETAIN_DAYS * 86_400_000);
    const storage = this.state.storage;
    const old = [
      ...(await storage.list({ prefix: "day:", end: dayKey(cutoff) })).keys(),
      ...(await storage.list({ prefix: "reported:", end: reportedKey(cutoff) })).keys(),
    ];
    for (let i = 0; i < old.length; i += 128) await storage.delete(old.slice(i, i + 128));
  }
}

function counter(env: Env, account: string): DurableObjectStub | null {
  const ns = env.USAGE_COUNTER as DurableObjectNamespace | undefined;
  return ns ? ns.get(ns.idFromName(account)) : null;
}

/** Adds one request to the account's count for `day`. No-op without the binding. */
export async function countRequest(env: Env, account: string, day: string): Promise<void> {
  const stub = counter(env, account);
  if (!stub) return;
  const res = await stub.fetch("https://usage-counter/increment", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ day, n: 1 }),
  });
  if (!res.ok) throw new Error(`UsageCounter increment failed: ${res.status}`);
}

export async function dayUsage(env: Env, account: string, day: string): Promise<DayUsage | null> {
  const stub = counter(env, account);
  if (!stub) return null;
  const res = await stub.fetch(`https://usage-counter/usage?day=${day}`);
  if (!res.ok) throw new Error(`UsageCounter read failed: ${res.status}`);
  return (await res.json()) as DayUsage;
}

/** Every retained day of an account, oldest first. Empty without the binding. */
export async function listUsage(env: Env, account: string): Promise<DayUsage[]> {
  const stub = counter(env, account);
  if (!stub) return [];
  const res = await stub.fetch("https://usage-counter/usage");
  if (!res.ok) throw new Error(`UsageCounter read failed: ${res.status}`);
  return ((await res.json()) as { days: DayUsage[] }).days;
}

export async function markReported(env: Env, account: string, day: string): Promise<void> {
  const stub = counter(env, account);
  if (!stub) return;
  const res = await stub.fetch("https://usage-counter/reported", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ day }),
  });
  if (!res.ok) throw new Error(`UsageCounter mark failed: ${res.status}`);
}

export interface MeterPoint {
  site: string;
  plan: string;
  account: string | null;
  method: string;
  path: string;
  status: number;
  bytes: number;
  colo: string;
}

/** One Analytics Engine data point. No-op without the binding. Never includes credentials. */
export function writeDataPoint(env: Env, p: MeterPoint): void {
  const ds = env.USAGE as AnalyticsEngineDataset | undefined;
  if (!ds) return;
  ds.writeDataPoint({
    indexes: [p.account ?? `anon:${p.site}`],
    blobs: [p.site, p.plan, p.account ?? "", p.method, p.path.slice(0, 512), String(p.status), p.colo],
    doubles: [1, p.bytes, p.status],
  });
}
