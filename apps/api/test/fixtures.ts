import { FakeR2 } from "./fake-r2";
import { keys, type Catalog, type Manifest, type PipelineState, type VersionDoc } from "../src/types";

export const CURRENT = "2026-10-09.aaaaaaaaaa";
export const OLD = "2026-09-08.bbbbbbbbbb";
export const BLOCKED = "2026-10-10.cccccccccc";

export const MLO_JSON = JSON.stringify({ meta: { series: "average" }, data: [{ date: "2026-09", value: 426.07 }] });
export const MLO_CSV = "date,value\n2026-09,426.07\n";
export const BULK_CSV = "series,location,date,value\naverage,noaa-mlo,2026-09,426.07\n";

function versionDoc(version: string, files: VersionDoc["files"], status: VersionDoc["validation"]["status"] = "pass"): VersionDoc {
  return {
    version,
    site: "climatedata",
    dataset: "co2-monthly",
    title: "Monthly atmospheric CO2",
    description: "test",
    cadence: "P1M",
    contract_version: 1,
    transform_version: "test",
    source: {
      id: "noaa-gml-co2",
      name: "NOAA GML",
      url: "https://gml.noaa.gov/ccgg/trends/",
      license: "LicenseRef-US-Government-Work",
      commercial_ok: true,
      attribution: "NOAA GML",
    },
    coverage: { start: "1958-03", end: "2026-09" },
    series: { average: "Monthly mean", trend: "Trend" },
    locations: { "noaa-mlo": "Mauna Loa", world: "Global" },
    validation: { status, errors: [], warnings: [] },
    run: null,
    tree: version.split(".")[1]!.padEnd(64, "0"),
    files,
  };
}

export function seededBucket(): FakeR2 {
  const r2 = new FakeR2();
  const blobs = { mloJson: "1".repeat(64), mloCsv: "2".repeat(64), bulk: "3".repeat(64), old: "4".repeat(64) };
  r2.putText(keys.blob(blobs.mloJson), MLO_JSON);
  r2.putText(keys.blob(blobs.mloCsv), MLO_CSV);
  r2.putText(keys.blob(blobs.bulk), BULK_CSV);
  r2.putText(keys.blob(blobs.old), "old");

  const manifest: Manifest = {
    site: "climatedata",
    dataset: "co2-monthly",
    current: CURRENT,
    published_at: "2026-10-09T19:30:00Z",
    previous: [OLD],
    snapshots: { "2026-09": OLD, "2026-10": CURRENT },
    quarantined: [BLOCKED],
    rev: 2,
  };
  r2.putJson(keys.manifest("climatedata", "co2-monthly"), manifest);
  r2.putJson(
    keys.version("climatedata", "co2-monthly", CURRENT),
    versionDoc(CURRENT, {
      "average/noaa-mlo.json": { blob: blobs.mloJson, bytes: MLO_JSON.length, rows: 1, type: "application/json" },
      "average/noaa-mlo.csv": { blob: blobs.mloCsv, bytes: MLO_CSV.length, rows: 1, type: "text/csv; charset=utf-8" },
      "trend/world.json": { blob: blobs.mloJson, bytes: MLO_JSON.length, rows: 1, type: "application/json" },
      "download/co2-monthly.csv": { blob: blobs.bulk, bytes: BULK_CSV.length, rows: 1, type: "text/csv; charset=utf-8" },
    }),
  );
  r2.putJson(
    keys.version("climatedata", "co2-monthly", OLD),
    versionDoc(OLD, { "average/noaa-mlo.json": { blob: blobs.old, bytes: 3, rows: 1, type: "application/json" } }),
  );
  r2.putJson(
    keys.version("climatedata", "co2-monthly", BLOCKED),
    versionDoc(BLOCKED, { "average/noaa-mlo.json": { blob: blobs.old, bytes: 3, type: "application/json" } }, "blocked"),
  );

  const catalog: Catalog = {
    site: "climatedata",
    datasets: [
      {
        id: "co2-monthly",
        title: "Monthly atmospheric CO2",
        description: "test",
        current: CURRENT,
        published_at: manifest.published_at,
        cadence: "P1M",
        check_every: "P1D",
        sla: "P45D",
        discontinued: false,
        source: { name: "NOAA GML", url: "https://gml.noaa.gov/", license: "LicenseRef-US-Government-Work", commercial_ok: true },
        coverage: { start: "1958-03", end: "2026-09" },
      },
    ],
  };
  r2.putJson(keys.catalog("climatedata"), catalog);

  const state: PipelineState = {
    checked_at: "2026-10-23T14:23:00Z",
    last_success: "2026-10-23T14:23:00Z",
    last_published: "2026-10-09T19:30:00Z",
    source_updated_at: "2026-10-09T19:23:07Z",
    consecutive_failures: 0,
    last_error: null,
    blocked_version: null,
  };
  r2.putJson(keys.state("climatedata", "co2-monthly"), state);
  return r2;
}
