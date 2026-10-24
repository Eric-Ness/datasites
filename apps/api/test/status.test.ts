import { describe, expect, it } from "vitest";
import { durationMs } from "../src/duration";
import { datasetStatus } from "../src/status";
import type { CatalogEntry, PipelineState } from "../src/types";

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
  ] as const)("classifies %s", (expected, e, s) => {
    expect(datasetStatus(e, s, now).status).toBe(expected);
  });

  it("does not count one or two failures as failing", () => {
    expect(datasetStatus(entry, state({ consecutive_failures: 2 }), now).status).toBe("ok");
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
