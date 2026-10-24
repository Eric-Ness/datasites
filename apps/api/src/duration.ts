// ISO 8601 durations as used in dataset contracts (P1D, PT1H, P1M, P45D).
// Months and years are approximated as 30 and 365 days, which is fine for freshness checks.

const PATTERN = /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/;

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function durationMs(iso: string): number {
  const m = PATTERN.exec(iso);
  if (!m || iso === "P" || iso.endsWith("T")) throw new Error(`invalid ISO 8601 duration: ${iso}`);
  const [, y, mo, w, d, h, mi, s] = m.map((part) => Number(part ?? 0));
  return (
    (y ?? 0) * 365 * DAY +
    (mo ?? 0) * 30 * DAY +
    (w ?? 0) * 7 * DAY +
    (d ?? 0) * DAY +
    (h ?? 0) * HOUR +
    (mi ?? 0) * MINUTE +
    (s ?? 0) * SECOND
  );
}
