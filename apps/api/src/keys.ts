// API keys: dk_live_ + 32 base32 characters (160 random bits). KV holds only the SHA-256 of a key.
//
// KV layout (binding KEYS):
//   <sha256 hex of the key>   -> KeyRecord      written by scripts/keys.mjs
//   acct:<account>            -> AccountRecord  written by the Stripe webhook or `keys.mjs set-plan`
//   cust:<stripe customer id> -> account id     written by the Stripe webhook (subscription events carry only the customer)
//
// The account record, when present, overrides the plan stored on the key, so a plan change never has to
// rewrite every key of the account.

export type Plan = "anonymous" | "free" | "pro";
export type PaidPlan = Exclude<Plan, "anonymous">;

export const PLANS: readonly PaidPlan[] = ["free", "pro"];
export const KEY_PATTERN = /^dk_live_[a-z2-7]{32}$/;
export const ACCOUNT_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export interface KeyRecord {
  account: string;
  plan: PaidPlan;
  status: "active" | "revoked";
  created_at: string;
  revoked_at?: string;
}

export interface AccountRecord {
  account: string;
  plan: PaidPlan;
  /** "suspended" blocks every key of the account with 403. */
  status: "active" | "suspended";
  stripe_customer?: string | null;
  stripe_subscription?: string | null;
  /** `created` of the last Stripe event applied, so out-of-order deliveries can't roll a plan back. */
  stripe_event_created?: number;
  updated_at: string;
}

export interface Principal {
  account: string;
  plan: PaidPlan;
  status: "active" | "suspended";
}

export const accountKey = (account: string) => `acct:${account}`;
export const customerKey = (customer: string) => `cust:${customer}`;

const BASE32 = "abcdefghijklmnopqrstuvwxyz234567";

/** A new key. 20 random bytes = 160 bits = exactly 32 base32 characters. */
export function generateKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(20));
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

export async function hashKey(key: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function isPaidPlan(value: unknown): value is PaidPlan {
  return value === "free" || value === "pro";
}

/** The key from `Authorization: Bearer <key>` or `X-API-Key`. undefined = no credentials; "" = malformed or conflicting. */
export function presentedKey(headers: Headers): string | undefined {
  const auth = headers.get("authorization");
  const header = headers.get("x-api-key");
  let bearer: string | undefined;
  if (auth !== null) {
    const m = /^Bearer[ \t]+(\S+)[ \t]*$/i.exec(auth);
    bearer = m ? m[1]! : "";
  }
  const api = header === null ? undefined : header.trim();
  if (bearer !== undefined && api !== undefined && bearer !== api) return "";
  return bearer ?? api;
}

// ---- isolate-memory cache, so a hot key costs no KV read -----------------------------------------------

export const LOOKUP_TTL_MS = 5 * 60_000;
const MAX_ENTRIES = 2000;

interface Cached<T> {
  value: T;
  expires: number;
}
const cache = new Map<string, Cached<unknown>>();

function remember<T>(name: string, value: T, now: number): T {
  if (cache.size >= MAX_ENTRIES) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  cache.set(name, { value, expires: now + LOOKUP_TTL_MS });
  return value;
}

function recall<T>(name: string, now: number): { hit: true; value: T } | { hit: false } {
  const entry = cache.get(name);
  if (!entry) return { hit: false };
  if (entry.expires <= now) {
    cache.delete(name);
    return { hit: false };
  }
  return { hit: true, value: entry.value as T };
}

/** Test hook. */
export function clearKeyCache(): void {
  cache.clear();
}

/** Is the lookup for this key hash already in isolate memory? (Lets the caller skip the guard on cache hits.) */
export function isCached(hash: string, now = Date.now()): boolean {
  return recall(`k:${hash}`, now).hit;
}

/**
 * Who a key belongs to, or null for an unknown or revoked key. Both outcomes are cached for LOOKUP_TTL_MS,
 * so a revocation takes effect within five minutes. KV errors propagate and are not cached.
 */
export async function lookupKey(kv: KVNamespace, hash: string, now = Date.now()): Promise<Principal | null> {
  const name = `k:${hash}`;
  const cached = recall<Principal | null>(name, now);
  if (cached.hit) return cached.value;

  const record = await kv.get<KeyRecord>(hash, "json");
  if (!record || record.status !== "active" || typeof record.account !== "string") return remember(name, null, now);

  const account = await kv.get<AccountRecord>(accountKey(record.account), "json");
  const principal: Principal = {
    account: record.account,
    plan: isPaidPlan(account?.plan) ? account.plan : isPaidPlan(record.plan) ? record.plan : "free",
    status: account?.status === "suspended" ? "suspended" : "active",
  };
  return remember(name, principal, now);
}
