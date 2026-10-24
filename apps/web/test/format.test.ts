import { describe, expect, it } from "vitest";
import {
  cadenceLabel,
  columnTypeLabel,
  commercialLabel,
  displayUnit,
  durationLabel,
  formatBytes,
  formatCoverage,
  formatPeriod,
  formatValue,
  kindLabel,
  licenseInfo,
  listJoin,
  relativeTime,
} from "../src/lib/format";

describe("formatBytes", () => {
  it("uses decimal units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(15_778)).toBe("15.8 kB");
    expect(formatBytes(101_535)).toBe("102 kB");
    expect(formatBytes(2_500_000)).toBe("2.5 MB");
    expect(formatBytes(3_200_000_000)).toBe("3.2 GB");
  });

  it("returns nothing for unknown sizes", () => {
    expect(formatBytes(null)).toBe("");
    expect(formatBytes(undefined)).toBe("");
    expect(formatBytes(-1)).toBe("");
  });
});

describe("displayUnit", () => {
  it("shows contract unit codes as symbols", () => {
    expect(displayUnit("degC")).toBe("°C");
    expect(displayUnit("ft^3/s")).toBe("ft³/s");
    expect(displayUnit("percent")).toBe("%");
  });

  it("passes unknown units through and drops empty ones", () => {
    expect(displayUnit("ppm")).toBe("ppm");
    expect(displayUnit("")).toBeNull();
    expect(displayUnit(null)).toBeNull();
  });
});

describe("durations", () => {
  it("names common cadences", () => {
    expect(cadenceLabel("P1M")).toBe("Monthly");
    expect(cadenceLabel("P1D")).toBe("Daily");
    expect(cadenceLabel("P7D")).toBe("Weekly");
    expect(cadenceLabel("PT1H")).toBe("Hourly");
    expect(cadenceLabel("P1Y")).toBe("Yearly");
    expect(cadenceLabel("PT15M")).toBe("Every 15 minutes");
    expect(cadenceLabel("PT3H")).toBe("Every 3 hours");
    expect(cadenceLabel("nonsense")).toBe("nonsense");
  });

  it("spells out durations", () => {
    expect(durationLabel("P45D")).toBe("45 days");
    expect(durationLabel("P1M")).toBe("1 month");
    expect(durationLabel("P1DT12H")).toBe("1 day 12 hours");
    expect(durationLabel("P")).toBe("P");
  });
});

describe("licenses", () => {
  it("names known licenses with links", () => {
    const gov = licenseInfo("LicenseRef-US-Government-Work");
    expect(gov.label).toMatch(/public domain/);
    expect(gov.url).toBe("https://www.usa.gov/government-works");
    expect(licenseInfo("CC-BY-4.0")).toMatchObject({ short: "CC BY 4.0", url: "https://creativecommons.org/licenses/by/4.0/" });
  });

  it("falls back to SPDX for other ids and to the id text for unknown LicenseRefs", () => {
    expect(licenseInfo("MIT").url).toBe("https://spdx.org/licenses/MIT.html");
    expect(licenseInfo("LicenseRef-Example-Terms")).toMatchObject({ label: "Example Terms", url: null });
  });

  it("splits SPDX expressions", () => {
    const both = licenseInfo("CC-BY-4.0 AND OGL-UK-3.0");
    expect(both.parts.map((p) => p.id)).toEqual(["CC-BY-4.0", "OGL-UK-3.0"]);
    expect(both.label).toBe("Creative Commons Attribution 4.0 and Open Government Licence v3.0");
    expect(both.short).toBe("CC BY 4.0 + OGL v3.0");
    expect(licenseInfo("(MIT OR Apache-2.0)").label).toBe("MIT or Apache-2.0");
  });

  it("labels commercial use", () => {
    expect(commercialLabel(true)).toBe("Allowed");
    expect(commercialLabel(false)).toBe("Not allowed");
    expect(commercialLabel(null)).toMatch(/Unknown/);
  });
});

describe("periods and times", () => {
  it("formats date-like values", () => {
    expect(formatPeriod("1958-03")).toBe("March 1958");
    expect(formatPeriod("2026-10-24")).toBe("24 October 2026");
    expect(formatPeriod("2026-10-24T11:51:33Z")).toBe("24 October 2026, 11:51 UTC");
    expect(formatPeriod("1958")).toBe("1958");
    expect(formatPeriod("1958-03", "short")).toBe("Mar 1958");
    expect(formatPeriod(null)).toBe("");
  });

  it("formats coverage ranges", () => {
    expect(formatCoverage("1958-03", "2026-09")).toBe("March 1958 to September 2026");
    expect(formatCoverage("2026-09", "2026-09")).toBe("September 2026");
    expect(formatCoverage(null, null)).toBe("");
    expect(formatCoverage("1958-03", null)).toBe("From March 1958");
  });

  it("formats relative times", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    expect(relativeTime("2026-10-24T11:59:50Z", now)).toBe("just now");
    expect(relativeTime("2026-10-24T11:55:00Z", now)).toBe("5 minutes ago");
    expect(relativeTime("2026-10-24T09:00:00Z", now)).toBe("3 hours ago");
    expect(relativeTime("2026-10-23T12:00:00Z", now)).toBe("1 day ago");
    expect(relativeTime("2026-10-24T13:00:00Z", now)).toBe("in 1 hour");
    expect(relativeTime("not a date", now)).toBe("");
  });
});

describe("values and labels", () => {
  it("formats data values", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(true)).toBe("yes");
    expect(formatValue(426.07)).toBe("426.07");
    expect(formatValue(1234567.5)).toBe("1,234,567.5");
    expect(formatValue(2026, "year")).toBe("2026");
    expect(formatValue("us-ca")).toBe("us-ca");
  });

  it("labels kinds, column types and lists", () => {
    expect(kindLabel("series")).toBe("Time series");
    expect(kindLabel("files")).toBe("Files");
    expect(columnTypeLabel("month")).toBe("month (YYYY-MM)");
    expect(listJoin(["a"])).toBe("a");
    expect(listJoin(["a", "b", "c"])).toBe("a, b and c");
  });
});
