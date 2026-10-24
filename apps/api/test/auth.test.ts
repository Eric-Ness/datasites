import { beforeEach, describe, expect, it } from "vitest";
import { app } from "../src/index";
import { accountKey, clearKeyCache, generateKey, hashKey, KEY_PATTERN, presentedKey } from "../src/keys";
import { clearMemo } from "../src/store";
import { utcDay } from "../src/usage";
import { FakeAnalytics, FakeContext, FakeCounterNamespace, FakeKV, FakeLimiter, FakeStorage } from "./fakes";
import { seededBucket } from "./fixtures";
import { UsageCounter } from "../src/usage";

let kv: FakeKV;
let env: Record<string, unknown>;

function request(path: string, init: RequestInit = {}, ctx?: FakeContext) {
  const url = `http://localhost${path}${path.includes("?") ? "&" : "?"}site=climatedata`;
  return app.request(url, init, { DATA: seededBucket().asBucket(), ENV: "production", KEYS: kv.asKV(), ...env } as unknown as Env, ctx?.asContext());
}

async function issue(account: string, plan: "free" | "pro" = "free", status = "active"): Promise<string> {
  const key = generateKey();
  await kv.put(await hashKey(key), JSON.stringify({ account, plan, status, created_at: "2026-10-24T00:00:00Z" }));
  return key;
}

beforeEach(() => {
  clearMemo();
  clearKeyCache();
  kv = new FakeKV();
  env = {};
});

describe("API key format", () => {
  it("generates dk_live_ plus 32 base32 characters, never the same twice", () => {
    const keys = new Set(Array.from({ length: 200 }, generateKey));
    expect(keys.size).toBe(200);
    for (const key of keys) expect(key).toMatch(KEY_PATTERN);
  });

  it("hashes keys with SHA-256 (hex)", async () => {
    // sha256("abc") from FIPS 180-2
    expect(await hashKey("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("reads Bearer and X-API-Key, and flags malformed or conflicting credentials", () => {
    const h = (init: Record<string, string>) => presentedKey(new Headers(init));
    expect(h({})).toBeUndefined();
    expect(h({ authorization: "Bearer abc" })).toBe("abc");
    expect(h({ authorization: "bearer   abc " })).toBe("abc");
    expect(h({ "x-api-key": " abc " })).toBe("abc");
    expect(h({ authorization: "Bearer abc", "x-api-key": "abc" })).toBe("abc");
    expect(h({ authorization: "Bearer abc", "x-api-key": "xyz" })).toBe("");
    expect(h({ authorization: "Basic dXNlcjpwYXNz" })).toBe("");
  });
});

describe("authenticate", () => {
  it("serves anonymous requests with X-Plan: anonymous", async () => {
    const res = await request("/v1");
    expect(res.status).toBe(200);
    expect(res.headers.get("x-plan")).toBe("anonymous");
    expect(kv.reads).toBe(0);
  });

  it("stays anonymous-only when no KEYS binding exists", async () => {
    const res = await app.request("http://localhost/v1?site=climatedata", { headers: { "x-api-key": "dk_live_whatever" } }, {
      DATA: seededBucket().asBucket(),
      ENV: "production",
    } as unknown as Env);
    expect(res.status).toBe(200);
    expect(res.headers.get("x-plan")).toBe("anonymous");
  });

  it("accepts a key by Bearer or X-API-Key and reports its plan", async () => {
    const key = await issue("acme");
    const bearer = await request("/v1", { headers: { authorization: `Bearer ${key}` } });
    expect(bearer.status).toBe(200);
    expect(bearer.headers.get("x-plan")).toBe("free");
    const header = await request("/v1/co2-monthly/average/noaa-mlo.json", { headers: { "x-api-key": key } });
    expect(header.status).toBe(200);
    expect(header.headers.get("x-plan")).toBe("free");
  });

  it("lets the account record override the plan on the key", async () => {
    const key = await issue("acme", "free");
    await kv.put(accountKey("acme"), JSON.stringify({ account: "acme", plan: "pro", status: "active", updated_at: "" }));
    const res = await request("/v1", { headers: { "x-api-key": key } });
    expect(res.headers.get("x-plan")).toBe("pro");
  });

  it.each([
    ["a malformed key", "not-a-key"],
    ["an unknown well-formed key", generateKey()],
  ])("rejects %s with 401 invalid-key", async (_label, key) => {
    const res = await request("/v1", { headers: { authorization: `Bearer ${key}` } });
    expect(res.status).toBe(401);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    expect(res.headers.get("www-authenticate")).toContain("Bearer");
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    expect(res.headers.get("x-plan")).toBe("anonymous");
    const body = (await res.json()) as { type: string; status: number; detail: string };
    expect(body.type).toMatch(/#invalid-key$/);
    expect(body.status).toBe(401);
    expect(JSON.stringify(body)).not.toContain(key);
  });

  it("rejects a revoked key with 401", async () => {
    const key = await issue("acme", "free", "revoked");
    const res = await request("/v1", { headers: { "x-api-key": key } });
    expect(res.status).toBe(401);
  });

  it("rejects conflicting Bearer and X-API-Key with 401", async () => {
    const res = await request("/v1", { headers: { authorization: `Bearer ${await issue("a")}`, "x-api-key": await issue("b") } });
    expect(res.status).toBe(401);
  });

  it("answers a valid key of a suspended account with 403, not 401", async () => {
    const key = await issue("acme");
    await kv.put(accountKey("acme"), JSON.stringify({ account: "acme", plan: "free", status: "suspended", updated_at: "" }));
    const res = await request("/v1", { headers: { "x-api-key": key } });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { type: string }).type).toMatch(/#account-suspended$/);
  });

  it("caches lookups in isolate memory, including misses", async () => {
    const key = await issue("acme");
    await request("/v1", { headers: { "x-api-key": key } });
    const reads = kv.reads;
    for (let i = 0; i < 5; i++) await request("/v1", { headers: { "x-api-key": key } });
    expect(kv.reads).toBe(reads);

    const unknown = generateKey();
    await request("/v1", { headers: { "x-api-key": unknown } });
    const afterMiss = kv.reads;
    expect((await request("/v1", { headers: { "x-api-key": unknown } })).status).toBe(401);
    expect(kv.reads).toBe(afterMiss);
  });

  it("charges uncached lookups to the client IP, so random keys can't flood KV", async () => {
    const anon = new FakeLimiter(false);
    env = { RL_ANON: anon.asBinding() };
    const res = await request("/v1", { headers: { "x-api-key": generateKey(), "cf-connecting-ip": "203.0.113.9" } });
    expect(res.status).toBe(429);
    expect(anon.calls).toEqual(["auth:203.0.113.9"]);
    expect(kv.reads).toBe(0);
  });

  it("answers 503 when the key store fails, and does not cache the failure", async () => {
    const key = await issue("acme");
    kv.failing = true;
    expect((await request("/v1", { headers: { "x-api-key": key } })).status).toBe(503);
    kv.failing = false;
    expect((await request("/v1", { headers: { "x-api-key": key } })).status).toBe(200);
  });

  it("answers CORS preflight for keyed requests without authenticating it", async () => {
    const res = await request("/v1/co2-monthly", {
      method: "OPTIONS",
      headers: { origin: "https://example.org", "access-control-request-headers": "authorization, x-api-key" },
    });
    expect(res.status).toBe(204);
    const allowed = res.headers.get("access-control-allow-headers")!.toLowerCase();
    expect(allowed).toContain("authorization");
    expect(allowed).toContain("x-api-key");
    expect(res.headers.get("access-control-expose-headers")).toContain("X-Plan");
    expect(kv.reads).toBe(0);
  });
});

describe("rate limits per plan", () => {
  it("keys anonymous traffic by IP and keyed traffic by account, on the plan's binding", async () => {
    const anon = new FakeLimiter();
    const free = new FakeLimiter();
    const pro = new FakeLimiter();
    env = { RL_ANON: anon.asBinding(), RL_FREE: free.asBinding(), RL_PRO: pro.asBinding() };
    const freeKey = await issue("acme", "free");
    const proKey = await issue("bigco", "pro");

    await request("/v1", { headers: { "cf-connecting-ip": "198.51.100.7" } });
    await request("/v1", { headers: { "x-api-key": freeKey, "cf-connecting-ip": "198.51.100.7" } });
    await request("/v1", { headers: { "x-api-key": proKey } });

    expect(anon.calls).toEqual(["198.51.100.7", "auth:198.51.100.7", "auth:unknown"]);
    expect(free.calls).toEqual(["acct:acme"]);
    expect(pro.calls).toEqual(["acct:bigco"]);
  });

  it("answers 429 with Retry-After and no made-up RateLimit headers", async () => {
    const free = new FakeLimiter(false);
    env = { RL_FREE: free.asBinding() };
    const res = await request("/v1", { headers: { "x-api-key": await issue("acme") } });
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("60");
    expect(res.headers.get("ratelimit")).toBeNull();
    expect(res.headers.get("ratelimit-policy")).toBeNull();
    expect(res.headers.get("x-plan")).toBe("free");
  });
});

describe("usage metering", () => {
  it("writes one Analytics Engine point per request in waitUntil, without credentials", async () => {
    const usage = new FakeAnalytics();
    env = { USAGE: usage.asBinding() };
    const ctx = new FakeContext();
    const key = await issue("acme");
    await request("/v1/co2-monthly", { headers: { "x-api-key": key } }, ctx);
    expect(ctx.pending.length).toBeGreaterThan(0); // metering was handed to waitUntil
    await ctx.settle();
    expect(usage.points).toHaveLength(1);
    const point = usage.points[0]!;
    expect(point.indexes).toEqual(["acme"]);
    expect(point.blobs?.slice(0, 6)).toEqual(["climatedata", "free", "acme", "GET", "/v1/co2-monthly", "200"]);
    expect(JSON.stringify(point)).not.toContain(key);
  });

  it("counts served keyed requests per account and day, not errors or anonymous traffic", async () => {
    const counters = new FakeCounterNamespace();
    env = { USAGE_COUNTER: counters.asBinding() };
    const ctx = new FakeContext();
    const key = await issue("acme");
    await request("/v1/co2-monthly", { headers: { "x-api-key": key } }, ctx);
    await request("/v1/co2-monthly/average/noaa-mlo.json", { headers: { "x-api-key": key } }, ctx);
    await request("/v1/nope", { headers: { "x-api-key": key } }, ctx); // 404: not billed
    await request("/v1/co2-monthly", {}, ctx); // anonymous: not counted
    await ctx.settle();
    expect([...counters.objects.keys()]).toEqual(["acme"]);
    const today = utcDay(Date.now());
    expect(counters.objects.get("acme")!.storage.data.get(`day:${today}`)).toBe(2);
  });

  it("records rejected requests in analytics but never counts them for billing", async () => {
    const usage = new FakeAnalytics();
    const counters = new FakeCounterNamespace();
    const free = new FakeLimiter(false);
    env = { USAGE: usage.asBinding(), USAGE_COUNTER: counters.asBinding(), RL_FREE: free.asBinding() };
    const ctx = new FakeContext();
    await request("/v1", { headers: { "x-api-key": generateKey() } }, ctx); // 401
    await request("/v1", { headers: { "x-api-key": await issue("acme") } }, ctx); // 429
    await ctx.settle();
    expect(usage.points.map((p) => p.blobs?.[5])).toEqual(["401", "429"]);
    expect(counters.requests).toBe(0);
  });

  it("works with neither binding (metering is optional)", async () => {
    const ctx = new FakeContext();
    const res = await request("/v1", { headers: { "x-api-key": await issue("acme") } }, ctx);
    await ctx.settle();
    expect(res.status).toBe(200);
  });
});

describe("UsageCounter", () => {
  function counter() {
    const storage = new FakeStorage();
    const obj = new UsageCounter({ storage } as unknown as DurableObjectState, {} as Env);
    const call = async (method: string, path: string, body?: unknown) =>
      (await obj.fetch(new Request(`https://usage-counter${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) }))).json() as Promise<
        Record<string, unknown>
      >;
    return { storage, call };
  }

  it("increments daily counts, reports them, and remembers what was billed", async () => {
    const { call } = counter();
    await call("POST", "/increment", { day: "2026-10-23" });
    await call("POST", "/increment", { day: "2026-10-23", n: 4 });
    await call("POST", "/increment", { day: "2026-10-24" });
    expect(await call("GET", "/usage?day=2026-10-23")).toEqual({ day: "2026-10-23", count: 5, reported: false });
    await call("POST", "/reported", { day: "2026-10-23" });
    expect(await call("GET", "/usage")).toEqual({
      days: [
        { day: "2026-10-23", count: 5, reported: true },
        { day: "2026-10-24", count: 1, reported: false },
      ],
    });
  });

  it("rejects bad input", async () => {
    const { call } = counter();
    expect(await call("POST", "/increment", { day: "yesterday" })).toHaveProperty("error");
    expect(await call("POST", "/increment", { day: "2026-10-24", n: -3 })).toHaveProperty("error");
  });

  it("drops days older than the retention window", async () => {
    const { storage, call } = counter();
    await storage.put("day:2024-01-01", 9);
    await storage.put("reported:2024-01-01", true);
    await call("POST", "/increment", { day: "2026-10-24" });
    expect([...storage.data.keys()]).toEqual(["day:2026-10-24"]);
  });
});
