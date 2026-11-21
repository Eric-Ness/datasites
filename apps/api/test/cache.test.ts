import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { app } from "../src/index";
import { blobCacheKey } from "../src/cache";
import { clearMemo, TTL } from "../src/store";
import { runFreshnessCheck } from "../src/status";
import { keys } from "../src/types";
import { BULK_CSV, MLO_JSON, OLD, seededBucket } from "./fixtures";
import { FakeCache, FakeContext } from "./fakes";
import type { FakeR2 } from "./fake-r2";

let r2: FakeR2;
let cache: FakeCache;

function request(url: string, init: RequestInit = {}, ctx = new FakeContext()) {
  return app.request(url, init, { DATA: r2.asBucket(), ENV: "production" } as unknown as Env, ctx.asContext());
}

beforeEach(() => {
  clearMemo();
  r2 = seededBucket();
  cache = new FakeCache();
  vi.stubGlobal("caches", { default: cache });
});
afterEach(() => vi.unstubAllGlobals());

/** Blob entries only: metadata reads have their own entries (see "edge cache for metadata"). */
const blobKeys = () => [...cache.entries.keys()].filter((k) => k.includes("/__edge-cache/blob/"));

describe("edge cache for blobs", () => {
  const url = "https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.json";

  it("stores a whole-body GET, then serves it without reading R2", async () => {
    const ctx = new FakeContext();
    const first = await request(url, {}, ctx);
    expect(await first.text()).toBe(MLO_JSON);
    await ctx.settle();
    expect(blobKeys()).toEqual([`https://api.climatedata.dev/__edge-cache/blob/climatedata/${"1".repeat(64)}`]);

    const reads = r2.reads;
    const second = await request(url);
    expect(second.status).toBe(200);
    expect(await second.text()).toBe(MLO_JSON);
    expect(r2.reads).toBe(reads);
    expect(second.headers.get("content-type")).toBe("application/json");
    expect(second.headers.get("content-length")).toBe(String(MLO_JSON.length));
    expect(second.headers.get("etag")).toBe(`"${"1".repeat(64)}"`);
    expect(second.headers.get("access-control-allow-origin")).toBe("*");
    expect(second.headers.get("x-plan")).toBe("anonymous");
  });

  it("rebuilds request-specific headers on a hit (pinned vs current)", async () => {
    const ctx = new FakeContext();
    await request(`https://api.climatedata.dev/v1/co2-monthly@${OLD}/average/noaa-mlo.json`, {}, ctx);
    await ctx.settle();
    const pinned = await request(`https://api.climatedata.dev/v1/co2-monthly@${OLD}/average/noaa-mlo.json`);
    expect(pinned.headers.get("cache-control")).toBe("public, max-age=31536000, immutable");
    expect(pinned.headers.get("x-data-version")).toBe(OLD);
  });

  it("never mixes hosts or sites", () => {
    const a = blobCacheKey("https://api.climatedata.dev/v1/x/y/z.json", "climatedata", "ab".repeat(32));
    const b = blobCacheKey("https://api.waterdata.dev/v1/x/y/z.json", "waterdata", "ab".repeat(32));
    const c = blobCacheKey("http://localhost/v1/x/y/z.json?site=waterdata&x-api-key=1", "waterdata", "ab".repeat(32));
    expect(new Set([a.url, b.url, c.url]).size).toBe(3);
    expect(c.url).not.toContain("?");
  });

  it("sends Range requests to R2 and never caches them", async () => {
    const reads = r2.reads;
    const ctx = new FakeContext();
    const res = await request("https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.csv", { headers: { range: "bytes=0-5" } }, ctx);
    await ctx.settle();
    expect(res.status).toBe(206);
    expect(await res.text()).toBe(BULK_CSV.slice(0, 6));
    expect(r2.reads).toBeGreaterThan(reads);
    expect(blobKeys()).toEqual([]);
  });

  it("answers HEAD with the length and no body, without downloading the blob", async () => {
    const get = vi.spyOn(r2, "get");
    const head = vi.spyOn(r2, "head");
    const res = await request("https://api.climatedata.dev/v1/co2-monthly/download/co2-monthly.csv", { method: "HEAD" });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-length")).toBe(String(BULK_CSV.length));
    expect(await res.text()).toBe("");
    expect(head).toHaveBeenCalledTimes(1);
    expect(get.mock.calls.some(([key]) => String(key).startsWith("blobs/sha256/33/"))).toBe(false);
  });

  it("still answers If-None-Match with 304 before touching the cache", async () => {
    const res = await request(url, { headers: { "if-none-match": `"${"1".repeat(64)}"` } });
    expect(res.status).toBe(304);
    expect(cache.matched.filter((u) => u.includes("/__edge-cache/blob/"))).toEqual([]);
  });
});

describe("edge cache for metadata", () => {
  const url = "https://api.climatedata.dev/v1/co2-monthly/average/noaa-mlo.json";
  const manifestKey = `https://api.climatedata.dev/__edge-cache/json/${keys.manifest("climatedata", "co2-monthly")}`;

  afterEach(() => vi.useRealTimers());

  /** One request on a fresh isolate (empty memo), with its waitUntil work finished. */
  async function coldRequest(u = url) {
    clearMemo();
    const ctx = new FakeContext();
    const res = await request(u, {}, ctx);
    await ctx.settle();
    return res;
  }

  it("lets a fresh isolate answer without reading R2 at all", async () => {
    expect((await coldRequest()).status).toBe(200);
    const reads = r2.reads;
    const res = await coldRequest();
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(MLO_JSON);
    expect(r2.reads).toBe(reads);
  });

  it("caches a version doc for good and a manifest only for its ttl", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-20T12:00:00Z"), toFake: ["Date"] });
    await coldRequest();
    const manifest = cache.entries.get(manifestKey)!;
    expect(manifest.headers.get("cache-control")).toBe(`public, max-age=${TTL.manifest / 1000}`);
    expect(manifest.headers.get("x-datasites-expires")).toBe(String(Date.now() + TTL.manifest));
    const version = [...cache.entries].find(([k]) => k.includes("/versions/"))!;
    expect(version[1].headers.get("cache-control")).toBe("public, max-age=31536000, immutable");
    expect(version[1].headers.get("x-datasites-expires")).toBeNull();
  });

  it("never keeps a manifest from the edge past its first ttl, so a new version shows within it", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-20T12:00:00Z"), toFake: ["Date"] });
    await coldRequest();
    const get = vi.spyOn(r2, "get");
    vi.setSystemTime(Date.now() + TTL.manifest - 1_000);
    await coldRequest();
    expect(get.mock.calls.map(([k]) => k)).not.toContain(keys.manifest("climatedata", "co2-monthly"));
    vi.setSystemTime(Date.now() + 2_000); // past the ttl the entry was first stored with
    await coldRequest();
    expect(get.mock.calls.map(([k]) => k)).toContain(keys.manifest("climatedata", "co2-monthly"));
  });

  it("keeps sites apart", async () => {
    await coldRequest();
    expect([...cache.entries.keys()].every((k) => k.startsWith("https://api.climatedata.dev/__edge-cache/"))).toBe(true);
  });

  it("falls back to R2 when the edge cache fails", async () => {
    vi.spyOn(cache, "match").mockRejectedValue(new Error("cache down"));
    vi.spyOn(cache, "put").mockRejectedValue(new Error("cache down"));
    const res = await coldRequest();
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(MLO_JSON);
  });

  it("is not used by the freshness cron, which must see R2 itself", async () => {
    await runFreshnessCheck({ DATA: r2.asBucket(), ENV: "production" } as unknown as Env);
    expect(cache.matches).toBe(0);
    expect(cache.entries.size).toBe(0);
  });
});
