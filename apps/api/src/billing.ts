// Stripe usage billing. Inert unless the secrets are set:
//   STRIPE_WEBHOOK_SECRET  enables POST /billing/webhook (plan changes from Checkout and subscriptions)
//   STRIPE_SECRET_KEY      enables the nightly report of each account's previous-day usage to Billing Meter Events
//
// Signature scheme (https://docs.stripe.com/webhooks, "Verify webhook signatures manually"):
//   Stripe-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>[,v1=...][,v0=...]
//   Only v1 counts (ignore other schemes against downgrades); several v1 values appear while a secret is
//   being rolled; compare in constant time; reject timestamps outside the tolerance (Stripe's default 300 s).

import type { Context } from "hono";
import { problem } from "./http";
import {
  accountKey,
  ACCOUNT_PATTERN,
  customerKey,
  isPaidPlan,
  type AccountRecord,
  type PaidPlan,
} from "./keys";
import type { AppEnv } from "./middleware";
import { listUsage, markReported, utcDay, type DayUsage } from "./usage";

/** The daily cron in wrangler.jsonc that reports usage to Stripe; every other cron is the freshness check. */
export const BILLING_CRON = "7 0 * * *";
export const SIGNATURE_TOLERANCE_S = 300;
const MAX_BODY_BYTES = 1_000_000;
export const METER_EVENTS_URL = "https://api.stripe.com/v1/billing/meter_events";

const encoder = new TextEncoder();

function hexToBytes(hex: string): Uint8Array | null {
  if (hex.length % 2 !== 0 || !/^[0-9a-f]*$/i.test(hex)) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/** Constant time for equal-length inputs; the length of an HMAC is not secret. */
export function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.byteLength !== b.byteLength) return false;
  let diff = 0;
  for (let i = 0; i < a.byteLength; i++) diff |= a[i]! ^ b[i]!;
  return diff === 0;
}

async function hmacSha256(secret: string, message: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
}

/** Hex v1 signature for a payload; exported so tests (and local tooling) can produce known-good headers. */
export async function signPayload(secret: string, timestamp: number, payload: string): Promise<string> {
  const mac = await hmacSha256(secret, `${timestamp}.${payload}`);
  return [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export type SignatureResult = { ok: true; timestamp: number } | { ok: false; reason: string };

export async function verifyStripeSignature(
  payload: string,
  header: string | null | undefined,
  secret: string,
  nowS = Math.floor(Date.now() / 1000),
  toleranceS = SIGNATURE_TOLERANCE_S,
): Promise<SignatureResult> {
  if (!header) return { ok: false, reason: "missing Stripe-Signature header" };
  let timestamp: number | null = null;
  const signatures: Uint8Array[] = [];
  for (const part of header.split(",")) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    const name = part.slice(0, eq).trim();
    const value = part.slice(eq + 1).trim();
    if (name === "t" && /^\d{1,12}$/.test(value)) timestamp = Number(value);
    else if (name === "v1") {
      const bytes = hexToBytes(value);
      if (bytes) signatures.push(bytes);
    }
  }
  if (timestamp === null) return { ok: false, reason: "no timestamp in Stripe-Signature" };
  if (signatures.length === 0) return { ok: false, reason: "no v1 signature in Stripe-Signature" };

  const expected = await hmacSha256(secret, `${timestamp}.${payload}`);
  let match = false;
  for (const sig of signatures) match = timingSafeEqual(expected, sig) || match; // no early exit
  if (!match) return { ok: false, reason: "signature mismatch" };
  if (Math.abs(nowS - timestamp) > toleranceS) return { ok: false, reason: "timestamp outside the tolerance" };
  return { ok: true, timestamp };
}

// ---- events ------------------------------------------------------------------------------------------

interface StripeEvent {
  id: string;
  type: string;
  created: number;
  data: { object: Record<string, unknown> };
}

const ACTIVE_SUBSCRIPTION = new Set(["active", "trialing", "past_due"]);

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** `customer` is an id string, or an expanded object with an id. */
function idOf(value: unknown): string | null {
  if (typeof value === "string") return str(value);
  if (value && typeof value === "object") return str((value as { id?: unknown }).id);
  return null;
}

function metadata(obj: Record<string, unknown>): Record<string, unknown> {
  const m = obj.metadata;
  return m && typeof m === "object" ? (m as Record<string, unknown>) : {};
}

function planFrom(obj: Record<string, unknown>, fallback: PaidPlan): PaidPlan {
  const p = metadata(obj).plan;
  return isPaidPlan(p) ? p : fallback;
}

function validAccount(value: unknown): string | null {
  const s = str(value);
  return s && ACCOUNT_PATTERN.test(s) ? s : null;
}

export type EventOutcome = { applied: boolean; account?: string; plan?: PaidPlan; reason?: string };

async function writeAccount(kv: KVNamespace, record: AccountRecord): Promise<void> {
  await kv.put(accountKey(record.account), JSON.stringify(record), {
    metadata: { plan: record.plan, status: record.status, stripe_customer: record.stripe_customer ?? null },
  });
}

async function updateAccount(
  kv: KVNamespace,
  event: StripeEvent,
  account: string,
  change: Pick<AccountRecord, "plan"> & Partial<AccountRecord>,
): Promise<EventOutcome> {
  const existing = await kv.get<AccountRecord>(accountKey(account), "json");
  if (existing?.stripe_event_created !== undefined && event.created < existing.stripe_event_created) {
    return { applied: false, account, reason: "older than the last applied event" };
  }
  const record: AccountRecord = {
    account,
    status: existing?.status ?? "active",
    stripe_customer: existing?.stripe_customer ?? null,
    stripe_subscription: existing?.stripe_subscription ?? null,
    ...change,
    stripe_event_created: event.created,
    updated_at: new Date(event.created * 1000).toISOString(),
  };
  await writeAccount(kv, record);
  return { applied: true, account, plan: record.plan };
}

/**
 * Applies one verified event to KV:
 *   checkout.session.completed      -> acct:<client_reference_id or metadata.account> = pro (or metadata.plan),
 *                                      and cust:<customer> -> account for later subscription events
 *   customer.subscription.updated   -> pro while active/trialing/past_due, free otherwise
 *   customer.subscription.deleted   -> free
 * Checkout must be created with client_reference_id (or metadata.account) set to our account id.
 */
export async function applyEvent(kv: KVNamespace, event: StripeEvent): Promise<EventOutcome> {
  const obj = event.data?.object ?? {};
  switch (event.type) {
    case "checkout.session.completed": {
      const account = validAccount(obj.client_reference_id) ?? validAccount(metadata(obj).account);
      if (!account) return { applied: false, reason: "checkout session has no account reference" };
      if (obj.mode !== undefined && obj.mode !== "subscription") return { applied: false, account, reason: `mode ${String(obj.mode)}` };
      const customer = idOf(obj.customer);
      if (customer) await kv.put(customerKey(customer), account);
      return updateAccount(kv, event, account, {
        plan: planFrom(obj, "pro"),
        ...(customer ? { stripe_customer: customer } : {}),
        ...(idOf(obj.subscription) ? { stripe_subscription: idOf(obj.subscription) } : {}),
      });
    }
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const customer = idOf(obj.customer);
      const account =
        validAccount(metadata(obj).account) ?? (customer ? validAccount(await kv.get(customerKey(customer))) : null);
      if (!account) return { applied: false, reason: "subscription is not linked to an account" };
      const active = event.type === "customer.subscription.updated" && ACTIVE_SUBSCRIPTION.has(String(obj.status));
      return updateAccount(kv, event, account, {
        plan: active ? planFrom(obj, "pro") : "free",
        ...(customer ? { stripe_customer: customer } : {}),
        stripe_subscription: active ? idOf(obj.id) : null,
      });
    }
    default:
      return { applied: false, reason: `ignored event type ${event.type}` };
  }
}

export async function webhook(c: Context<AppEnv>): Promise<Response> {
  const secret = c.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) return problem(c, 404, "not-found", "Not found", "No such endpoint.", { hint: "GET /v1" });
  const kv = c.env.KEYS as KVNamespace | undefined;
  if (!kv) return problem(c, 503, "billing-unavailable", "Billing unavailable", "The account store is not configured.");

  const declared = Number(c.req.header("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return problem(c, 413, "payload-too-large", "Payload too large", "Webhook bodies are limited to 1 MB.");
  const raw = await c.req.text();
  if (encoder.encode(raw).byteLength > MAX_BODY_BYTES) {
    return problem(c, 413, "payload-too-large", "Payload too large", "Webhook bodies are limited to 1 MB.");
  }

  const verdict = await verifyStripeSignature(raw, c.req.header("stripe-signature"), secret);
  if (!verdict.ok) return problem(c, 400, "invalid-signature", "Invalid signature", `Webhook signature rejected: ${verdict.reason}.`);

  let event: StripeEvent;
  try {
    event = JSON.parse(raw) as StripeEvent;
  } catch {
    return problem(c, 400, "invalid-payload", "Invalid payload", "The webhook body is not JSON.");
  }
  if (typeof event?.type !== "string" || typeof event.created !== "number") {
    return problem(c, 400, "invalid-payload", "Invalid payload", "The webhook body is not a Stripe event.");
  }

  const outcome = await applyEvent(kv, event);
  console.log(`stripe ${event.type} ${event.id}: ${outcome.applied ? `applied ${outcome.account} -> ${outcome.plan}` : outcome.reason}`);
  return new Response(JSON.stringify({ received: true, applied: outcome.applied }), {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

// ---- nightly meter reporting -------------------------------------------------------------------------

export interface ReportSummary {
  /** The UTC day the run treated as "today"; every earlier unreported day within CATCH_UP_DAYS is sent. */
  today: string;
  reported: number;
  skipped: number;
  failed: number;
}

interface AccountMeta {
  plan?: string;
  stripe_customer?: string | null;
}

/** Days before today that the cron may still report; Stripe accepts meter events up to 35 days old. */
export const CATCH_UP_DAYS = 30;

/**
 * Sends each Stripe-linked account's daily usage to Billing Meter Events (POST /v1/billing/meter_events,
 * form-encoded: event_name, payload[stripe_customer_id], payload[value], identifier, timestamp), one event per
 * account per finished UTC day. Normally that is just yesterday; days a failed run missed are caught up for
 * CATCH_UP_DAYS. The identifier `<account>:<day>` and the Idempotency-Key make a retry safe, and the counter
 * remembers which days were sent, so a rerun never double-bills.
 * Every account with a Stripe customer is reported: Stripe bills an event only if the customer has a
 * subscription price attached to the meter, so a cancelled customer's events cost nothing.
 */
export async function reportUsage(env: Env, nowMs = Date.now()): Promise<ReportSummary | null> {
  const secretKey = env.STRIPE_SECRET_KEY;
  const kv = env.KEYS as KVNamespace | undefined;
  if (!secretKey || !kv || !env.USAGE_COUNTER) return null;

  const today = utcDay(nowMs);
  const oldest = utcDay(nowMs - CATCH_UP_DAYS * 86_400_000);
  const eventName = env.STRIPE_METER_EVENT || "api_requests";
  const summary: ReportSummary = { today, reported: 0, skipped: 0, failed: 0 };

  let cursor: string | undefined;
  do {
    const page = await kv.list<AccountMeta>({ prefix: "acct:", cursor });
    for (const { name, metadata } of page.keys) {
      const account = name.slice("acct:".length);
      const customer = metadata
        ? (metadata.stripe_customer ?? null)
        : ((await kv.get<AccountRecord>(name, "json"))?.stripe_customer ?? null);
      if (!customer) {
        summary.skipped++;
        continue;
      }
      let days: DayUsage[];
      try {
        days = await listUsage(env, account);
      } catch (err) {
        summary.failed++;
        console.error(`usage read for ${account} failed: ${err instanceof Error ? err.message : String(err)}`);
        continue;
      }
      const due = days.filter((d) => d.day < today && d.day >= oldest && d.count > 0 && !d.reported);
      if (due.length === 0) summary.skipped++;
      for (const usage of due) {
        const identifier = `${account}:${usage.day}`;
        try {
          const res = await fetch(METER_EVENTS_URL, {
            method: "POST",
            headers: {
              authorization: `Bearer ${secretKey}`,
              "content-type": "application/x-www-form-urlencoded",
              "idempotency-key": identifier,
            },
            body: new URLSearchParams({
              event_name: eventName,
              "payload[stripe_customer_id]": customer,
              "payload[value]": String(usage.count),
              identifier,
              timestamp: String(Math.floor(Date.parse(`${usage.day}T23:59:59Z`) / 1000)),
            }),
          });
          if (!res.ok) {
            summary.failed++;
            console.error(`stripe meter event ${identifier} failed: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
            continue;
          }
          await markReported(env, account, usage.day);
          summary.reported++;
        } catch (err) {
          summary.failed++;
          console.error(`stripe meter event ${identifier} failed: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return summary;
}
