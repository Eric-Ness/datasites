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

export async function readJson<T>(bucket: R2Bucket, key: string, ttlMs: number): Promise<T | null> {
  const now = Date.now();
  const hit = memo.get(key);
  if (hit && hit.expires > now) return hit.value as T | null;

  const obj = await bucket.get(key);
  const value = obj ? ((await obj.json()) as T) : null;

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
