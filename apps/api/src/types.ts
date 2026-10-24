// Shapes of the objects the pipelines write to R2. pipelines/core/src/datasites_core/layout.py
// writes the same shapes; change both together.

export interface FileEntry {
  blob: string;
  bytes: number;
  rows?: number;
  type: string;
}

export interface SourceInfo {
  id: string;
  name: string;
  publisher?: string;
  url: string;
  license: string;
  commercial_ok: boolean | null;
  attribution: string;
  retrieved_at?: string;
  source_updated_at?: string | null;
}

export type DatasetKind = "series" | "table" | "files";

export interface VersionDoc {
  version: string;
  site: string;
  dataset: string;
  /** Absent on versions published before kinds existed; treat as "series". */
  kind?: DatasetKind;
  title: string;
  description: string;
  cadence: string;
  contract_version: number;
  transform_version: string;
  source: SourceInfo;
  coverage: { start: string | null; end: string | null };
  series: Record<string, string>;
  /** Inline for small series datasets... */
  locations?: Record<string, string>;
  /** ...or a blob holding the same {id: name} map for big ones. */
  locations_blob?: string;
  /** Table datasets */
  key?: string[];
  partition?: string | null;
  validation: { status: "pass" | "warn" | "blocked"; errors: string[]; warnings: string[] };
  run: string | null;
  tree: string;
  /** Every file for small datasets; only download/* when file_shards is present. */
  files: Record<string, FileEntry>;
  /** First path segment (series name or "table") -> blob holding that segment's {path: FileEntry} map. */
  file_shards?: Record<string, string>;
}

export interface Manifest {
  site: string;
  dataset: string;
  current: string;
  published_at: string;
  previous: string[];
  snapshots: Record<string, string>;
  quarantined: string[];
  rev: number;
}

export interface CatalogEntry {
  id: string;
  kind?: DatasetKind;
  title: string;
  description: string;
  current: string | null;
  published_at: string | null;
  cadence: string;
  check_every: string;
  sla: string;
  discontinued: boolean;
  source: { name: string; url: string; license: string; commercial_ok: boolean | null };
  coverage: { start: string | null; end: string | null };
}

export interface Catalog {
  site: string;
  datasets: CatalogEntry[];
}

export interface PipelineState {
  checked_at: string | null;
  last_success: string | null;
  last_published: string | null;
  source_updated_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
  blocked_version: string | null;
}

export const keys = {
  catalog: (site: string) => `sites/${site}/catalog.json`,
  manifest: (site: string, dataset: string) => `sites/${site}/datasets/${dataset}/manifest.json`,
  version: (site: string, dataset: string, version: string) =>
    `sites/${site}/datasets/${dataset}/versions/${version}.json`,
  blob: (hash: string) => `blobs/sha256/${hash.slice(0, 2)}/${hash}`,
  state: (site: string, dataset: string) => `ops/state/${site}/${dataset}.json`,
};
