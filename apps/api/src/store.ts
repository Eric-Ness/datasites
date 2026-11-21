// JSON reads from R2 with a small per-isolate memo, so hot requests skip R2 entirely.

const MAX_ENTRIES = 500;
const MISSING_TTL_MS = 30_000;

export const TTL = {
  manifest: 30_000,
  catalog: 60_000,
  state: 15_000,
  immutable: Number.POSITIVE_INFINITY,
} as const;

interface Memo {
  value: unknown;
  expires: number;
}

const memo = new Map<string, Memo>();

/**
 * Waits before each retry of a failed R2 read. R2 sometimes answers "We encountered an internal error. Please try
 * again. (10001)" for a moment: on 2026-11-20 at 13:00 UTC six sites' reads failed at once and the freshness check
 * paged, then the next run 15 minutes later was fine. A missing object (null) is an answer, not an error, and isn't
 * retried. If every try fails, the last error is thrown as before.
 */
export const R2_RETRY_DELAYS_MS = [250, 1_000];

async function getJsonWithRetry<T>(bucket: R2Bucket, key: string): Promise<T | null> {
  for (let attempt = 0; ; attempt++) {
    try {
      const obj = await bucket.get(key);
      return obj ? ((await obj.json()) as T) : null;
    } catch (err) {
      const delay = R2_RETRY_DELAYS_MS[attempt];
      if (delay === undefined) throw err;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
}

export async function readJson<T>(bucket: R2Bucket, key: string, ttlMs: number): Promise<T | null> {
  const now = Date.now();
  const hit = memo.get(key);
  if (hit && hit.expires > now) return hit.value as T | null;

  const value = await getJsonWithRetry<T>(bucket, key);

  if (memo.size >= MAX_ENTRIES) {
    const oldest = memo.keys().next().value;
    if (oldest !== undefined) memo.delete(oldest);
  }
  // Never cache a missing object forever: it may be published a minute from now.
  memo.set(key, { value, expires: now + (value === null ? Math.min(ttlMs, MISSING_TTL_MS) : ttlMs) });
  return value;
}

/** Test hook: forget everything memoized in this isolate. */
export function clearMemo(): void {
  memo.clear();
}
