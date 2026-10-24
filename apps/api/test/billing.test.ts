import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, { app } from "../src/index";
import { BILLING_CRON, METER_EVENTS_URL, reportUsage, signPayload, timingSafeEqual, verifyStripeSignature } from "../src/billing";
import { accountKey, clearKeyCache, customerKey, generateKey, hashKey } from "../src/keys";
import { clearMemo } from "../src/store";
import { FakeContext, FakeCounterNamespace, FakeKV } from "./fakes";
import { seededBucket } from "./fixtures";

// A made-up test secret; this code never talks to a real Stripe account.
const SECRET = "whsec_test_0123456789abcdefghijklmnopqrstuv";
const NOW = 1_792_800_000; // 2026-10-24T...Z, seconds

/**
 * Known-good vector computed outside this codebase (Python: hmac.new(SECRET, f"{t}.{payload}", sha256).hexdigest()),
 * following Stripe's documented scheme. It pins signPayload, which the other tests then use to sign deliveries.
 */
const KNOWN_PAYLOAD = '{"id":"evt_test_1","type":"customer.subscription.updated","created":1792800000}';
const KNOWN_SIGNATURE = "87f001eed7e364a0702e0b9831b49b4965c5b782b22570d871b1d3972b00e14b";

const sign = (secret: string, t: number, payload: string) => signPayload(secret, t, payload);

describe("verifyStripeSignature", () => {
  const payload = KNOWN_PAYLOAD;

  it("accepts a known-good signature", async () => {
    const header = `t=${NOW},v1=${KNOWN_SIGNATURE}`;
    expect(await verifyStripeSignature(payload, header, SECRET, NOW)).toEqual({ ok: true, timestamp: NOW });
    expect(await signPayload(SECRET, NOW, payload)).toBe(KNOWN_SIGNATURE);
  });

  it("accepts any matching v1 while a secret is being rolled, and ignores v0", async () => {
    const good = await sign(SECRET, NOW, payload);
    const other = await sign("whsec_old", NOW, payload);
    const header = `t=${NOW},v1=${other},v1=${good},v0=${"0".repeat(64)}`;
    expect((await verifyStripeSignature(payload, header, SECRET, NOW)).ok).toBe(true);
  });

  it("rejects a tampered payload", async () => {
    const header = `t=${NOW},v1=${await sign(SECRET, NOW, payload)}`;
    const tampered = payload.replace("updated", "deleted");
    expect(await verifyStripeSignature(tampered, header, SECRET, NOW)).toEqual({ ok: false, reason: "signature mismatch" });
  });

  it("rejects a tampered timestamp (it is part of the signed payload)", async () => {
    const header = `t=${NOW + 1},v1=${await sign(SECRET, NOW, payload)}`;
    expect((await verifyStripeSignature(payload, header, SECRET, NOW)).ok).toBe(false);
  });

  it("rejects an old timestamp even with a valid signature, and one too far in the future", async () => {
    const old = NOW - 301;
    const oldHeader = `t=${old},v1=${await sign(SECRET, old, payload)}`;
    expect(await verifyStripeSignature(payload, oldHeader, SECRET, NOW)).toEqual({ ok: false, reason: "timestamp outside the tolerance" });
    const edge = NOW - 300;
    expect((await verifyStripeSignature(payload, `t=${edge},v1=${await sign(SECRET, edge, payload)}`, SECRET, NOW)).ok).toBe(true);
    const future = NOW + 301;
    expect((await verifyStripeSignature(payload, `t=${future},v1=${await sign(SECRET, future, payload)}`, SECRET, NOW)).ok).toBe(false);
  });

  it("rejects the wrong secret, a v0-only header, and malformed headers", async () => {
    const sig = await sign(SECRET, NOW, payload);
    expect((await verifyStripeSignature(payload, `t=${NOW},v1=${sig}`, "whsec_other", NOW)).ok).toBe(false);
    expect((await verifyStripeSignature(payload, `t=${NOW},v0=${sig}`, SECRET, NOW)).ok).toBe(false);
    expect((await verifyStripeSignature(payload, `v1=${sig}`, SECRET, NOW)).ok).toBe(false);
    expect((await verifyStripeSignature(payload, null, SECRET, NOW)).ok).toBe(false);
    expect((await verifyStripeSignature(payload, `t=${NOW},v1=zz`, SECRET, NOW)).ok).toBe(false);
  });

  it("compares in constant time over equal lengths", () => {
    expect(timingSafeEqual(new Uint8Array([1, 2, 3]), new Uint8Array([1, 2, 3]))).toBe(true);
    expect(timingSafeEqual(new Uint8Array([1, 2, 3]), new Uint8Array([1, 2, 4]))).toBe(false);
    expect(timingSafeEqual(new Uint8Array([1, 2]), new Uint8Array([1, 2, 3]))).toBe(false);
  });
});

describe("POST /billing/webhook", () => {
  let kv: FakeKV;
  beforeEach(() => {
    kv = new FakeKV();
    clearKeyCache();
    clearMemo();
    vi.useFakeTimers({ now: NOW * 1000, toFake: ["Date"] });
  });
  afterEach(() => vi.useRealTimers());

  const env = (extra: Record<string, unknown> = {}) =>
    ({ DATA: seededBucket().asBucket(), ENV: "production", KEYS: kv.asKV(), STRIPE_WEBHOOK_SECRET: SECRET, ...extra }) as unknown as Env;

  async function deliver(event: Record<string, unknown>, opts: { secret?: string; t?: number; env?: Env } = {}) {
    const body = JSON.stringify(event);
    const t = opts.t ?? NOW;
    return app.request(
      "https://api.climatedata.dev/billing/webhook",
      { method: "POST", body, headers: { "content-type": "application/json", "stripe-signature": `t=${t},v1=${await sign(opts.secret ?? SECRET, t, body)}` } },
      opts.env ?? env(),
    );
  }

  const checkout = (created = NOW) => ({
    id: "evt_checkout",
    type: "checkout.session.completed",
    created,
    data: { object: { object: "checkout.session", mode: "subscription", client_reference_id: "acme", customer: "cus_test_1", subscription: "sub_test_1" } },
  });
  const subscription = (type: string, status: string, created: number) => ({
    id: `evt_${type}_${created}`,
    type,
    created,
    data: { object: { object: "subscription", id: "sub_test_1", customer: "cus_test_1", status } },
  });

  it("is inert (404) without a webhook secret", async () => {
    const res = await app.request("https://api.climatedata.dev/billing/webhook", { method: "POST", body: "{}" }, env({ STRIPE_WEBHOOK_SECRET: undefined }));
    expect(res.status).toBe(404);
  });

  it("rejects a bad signature with 400 and changes nothing", async () => {
    const res = await deliver(checkout(), { secret: "whsec_wrong" });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { type: string }).type).toMatch(/#invalid-signature$/);
    expect(kv.values.size).toBe(0);
  });

  it("rejects a replay of an old delivery", async () => {
    expect((await deliver(checkout(), { t: NOW - 3600 })).status).toBe(400);
  });

  it("upgrades the account on checkout, and its keys pick up the plan", async () => {
    const key = generateKey();
    await kv.put(await hashKey(key), JSON.stringify({ account: "acme", plan: "free", status: "active", created_at: "" }));
    const res = await deliver(checkout());
    expect(res.status).toBe(200);
    expect(kv.json(accountKey("acme"))).toMatchObject({ plan: "pro", stripe_customer: "cus_test_1", stripe_subscription: "sub_test_1", status: "active" });
    expect(kv.values.get(customerKey("cus_test_1"))?.value).toBe("acme");
    expect(kv.values.get(accountKey("acme"))?.metadata).toMatchObject({ stripe_customer: "cus_test_1", plan: "pro" });

    const api = await app.request("https://api.climatedata.dev/v1", { headers: { "x-api-key": key } }, env());
    expect(api.headers.get("x-plan")).toBe("pro");
  });

  it("downgrades on cancellation and deletion, and ignores out-of-order older events", async () => {
    await deliver(checkout(NOW - 100));
    await deliver(subscription("customer.subscription.updated", "canceled", NOW - 50));
    expect(kv.json(accountKey("acme"))).toMatchObject({ plan: "free" });
    await deliver(subscription("customer.subscription.updated", "active", NOW - 40));
    expect(kv.json(accountKey("acme"))).toMatchObject({ plan: "pro" });
    await deliver(subscription("customer.subscription.deleted", "canceled", NOW - 10));
    expect(kv.json(accountKey("acme"))).toMatchObject({ plan: "free", stripe_subscription: null });
    // A delayed delivery of an earlier "active" update must not resurrect the plan.
    const late = await deliver(subscription("customer.subscription.updated", "active", NOW - 30));
    expect(late.status).toBe(200);
    expect(kv.json(accountKey("acme"))).toMatchObject({ plan: "free" });
  });

  it("acknowledges unrelated or unlinked events without changing anything", async () => {
    expect((await deliver({ id: "evt_x", type: "invoice.paid", created: NOW, data: { object: {} } })).status).toBe(200);
    expect((await deliver(subscription("customer.subscription.updated", "active", NOW))).status).toBe(200);
    expect(kv.values.size).toBe(0);
  });

  it("is not behind the API rate limiter or key check", async () => {
    const limiter = { limit: vi.fn(async () => ({ success: false })) };
    const res = await deliver(checkout(), { env: env({ RL_ANON: limiter }) });
    expect(res.status).toBe(200);
    expect(limiter.limit).not.toHaveBeenCalled();
  });
});

describe("reportUsage (nightly meter events)", () => {
  let kv: FakeKV;
  let counters: FakeCounterNamespace;
  const now = Date.parse("2026-10-24T00:07:00Z");

  beforeEach(async () => {
    kv = new FakeKV();
    counters = new FakeCounterNamespace();
    const linked = { account: "acme", plan: "pro", status: "active", stripe_customer: "cus_test_1", updated_at: "" };
    await kv.put(accountKey("acme"), JSON.stringify(linked), { metadata: { plan: "pro", status: "active", stripe_customer: "cus_test_1" } });
    await kv.put(accountKey("solo"), JSON.stringify({ account: "solo", plan: "free", status: "active", updated_at: "" }), {
      metadata: { plan: "free", status: "active", stripe_customer: null },
    });
    const bump = (account: string, day: string, n: number) =>
      counters.get(counters.idFromName(account)).fetch("https://usage-counter/increment", { method: "POST", body: JSON.stringify({ day, n }) });
    await bump("acme", "2026-10-23", 1234);
    await bump("acme", "2026-10-21", 7); // a day an earlier run missed
    await bump("acme", "2026-10-24", 5); // today: not finished, not reported
    await bump("solo", "2026-10-23", 50); // no Stripe customer
  });
  afterEach(() => vi.unstubAllGlobals());

  const env = (extra: Record<string, unknown> = {}) =>
    ({ KEYS: kv.asKV(), USAGE_COUNTER: counters.asBinding(), STRIPE_SECRET_KEY: "sk_test_fake", STRIPE_METER_EVENT: "api_requests", ...extra }) as unknown as Env;

  it("does nothing without STRIPE_SECRET_KEY", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(await reportUsage(env({ STRIPE_SECRET_KEY: undefined }), now)).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts one meter event per finished unreported day, then never again", async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => Response.json({ object: "billing.meter_event" }));
    vi.stubGlobal("fetch", fetchMock);

    const summary = await reportUsage(env(), now);
    expect(summary).toMatchObject({ today: "2026-10-24", reported: 2, failed: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe(METER_EVENTS_URL);
    expect(url).toBe("https://api.stripe.com/v1/billing/meter_events");
    expect(init.method).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("authorization")).toBe("Bearer sk_test_fake");
    expect(headers.get("content-type")).toBe("application/x-www-form-urlencoded");
    expect(headers.get("idempotency-key")).toBe("acme:2026-10-23");
    const form = new URLSearchParams(String(init.body));
    expect(Object.fromEntries(form)).toEqual({
      event_name: "api_requests",
      "payload[stripe_customer_id]": "cus_test_1",
      "payload[value]": "1234",
      identifier: "acme:2026-10-23",
      timestamp: String(Date.parse("2026-10-23T23:59:59Z") / 1000),
    });
    expect(new URLSearchParams(String(fetchMock.mock.calls[0]![1].body)).get("payload[value]")).toBe("7");

    fetchMock.mockClear();
    expect(await reportUsage(env(), now)).toMatchObject({ reported: 0 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves a day unreported when Stripe refuses it, so the next run retries", async () => {
    const fetchMock = vi.fn(async () => new Response('{"error":{"message":"No such customer"}}', { status: 400 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(await reportUsage(env(), now)).toMatchObject({ reported: 0, failed: 2 });
    const ok = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", ok);
    expect(await reportUsage(env(), now)).toMatchObject({ reported: 2 });
  });

  it("runs from the daily cron, and the 15-minute cron stays the freshness check", async () => {
    const fetchMock = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers({ now, toFake: ["Date"] });
    try {
      const ctx = new FakeContext();
      await worker.scheduled({ cron: BILLING_CRON, scheduledTime: now } as ScheduledController, env(), ctx.asContext());
      await ctx.settle();
      expect(fetchMock.mock.calls.map((c) => (c as unknown[])[0])).toEqual([METER_EVENTS_URL, METER_EVENTS_URL]);
    } finally {
      vi.useRealTimers();
    }
  });
});
