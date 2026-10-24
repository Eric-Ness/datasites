// The pure half of the line chart: time parsing, ticks, decimation and summaries. No DOM, so it is unit-tested.

import { formatValue } from "../lib/format";

export type TimeType = "month" | "date" | "year" | "datetime";

export interface Point {
  /** Milliseconds since the epoch (UTC). */
  t: number;
  v: number | null;
  /** The original date string from the API. */
  label: string;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = 86_400_000;
const HOUR = 3_600_000;

/** Parses a date-like column value ("1958-03", "1958-03-15", "1958", "1958-03-15T12:00:00Z") as UTC milliseconds. */
export function parseTime(value: string, type: TimeType): number {
  let m: RegExpExecArray | null;
  if (type === "year" && (m = /^(\d{4})$/.exec(value))) return Date.UTC(Number(m[1]), 0, 1);
  if (type === "month" && (m = /^(\d{4})-(\d{2})$/.exec(value))) return Date.UTC(Number(m[1]), Number(m[2]) - 1, 1);
  if (type === "date" && (m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value))) {
    return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  }
  const t = Date.parse(value);
  return Number.isNaN(t) ? Number.NaN : t;
}

/** Converts API rows into chart points, skipping rows whose date cannot be parsed. */
export function toPoints(rows: readonly Record<string, unknown>[], dateKey: string, valueKey: string, type: TimeType): Point[] {
  const points: Point[] = [];
  for (const row of rows) {
    const label = String(row[dateKey] ?? "");
    const t = parseTime(label, type);
    if (Number.isNaN(t)) continue;
    const raw = row[valueKey];
    const v = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
    points.push({ t, v, label });
  }
  return points.sort((a, b) => a.t - b.t);
}

/** A point's date for people: "Sep 2026", "24 Oct 2026", "1958", "24 Oct 2026 14:00 UTC". */
export function formatTime(t: number, type: TimeType): string {
  const d = new Date(t);
  const y = d.getUTCFullYear();
  const mon = MONTHS[d.getUTCMonth()];
  if (type === "year") return String(y);
  if (type === "month") return `${mon} ${y}`;
  if (type === "date") return `${d.getUTCDate()} ${mon} ${y}`;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${d.getUTCDate()} ${mon} ${y} ${hh}:${mm} UTC`;
}

/** Evenly spaced "nice" numbers (1, 2 or 5 times a power of ten) covering [min, max]. */
export function niceTicks(min: number, max: number, count: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) {
    const pad = Math.abs(min) * 0.05 || 1;
    min -= pad;
    max += pad;
  }
  const step = niceStep((max - min) / Math.max(1, count));
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = start, i = 0; v <= end + step / 2 && i < 1000; v = start + ++i * step) {
    ticks.push(Number(v.toPrecision(12)));
  }
  return ticks;
}

export function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  const f = raw / power;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * power;
}

/** Decimals needed to tell ticks `step` apart. */
export function stepDecimals(step: number): number {
  if (!(step > 0)) return 0;
  return Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
}

type Unit = "year" | "month" | "day" | "hour" | "minute";

interface TimeStep {
  unit: Unit;
  n: number;
  approx: number;
}

const TIME_STEPS: TimeStep[] = [
  ...[15, 30].map((n) => ({ unit: "minute" as const, n, approx: n * 60_000 })),
  ...[1, 3, 6, 12].map((n) => ({ unit: "hour" as const, n, approx: n * HOUR })),
  ...[1, 2, 7, 14].map((n) => ({ unit: "day" as const, n, approx: n * DAY })),
  ...[1, 2, 3, 6].map((n) => ({ unit: "month" as const, n, approx: n * 30.44 * DAY })),
  ...[1, 2, 5, 10, 20, 25, 50, 100, 200, 500].map((n) => ({ unit: "year" as const, n, approx: n * 365.25 * DAY })),
];

function* stepTimes(min: number, max: number, step: TimeStep): Generator<number> {
  const d = new Date(min);
  if (step.unit === "year") {
    let y = Math.ceil(d.getUTCFullYear() / step.n) * step.n;
    for (let t = Date.UTC(y, 0, 1); t <= max; t = Date.UTC((y += step.n), 0, 1)) if (t >= min) yield t;
  } else if (step.unit === "month") {
    let y = d.getUTCFullYear();
    let m = Math.ceil(d.getUTCMonth() / step.n) * step.n;
    for (let t = Date.UTC(y, m, 1); t <= max; t = Date.UTC(y, (m += step.n), 1)) if (t >= min) yield t;
  } else {
    const size = step.unit === "day" ? step.n * DAY : step.unit === "hour" ? step.n * HOUR : step.n * 60_000;
    for (let t = Math.ceil(min / size) * size; t <= max; t += size) yield t;
  }
}

export interface TimeTicks {
  values: number[];
  unit: Unit;
  format: (t: number) => string;
}

/** Calendar-aligned ticks (years, months, days, hours) with at most `maxTicks` labels. */
export function timeTicks(min: number, max: number, maxTicks: number): TimeTicks {
  const span = Math.max(1, max - min);
  const limit = Math.max(2, maxTicks);
  const step = TIME_STEPS.find((s) => span / s.approx <= limit) ?? TIME_STEPS[TIME_STEPS.length - 1]!;
  const values = [...stepTimes(min, max, step)];
  const format = (t: number) => {
    const d = new Date(t);
    const mon = MONTHS[d.getUTCMonth()]!;
    if (step.unit === "year") return String(d.getUTCFullYear());
    if (step.unit === "month") return d.getUTCMonth() === 0 || values.length <= 4 ? `${mon} ${d.getUTCFullYear()}` : mon;
    if (step.unit === "day") return `${d.getUTCDate()} ${mon}`;
    return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
  };
  return { values, unit: step.unit, format };
}

/**
 * Keeps at most about `buckets * 2` points by taking each bucket's lowest and highest value
 * in time order, so peaks survive. Gaps (null values) are kept so lines still break there.
 */
export function decimate(points: readonly Point[], buckets: number): Point[] {
  if (points.length <= buckets * 2 || buckets < 1) return points.slice();
  const out: Point[] = [];
  const size = points.length / buckets;
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * size);
    const end = Math.min(points.length, Math.floor((b + 1) * size));
    let lo: Point | null = null;
    let hi: Point | null = null;
    let gap: Point | null = null;
    for (let i = start; i < end; i++) {
      const p = points[i]!;
      if (p.v === null) {
        gap ??= p;
        continue;
      }
      if (!lo || p.v < lo.v!) lo = p;
      if (!hi || p.v > hi.v!) hi = p;
    }
    const keep = [lo, hi, gap].filter((p): p is Point => p !== null);
    keep.sort((a, b2) => a.t - b2.t);
    for (const p of keep) if (out[out.length - 1] !== p) out.push(p);
  }
  return out;
}

/** Splits points into runs without nulls, so a line is not drawn across missing values. */
export function segments(points: readonly Point[]): Point[][] {
  const runs: Point[][] = [];
  let run: Point[] = [];
  for (const p of points) {
    if (p.v === null) {
      if (run.length) runs.push(run);
      run = [];
    } else run.push(p);
  }
  if (run.length) runs.push(run);
  return runs;
}

/** Index of the point nearest to time `t` (points sorted by time). */
export function nearestIndex(points: readonly Point[], t: number): number {
  if (!points.length) return -1;
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid]!.t < t) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1]!.t - t) <= Math.abs(points[lo]!.t - t)) return lo - 1;
  return lo;
}

export function valueExtent(series: readonly (readonly Point[])[]): [number, number] | null {
  let min = Infinity;
  let max = -Infinity;
  for (const points of series) {
    for (const p of points) {
      if (p.v === null) continue;
      if (p.v < min) min = p.v;
      if (p.v > max) max = p.v;
    }
  }
  return min === Infinity ? null : [min, max];
}

export function formatMeasure(v: number | null, unit?: string | null): string {
  if (v === null) return "no value";
  return unit ? `${formatValue(v)} ${unit}` : formatValue(v);
}

/** A one-paragraph text description of a series, for screen readers and the figure caption. */
export function describeSeries(points: readonly Point[], type: TimeType, unit?: string | null): string {
  const values = points.filter((p) => p.v !== null);
  if (!values.length) return "No values to show.";
  let lo = values[0]!;
  let hi = values[0]!;
  for (const p of values) {
    if (p.v! < lo.v!) lo = p;
    if (p.v! > hi.v!) hi = p;
  }
  const first = values[0]!;
  const last = values[values.length - 1]!;
  const n = values.length.toLocaleString("en-US");
  return (
    `${n} values from ${formatTime(first.t, type)} to ${formatTime(last.t, type)}. ` +
    `Lowest ${formatMeasure(lo.v, unit)} (${formatTime(lo.t, type)}), highest ${formatMeasure(hi.v, unit)} (${formatTime(hi.t, type)}), ` +
    `latest ${formatMeasure(last.v, unit)} (${formatTime(last.t, type)}).`
  );
}

/**
 * Change over the previous 12 months, for monthly series: each point minus the point one year earlier.
 * Points without a value a year before are dropped.
 */
export function yearOverYear(points: readonly Point[]): Point[] {
  const monthKey = (t: number) => {
    const d = new Date(t);
    return d.getUTCFullYear() * 12 + d.getUTCMonth();
  };
  const byMonth = new Map<number, number | null>();
  for (const p of points) byMonth.set(monthKey(p.t), p.v);
  const out: Point[] = [];
  for (const p of points) {
    const before = byMonth.get(monthKey(p.t) - 12);
    if (before === undefined) continue;
    out.push({ t: p.t, label: p.label, v: p.v === null || before === null ? null : Number((p.v - before).toFixed(6)) });
  }
  return out;
}
