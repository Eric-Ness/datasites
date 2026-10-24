// Formatting helpers shared by the static pages and the browser scripts. Locale-independent
// (always en-US, always UTC) so a build gives the same bytes on every machine.

import type { ColumnType } from "@datasites/registry";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const SHORT_MONTHS = MONTHS.map((m) => m.slice(0, 3));

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1000) return `${bytes} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = -1;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit++;
  }
  const digits = value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

// Contracts store units as plain ASCII codes ("degC", "ft^3/s"); charts and tables show the usual symbols.
const UNIT_SYMBOLS: Record<string, string> = {
  degC: "°C",
  degF: "°F",
  deg: "°",
  degrees: "°",
  percent: "%",
  "ft^3/s": "ft³/s",
  "mi^2": "mi²",
  "km^3/s^2": "km³/s²",
  "rev/day^2": "rev/day²",
  "rev/day^3": "rev/day³",
};

/** A contract unit as people read it: "degC" -> "°C". Unknown units pass through unchanged. */
export function displayUnit(unit: string | null | undefined): string | null {
  if (!unit) return null;
  return UNIT_SYMBOLS[unit] ?? unit;
}

const NUMBER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });

export function formatInt(n: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
}

/** A data value for display: numbers grouped, booleans as yes/no, null as an em dash. */
export function formatValue(value: unknown, type?: ColumnType): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (type === "year") return String(value);
    return NUMBER.format(value);
  }
  return String(value);
}

interface Duration {
  years: number;
  months: number;
  weeks: number;
  days: number;
  hours: number;
  minutes: number;
  seconds: number;
}

const DURATION = /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/;

export function parseDuration(iso: string): Duration | null {
  const m = DURATION.exec(iso);
  if (!m || iso === "P" || iso.endsWith("T")) return null;
  const n = (i: number) => Number(m[i] ?? 0);
  return { years: n(1), months: n(2), weeks: n(3), days: n(4), hours: n(5), minutes: n(6), seconds: n(7) };
}

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

/** "P45D" -> "45 days", "P1M" -> "1 month", "PT15M" -> "15 minutes". Unparseable input is returned as is. */
export function durationLabel(iso: string): string {
  const d = parseDuration(iso);
  if (!d) return iso;
  const parts = [
    d.years && plural(d.years, "year"),
    d.months && plural(d.months, "month"),
    d.weeks && plural(d.weeks, "week"),
    d.days && plural(d.days, "day"),
    d.hours && plural(d.hours, "hour"),
    d.minutes && plural(d.minutes, "minute"),
    d.seconds && plural(d.seconds, "second"),
  ].filter(Boolean) as string[];
  return parts.length ? parts.join(" ") : iso;
}

/** How often a dataset updates, as an adjective where one exists: "P1M" -> "Monthly", "PT3H" -> "Every 3 hours". */
export function cadenceLabel(iso: string): string {
  const named: Record<string, string> = {
    P1Y: "Yearly",
    P3M: "Quarterly",
    P1M: "Monthly",
    P1W: "Weekly",
    P7D: "Weekly",
    P1D: "Daily",
    PT1H: "Hourly",
  };
  if (named[iso]) return named[iso]!;
  const label = durationLabel(iso);
  return label === iso ? iso : `Every ${label}`;
}

export interface LicenseInfo {
  /** The SPDX id or expression from the contract. */
  id: string;
  label: string;
  /** For tables and fact boxes: "CC BY 4.0". */
  short: string;
  url: string | null;
  /** The licenses of an expression such as "CC-BY-4.0 AND OGL-UK-3.0", one per id. */
  parts: LicenseInfo[];
}

const KNOWN_LICENSES: Record<string, { label: string; short: string; url: string | null }> = {
  "LicenseRef-US-Government-Work": {
    label: "US Government work (public domain in the United States)",
    short: "US Government work",
    url: "https://www.usa.gov/government-works",
  },
  "LicenseRef-USDM-Credit-Required": {
    label: "US Drought Monitor terms: free to use with credit",
    short: "USDM terms (credit required)",
    url: null,
  },
  "CC0-1.0": { label: "CC0 1.0 (public domain dedication)", short: "CC0 1.0", url: "https://creativecommons.org/publicdomain/zero/1.0/" },
  "CC-BY-4.0": { label: "Creative Commons Attribution 4.0", short: "CC BY 4.0", url: "https://creativecommons.org/licenses/by/4.0/" },
  "CC-BY-SA-4.0": {
    label: "Creative Commons Attribution-ShareAlike 4.0",
    short: "CC BY-SA 4.0",
    url: "https://creativecommons.org/licenses/by-sa/4.0/",
  },
  "ODbL-1.0": { label: "Open Database License 1.0", short: "ODbL 1.0", url: "https://opendatacommons.org/licenses/odbl/1-0/" },
  "PDDL-1.0": {
    label: "Open Data Commons Public Domain Dedication 1.0",
    short: "PDDL 1.0",
    url: "https://opendatacommons.org/licenses/pddl/1-0/",
  },
  "OGL-UK-3.0": {
    label: "Open Government Licence v3.0",
    short: "OGL v3.0",
    url: "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
  },
};

function singleLicense(id: string): LicenseInfo {
  const known = KNOWN_LICENSES[id];
  if (known) return { id, ...known, parts: [] };
  if (id.startsWith("LicenseRef-")) {
    const label = id.slice("LicenseRef-".length).replace(/-/g, " ");
    return { id, label, short: label, url: null, parts: [] };
  }
  return { id, label: id, short: id, url: `https://spdx.org/licenses/${encodeURIComponent(id)}.html`, parts: [] };
}

/** A readable name and a link for an SPDX id, LicenseRef-* or SPDX expression ("A AND B", "A OR B"). */
export function licenseInfo(expression: string): LicenseInfo {
  const ids = expression
    .replace(/[()]/g, " ")
    .split(/\s+(?:AND|OR)\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (ids.length <= 1) {
    const one = singleLicense(ids[0] ?? expression.trim());
    return { ...one, id: expression, parts: [one] };
  }
  const parts = ids.map(singleLicense);
  const joiner = /\sOR\s/.test(expression) ? " or " : " and ";
  return {
    id: expression,
    label: parts.map((p) => p.label).join(joiner),
    short: parts.map((p) => p.short).join(joiner === " or " ? " or " : " + "),
    url: null,
    parts,
  };
}

export function commercialLabel(ok: boolean | null): string {
  if (ok === true) return "Allowed";
  if (ok === false) return "Not allowed";
  return "Unknown: check the source's terms";
}

/** "1958-03" -> "March 1958", "2026-10-24" -> "24 October 2026", "1958" -> "1958". Other strings pass through. */
export function formatPeriod(value: string | null | undefined, style: "long" | "short" = "long"): string {
  if (!value) return "";
  const names = style === "long" ? MONTHS : SHORT_MONTHS;
  let m = /^(\d{4})-(\d{2})$/.exec(value);
  if (m) return `${names[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
  m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (m) return `${Number(m[3])} ${names[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
  m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (m) return `${Number(m[3])} ${names[Number(m[2]) - 1] ?? m[2]} ${m[1]}, ${m[4]}:${m[5]} UTC`;
  return value;
}

export function formatCoverage(start: string | null | undefined, end: string | null | undefined): string {
  if (!start && !end) return "";
  if (start && end) return start === end ? formatPeriod(start) : `${formatPeriod(start)} to ${formatPeriod(end)}`;
  return start ? `From ${formatPeriod(start)}` : `Until ${formatPeriod(end)}`;
}

/** "3 hours ago", "in 5 minutes", "just now". */
export function relativeTime(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const diff = now - then;
  const abs = Math.abs(diff);
  if (abs < 45_000) return "just now";
  const steps: [number, string][] = [
    [60_000, "minute"],
    [3_600_000, "hour"],
    [86_400_000, "day"],
    [30 * 86_400_000, "month"],
    [365 * 86_400_000, "year"],
  ];
  let unit = steps[0]!;
  for (const step of steps) if (abs >= step[0]) unit = step;
  const n = Math.max(1, Math.round(abs / unit[0]));
  const text = plural(n, unit[1]);
  return diff >= 0 ? `${text} ago` : `in ${text}`;
}

export const COLUMN_TYPES: Record<ColumnType, string> = {
  month: "month (YYYY-MM)",
  date: "date (YYYY-MM-DD)",
  year: "year (YYYY)",
  datetime: "datetime (ISO 8601, UTC)",
  float: "number",
  int: "integer",
  str: "text",
  bool: "true / false",
};

export function columnTypeLabel(type: ColumnType): string {
  return COLUMN_TYPES[type] ?? type;
}

export function kindLabel(kind: string): string {
  return kind === "series" ? "Time series" : kind === "table" ? "Table" : kind === "files" ? "Files" : kind;
}

/** Joins words as "a, b and c". */
export function listJoin(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
