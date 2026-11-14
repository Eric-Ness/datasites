import { describe, expect, it } from "vitest";
import { listedFiles } from "../src/data";
import type { FileEntry } from "../src/types";

const f = (bytes: number): FileEntry => ({ blob: "0".repeat(64), bytes, type: "application/json" });

describe("listedFiles (metadata `files` for kind: files)", () => {
  it("lists files up to one folder deep, sorted, and leaves out bulk downloads and deep archives", () => {
    const files: Record<string, FileEntry> = {
      "current.geojson": f(10),
      "perimeters/index.json": f(5),
      "perimeters/IRWIN-1/20261114T000000Z-abc.geojson": f(3),
      "snapshots/index.json": f(4),
      "snapshots/2026-11-14/120000Z-def.json": f(2),
      "days/2026-11-13.geojson": f(7),
      "download/all.zip": f(99),
    };
    expect(listedFiles(files).map(([path]) => path)).toEqual([
      "current.geojson",
      "days/2026-11-13.geojson",
      "perimeters/index.json",
      "snapshots/index.json",
    ]);
  });

  it("is empty when a version has only bulk downloads", () => {
    expect(listedFiles({ "download/x.csv": f(1) })).toEqual([]);
  });
});
