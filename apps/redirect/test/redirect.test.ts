import { describe, expect, it } from "vitest";
import worker, { redirectFor } from "../src/index";

describe("votingdata.net", () => {
  it.each([
    ["https://votingdata.net/", "https://electiondata.dev/"],
    ["https://votingdata.net/some/path?x=1", "https://electiondata.dev/some/path?x=1"],
    ["https://www.votingdata.net/datasets/senate-results/?location=us-az", "https://electiondata.dev/datasets/senate-results/?location=us-az"],
  ])("sends %s to %s with a 302", (from, to) => {
    expect(redirectFor(new URL(from))).toEqual({ location: to, status: 302 });
  });
});

describe("www.<site>.dev", () => {
  it.each([
    ["https://www.climatedata.dev/", "https://climatedata.dev/"],
    ["https://www.waterdata.dev/datasets/streamflow-daily/?location=usgs-01646500", "https://waterdata.dev/datasets/streamflow-daily/?location=usgs-01646500"],
  ])("sends %s to %s with a 301", (from, to) => {
    expect(redirectFor(new URL(from))).toEqual({ location: to, status: 301 });
  });
});

describe("the Worker", () => {
  it("answers each method with the redirect", () => {
    for (const method of ["GET", "HEAD", "POST"]) {
      const res = worker.fetch(new Request("https://www.airdata.dev/status/?x=1", { method }));
      expect(res.status).toBe(301);
      expect(res.headers.get("location")).toBe("https://airdata.dev/status/?x=1");
    }
  });

  it("refuses hosts it doesn't handle rather than redirecting them anywhere", () => {
    expect(redirectFor(new URL("https://climatedata.dev/"))).toBeNull();
    expect(redirectFor(new URL("https://www.example.com/"))).toBeNull();
    expect(worker.fetch(new Request("https://api.climatedata.dev/v1")).status).toBe(404);
  });
});
