import { describe, expect, it } from "vitest";
import { byLevel, children, isLocationId, location, locations, sources } from "../src/index";

describe("location registry", () => {
  it("has unique ids and every parent exists", () => {
    const ids = locations.map((loc) => loc.id);
    expect(new Set(ids).size).toBe(ids.length);
    const orphans = locations.filter((loc) => loc.parent && !isLocationId(loc.parent));
    expect(orphans).toEqual([]);
  });

  it("uses the id formats from the plan", () => {
    for (const loc of locations) {
      const pattern = {
        world: /^world$/,
        country: /^[a-z]{3}$/,
        "us-state": /^us-[a-z]{2}$/,
        "us-county": /^us-county-\d{5}$/,
      }[loc.level];
      expect(loc.id).toMatch(pattern);
    }
  });

  it("has the ISO 3166-1 countries with their codes", () => {
    expect(byLevel("country")).toHaveLength(249);
    expect(location("usa")).toMatchObject({ iso2: "US", iso3: "USA", numeric: "840", name: "United States" });
    expect(location("twn")?.name).toBe("Taiwan");
    // Private-use and reserved codes are not countries.
    expect(isLocationId("zzz")).toBe(false);
    expect(isLocationId("quu")).toBe(false);
  });

  it("has US states and equivalents with FIPS codes", () => {
    expect(location("us-ca")).toMatchObject({ fips: "06", usps: "CA", kind: "state", parent: "usa" });
    expect(location("us-dc")?.kind).toBe("district");
    expect(location("us-pr")?.kind).toBe("territory");
  });

  it("uses current county geography, including Connecticut's planning regions", () => {
    expect(location("us-county-06037")).toMatchObject({ name: "Los Angeles County", parent: "us-ca" });
    expect(location("us-county-09110")?.name).toBe("Capitol Planning Region");
    expect(location("us-county-09001")).toBeUndefined(); // old Fairfield County, replaced in 2022
    expect(children("us-ct")).toHaveLength(9);
    const la = location("us-county-06037")!;
    expect(la.lat).toBeCloseTo(34.2, 1);
    expect(la.lon).toBeCloseTo(-118.26, 1);
  });

  it("credits its sources", () => {
    expect(sources.map((s) => s.license)).toContain("Public domain (U.S. Government work)");
    expect(sources.some((s) => s.license.startsWith("Unicode License v3"))).toBe(true);
  });
});
