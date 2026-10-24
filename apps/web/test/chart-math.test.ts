import { describe, expect, it } from "vitest";
import {
  decimate,
  describeSeries,
  formatTime,
  nearestIndex,
  niceStep,
  niceTicks,
  parseTime,
  segments,
  stepDecimals,
  timeTicks,
  toPoints,
  valueExtent,
  yearOverYear,
  type Point,
} from "../src/client/chart-math";

const utc = Date.UTC;

describe("parseTime", () => {
  it("parses every date-like column type as UTC", () => {
    expect(parseTime("1958-03", "month")).toBe(utc(1958, 2, 1));
    expect(parseTime("2026-10-24", "date")).toBe(utc(2026, 9, 24));
    expect(parseTime("1958", "year")).toBe(utc(1958, 0, 1));
    expect(parseTime("2026-10-24T11:51:33Z", "datetime")).toBe(utc(2026, 9, 24, 11, 51, 33));
    expect(Number.isNaN(parseTime("garbage", "month"))).toBe(true);
  });

  it("formats points back for people", () => {
    expect(formatTime(utc(2026, 8, 1), "month")).toBe("Sep 2026");
    expect(formatTime(utc(2026, 9, 24), "date")).toBe("24 Oct 2026");
    expect(formatTime(utc(1958, 0, 1), "year")).toBe("1958");
    expect(formatTime(utc(2026, 9, 24, 14, 5), "datetime")).toBe("24 Oct 2026 14:05 UTC");
  });
});

describe("toPoints", () => {
  it("reads API rows, keeps nulls as gaps and sorts by time", () => {
    const rows = [
      { date: "1958-05", value: 317.51 },
      { date: "1958-03", value: 315.71 },
      { date: "1958-04", value: null },
      { date: "bad", value: 1 },
    ];
    const points = toPoints(rows, "date", "value", "month");
    expect(points.map((p) => p.label)).toEqual(["1958-03", "1958-04", "1958-05"]);
    expect(points.map((p) => p.v)).toEqual([315.71, null, 317.51]);
  });
});

describe("ticks", () => {
  it("picks 1, 2 or 5 steps", () => {
    expect(niceStep(0.7)).toBe(1);
    expect(niceStep(1.5)).toBe(2);
    expect(niceStep(37)).toBe(50);
    expect(niceStep(0)).toBe(1);
  });

  it("covers the data range with nice numbers", () => {
    const ticks = niceTicks(312.4, 431.4, 5);
    expect(ticks[0]).toBeLessThanOrEqual(312.4);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(431.4);
    expect(ticks).toEqual([300, 350, 400, 450]);
    expect(niceTicks(5, 5, 4).length).toBeGreaterThan(1);
    expect(niceTicks(Number.NaN, 1, 4)).toEqual([]);
  });

  it("knows how many decimals a step needs", () => {
    expect(stepDecimals(25)).toBe(0);
    expect(stepDecimals(0.5)).toBe(1);
    expect(stepDecimals(0.02)).toBe(2);
  });

  it("puts time ticks on calendar boundaries", () => {
    const years = timeTicks(utc(1958, 2, 1), utc(2026, 8, 1), 8);
    expect(years.unit).toBe("year");
    expect(years.values.length).toBeLessThanOrEqual(8);
    for (const t of years.values) expect(new Date(t).getUTCMonth()).toBe(0);
    expect(years.format(years.values[0]!)).toMatch(/^\d{4}$/);

    const months = timeTicks(utc(2026, 0, 1), utc(2026, 11, 1), 12);
    expect(months.unit).toBe("month");
    expect(months.format(utc(2026, 0, 1))).toBe("Jan 2026");

    const days = timeTicks(utc(2026, 9, 1), utc(2026, 9, 20), 6);
    expect(days.unit).toBe("day");
    expect(days.format(days.values[0]!)).toMatch(/^\d{1,2} Oct$/);

    const hours = timeTicks(utc(2026, 9, 24, 0), utc(2026, 9, 24, 12), 6);
    expect(hours.unit).toBe("hour");
    expect(hours.format(utc(2026, 9, 24, 6))).toBe("06:00");
  });
});

describe("decimate and segments", () => {
  const points: Point[] = Array.from({ length: 1000 }, (_, i) => ({ t: i, v: i === 500 ? 1000 : i % 7, label: String(i) }));

  it("keeps peaks and order while cutting points", () => {
    const out = decimate(points, 50);
    expect(out.length).toBeLessThanOrEqual(150);
    expect(out.some((p) => p.v === 1000)).toBe(true);
    for (let i = 1; i < out.length; i++) expect(out[i]!.t).toBeGreaterThan(out[i - 1]!.t);
    expect(decimate(points.slice(0, 10), 50)).toHaveLength(10);
  });

  it("keeps gaps so lines break at missing values", () => {
    const withGap = points.map((p) => (p.t === 300 ? { ...p, v: null } : p));
    expect(decimate(withGap, 50).some((p) => p.v === null)).toBe(true);
    const runs = segments([
      { t: 1, v: 1, label: "" },
      { t: 2, v: null, label: "" },
      { t: 3, v: 3, label: "" },
      { t: 4, v: 4, label: "" },
    ]);
    expect(runs.map((r) => r.map((p) => p.t))).toEqual([[1], [3, 4]]);
  });
});

describe("lookups and summaries", () => {
  const pts: Point[] = [0, 10, 20, 30].map((t) => ({ t, v: t / 10, label: String(t) }));

  it("finds the nearest point", () => {
    expect(nearestIndex(pts, -5)).toBe(0);
    expect(nearestIndex(pts, 14)).toBe(1);
    expect(nearestIndex(pts, 16)).toBe(2);
    expect(nearestIndex(pts, 99)).toBe(3);
    expect(nearestIndex([], 1)).toBe(-1);
  });

  it("finds the value extent across lines", () => {
    expect(valueExtent([pts, [{ t: 0, v: -2, label: "" }]])).toEqual([-2, 3]);
    expect(valueExtent([[{ t: 0, v: null, label: "" }]])).toBeNull();
  });

  it("describes a series in words", () => {
    const series = toPoints(
      [
        { date: "1958-03", value: 315.71 },
        { date: "2026-05", value: 431.43 },
        { date: "2026-09", value: 426.07 },
      ],
      "date",
      "value",
      "month",
    );
    expect(describeSeries(series, "month", "ppm")).toBe(
      "3 values from Mar 1958 to Sep 2026. Lowest 315.71 ppm (Mar 1958), highest 431.43 ppm (May 2026), latest 426.07 ppm (Sep 2026).",
    );
    expect(describeSeries([], "month")).toBe("No values to show.");
  });
});

describe("yearOverYear", () => {
  it("subtracts the value twelve months earlier", () => {
    const rows = [
      { date: "2024-01", value: 420 },
      { date: "2024-02", value: 421 },
      { date: "2025-01", value: 422.5 },
      { date: "2025-02", value: null },
      { date: "2025-03", value: 424 },
    ];
    const out = yearOverYear(toPoints(rows, "date", "value", "month"));
    expect(out.map((p) => [p.label, p.v])).toEqual([
      ["2025-01", 2.5],
      ["2025-02", null],
    ]);
  });
});
