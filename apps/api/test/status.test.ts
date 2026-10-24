import { afterEach, describe, expect, it, vi } from "vitest";
import { durationMs } from "../src/duration";
import { datasetStatus, runFreshnessCheck } from "../src/status";
import { clearMemo } from "../src/store";
import { keys, type CatalogEntry, type PipelineState } from "../src/types";
import { seededBucket } from "./fixtures";

const entry: CatalogEntry = {
  id: "co2-monthly",
  title: "CO2",
  description: "",
  current: "2026-10-09.aaaaaaaaaa",
  published_at: "2026-10-09T19:30:00Z",
  cadence: "P1M",
  check_every: "P1D",
  sla: "P45D",
  discontinued: false,
  source: { name: "NOAA", url: "", license: "", commercial_ok: true },
  coverage: { start: null, end: null },
};

const now = Date.parse("2026-10-23T15:00:00Z");

function state(overrides: Partial<PipelineState> = {}): PipelineState {
  return {
    checked_at: "2026-10-23T14:23:00Z",
    last_success: "2026-10-23T14:23:00Z",
    last_published: "2026-10-09T19:30:00Z",
    source_updated_at: "2026-10-09T19:23:07Z",
    consecutive_failures: 0,
    last_error: null,
    blocked_version: null,
    ...overrides,
  };
}

describe("datasetStatus", () => {
  it.each([
    ["ok", entry, state()],
    ["pending", entry, null],
    ["failing", entry, state({ consecutive_failures: 3, last_error: "HTTP 503" })],
    ["blocked", entry, state({ blocked_version: "2026-10-10.cccccccccc" })],
    ["late", entry, state({ checked_at: "2026-10-21T14:00:00Z" })],
    ["stale-upstream", entry, state({ source_updated_at: "2026-08-01T00:00:00Z" })],
    ["discontinued", { ...entry, discontinued: true }, state()],
    ["unpublished", { ...entry, current: null }, null],
    ["unpublished", { ...entry, current: null }, state({ checked_at: null, consecutive_failures: 2 })],
    ["failing", { ...entry, current: null }, state({ checked_at: null, consecutive_failures: 3 })],
  ] as const)("classifies %s", (expected, e, s) => {
    expect(datasetStatus(e, s, now).status).toBe(expected);
  });

  it("does not count one or two failures as failing", () => {
    expect(datasetStatus(entry, state({ consecutive_failures: 2 }), now).status).toBe("ok");
  });
});

describe("runFreshnessCheck (the 15-minute cron)", () => {
  const HC = "https://hc-ping.com/test-uuid";
  afterEach(() => {
    vi.unstubAllGlobals();
    clearMemo();
  });

  function envWith(stateOverrides: Partial<PipelineState>) {
    const r2 = seededBucket();
    r2.putJson(keys.state("climatedata", "co2-monthly"), state(stateOverrides));
    return { DATA: r2.asBucket(), ENV: "production", HEALTHCHECK_URL: HC } as unknown as Env;
  }

  it("pings the success URL when every dataset is healthy", async () => {
    const ping = vi.fn(async () => new Response("OK"));
    vi.stubGlobal("fetch", ping);
    const problems = await runFreshnessCheck(envWith({}), now);
    expect(problems).toEqual([]);
    expect(ping).toHaveBeenCalledWith(HC, expect.objectContaining({ method: "POST" }));
  });

  it("pings /fail with the problems when a dataset is failing", async () => {
    const ping = vi.fn(async () => new Response("OK"));
    vi.stubGlobal("fetch", ping);
    const problems = await runFreshnessCheck(envWith({ consecutive_failures: 3, last_error: "HTTP 503" }), now);
    expect(problems).toEqual(["climatedata/co2-monthly: failing (HTTP 503)"]);
    const [url, init] = ping.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${HC}/fail`);
    expect(init.body).toContain("climatedata/co2-monthly: failing");
  });

  it("does not page for stale-upstream, which is the source's problem", async () => {
    const ping = vi.fn(async () => new Response("OK"));
    vi.stubGlobal("fetch", ping);
    const problems = await runFreshnessCheck(envWith({ source_updated_at: "2026-08-01T00:00:00Z" }), now);
    expect(problems).toEqual([]);
    expect(ping).toHaveBeenCalledWith(HC, expect.anything());
  });

  it("works without a ping URL configured", async () => {
    const ping = vi.fn();
    vi.stubGlobal("fetch", ping);
    const env = { ...envWith({ consecutive_failures: 5 }), HEALTHCHECK_URL: undefined } as Env;
    expect(await runFreshnessCheck(env, now)).toHaveLength(1);
    expect(ping).not.toHaveBeenCalled();
  });
});

describe("durationMs", () => {
  it.each([
    ["PT15M", 15 * 60_000],
    ["PT1H", 3_600_000],
    ["P1D", 86_400_000],
    ["P1W", 7 * 86_400_000],
    ["P1M", 30 * 86_400_000],
    ["P1Y", 365 * 86_400_000],
    ["P1DT12H", 1.5 * 86_400_000],
  ])("parses %s", (iso, ms) => {
    expect(durationMs(iso)).toBe(ms);
  });

  it.each(["", "P", "1D", "PT", "P1DT"])("rejects %j", (iso) => {
    expect(() => durationMs(iso)).toThrow();
  });
});
