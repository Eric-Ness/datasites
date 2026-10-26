import { describe, expect, it } from "vitest";
import worker, { redirectUrl } from "../src/index";

describe("votingdata.net redirect", () => {
  it.each([
    ["https://votingdata.net/", "https://electiondata.dev/"],
    ["https://votingdata.net/some/path?x=1", "https://electiondata.dev/some/path?x=1"],
    ["https://www.votingdata.net/datasets/senate-results/?location=us-az", "https://electiondata.dev/datasets/senate-results/?location=us-az"],
  ])("sends %s to %s", (from, to) => {
    expect(redirectUrl(new URL(from))).toBe(to);
  });

  it("answers 302 with the location, for any method", () => {
    for (const method of ["GET", "HEAD", "POST"]) {
      const res = worker.fetch(new Request("https://votingdata.net/v1/status?site=electiondata", { method }));
      expect(res.status).toBe(302);
      expect(res.headers.get("location")).toBe("https://electiondata.dev/v1/status?site=electiondata");
    }
  });
});
