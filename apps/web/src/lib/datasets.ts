// Facts about a dataset contract that the pages need. Pure functions over registry entries,
// so they can be tested with fixture datasets of every kind.

import type { Column, Dataset } from "@datasites/registry";
import { apiUrl, paths } from "./urls";

export const TIME_TYPES = new Set(["month", "date", "year", "datetime"]);
export const NUMERIC_TYPES = new Set(["float", "int"]);

/** Location levels of packages/geo and a well-known example id for each. */
export const LEVEL_EXAMPLE: Record<string, string> = {
  world: "world",
  country: "usa",
  "us-state": "us-ca",
  "us-county": "us-county-06037",
};

export const LEVEL_LABEL: Record<string, { one: string; many: string; idPattern: string }> = {
  world: { one: "the world", many: "the world as a whole", idPattern: "world" },
  country: { one: "country", many: "countries", idPattern: "ISO 3166-1 alpha-3, lowercase (usa, twn)" },
  "us-state": { one: "US state", many: "US states and equivalents", idPattern: "us- plus the USPS code (us-ca, us-dc)" },
  "us-county": { one: "US county", many: "US counties and equivalents", idPattern: "us-county- plus the 5-digit FIPS code" },
};

export function timeColumn(ds: Dataset): Column | undefined {
  return ds.schema.find((c) => TIME_TYPES.has(c.type));
}

/** Columns worth charting: numbers that are not the date. */
export function valueColumns(ds: Dataset): Column[] {
  return ds.schema.filter((c) => NUMERIC_TYPES.has(c.type));
}

/** The column a series chart shows first: "value" when present, else the first number. */
export function defaultMeasure(ds: Dataset): string | null {
  const numeric = valueColumns(ds);
  return (numeric.find((c) => c.name === "value") ?? numeric[0])?.name ?? null;
}

export function seriesIds(ds: Dataset): string[] {
  return Object.keys(ds.series);
}

/** A location id that is known at build time to exist for this dataset, or null when only the API knows. */
export function sampleLocation(ds: Dataset): string | null {
  const explicit = Object.keys(ds.locations.explicit ?? {});
  if (explicit.length) return explicit[0]!;
  for (const level of ds.locations.registry ?? []) {
    if (LEVEL_EXAMPLE[level]) return LEVEL_EXAMPLE[level]!;
  }
  return null;
}

export interface Selection {
  series: string | null;
  location: string | null;
  partition: string | null;
  file: string | null;
}

/** The example the snippets start with, before the browser has asked the API for the real ids. */
export function defaultSelection(ds: Dataset): Selection {
  if (ds.kind === "series") {
    return { series: seriesIds(ds)[0] ?? null, location: sampleLocation(ds), partition: null, file: null };
  }
  if (ds.kind === "table") {
    // "all" exists for tables up to 50,000 rows; partitioned tables also have one file per value.
    return { series: null, location: null, partition: "all", file: null };
  }
  return { series: null, location: null, partition: null, file: null };
}

export interface LocationSummary {
  /** One sentence for the dataset page. */
  text: string;
  explicit: { id: string; name: string }[];
  levels: string[];
  pattern: string | null;
}

/** Describes where a series dataset's locations come from. `counts` gives the number of places per geo level. */
export function describeLocations(ds: Dataset, counts: Record<string, number> = {}): LocationSummary {
  const explicit = Object.entries(ds.locations.explicit ?? {}).map(([id, name]) => ({ id, name }));
  const levels = ds.locations.registry ?? [];
  const pattern = ds.locations.pattern ?? null;
  const parts: string[] = [];
  for (const level of levels) {
    const label = LEVEL_LABEL[level];
    const count = counts[level];
    if (level === "world") parts.push("the world as a whole (id world)");
    else if (label) parts.push(`${count ? `all ${count.toLocaleString("en-US")} ` : "all "}${label.many} (ids: ${label.idPattern})`);
    else parts.push(`every ${level} location`);
  }
  if (pattern) parts.push(`stations whose ids match ${pattern}`);
  if (explicit.length && !levels.length && !pattern) {
    parts.push(explicit.length === 1 ? "one place" : `${explicit.length} places`);
  } else if (explicit.length) {
    parts.push(`${explicit.length} more named ${explicit.length === 1 ? "place" : "places"}`);
  }
  const text = parts.length ? `Covers ${parts.join("; ")}.` : "The API lists the locations this dataset covers.";
  return { text, explicit, levels, pattern };
}

export interface EndpointLink {
  label: string;
  /** The path template, with {placeholders}. */
  template: string;
  /** A working example URL, or null when the example ids are only known at run time. */
  example: string | null;
}

/** Every endpoint of one dataset, with exact example URLs where the ids are known at build time. */
export function datasetEndpoints(ds: Dataset, base: string, siteId: string): EndpointLink[] {
  const u = (path: string) => apiUrl(base, siteId, path);
  const sel = defaultSelection(ds);
  const links: EndpointLink[] = [
    { label: "Metadata: version, coverage, downloads, validation", template: `/v1/${ds.id}`, example: u(paths.dataset(ds.id)) },
    { label: "Versions and monthly snapshots", template: `/v1/${ds.id}/versions`, example: u(paths.versions(ds.id)) },
  ];
  if (ds.kind === "series") {
    links.push({ label: "Series and locations", template: `/v1/${ds.id}/locations`, example: u(paths.locations(ds.id)) });
    for (const series of seriesIds(ds)) {
      for (const format of ["json", "csv"] as const) {
        links.push({
          label: `${ds.series[series]} (${format.toUpperCase()})`,
          template: `/v1/${ds.id}/${series}/{location}.${format}`,
          example: sel.location ? u(paths.series(ds.id, series, sel.location, format)) : null,
        });
      }
    }
  } else if (ds.kind === "table") {
    links.push({ label: "Partitions", template: `/v1/${ds.id}/locations`, example: u(paths.locations(ds.id)) });
    for (const format of ["json", "csv"] as const) {
      links.push({
        label: ds.partition ? `Rows for one ${ds.partition} (${format.toUpperCase()})` : `All rows (${format.toUpperCase()})`,
        template: ds.partition ? `/v1/${ds.id}/table/{${ds.partition}}.${format}` : `/v1/${ds.id}/table/all.${format}`,
        example: ds.partition ? null : u(paths.table(ds.id, "all", format)),
      });
    }
    if (ds.partition) {
      links.push({
        label: "All rows in one file (tables up to 50,000 rows)",
        template: `/v1/${ds.id}/table/all.json`,
        example: u(paths.table(ds.id, "all", "json")),
      });
    }
  } else {
    links.push({ label: "One file, at the path listed in the metadata", template: `/v1/${ds.id}/{path}`, example: null });
  }
  if (ds.kind !== "files") {
    for (const format of ["csv", "parquet"] as const) {
      links.push({
        label: `Whole dataset (${format === "csv" ? "CSV" : "Parquet"})`,
        template: `/v1/${ds.id}/download/${ds.id}.${format}`,
        example: u(paths.download(ds.id, format)),
      });
    }
  }
  return links;
}

/** Bulk download files every series and table dataset publishes. */
export function bulkDownloads(ds: Dataset, base: string, siteId: string): { name: string; format: string; url: string }[] {
  if (ds.kind === "files") return [];
  return (["csv", "parquet"] as const).map((format) => ({
    name: `${ds.id}.${format}`,
    format: format === "csv" ? "CSV" : "Parquet",
    url: apiUrl(base, siteId, paths.download(ds.id, format)),
  }));
}

/** Units of the schema columns, for chart and table headers. */
export function columnUnits(ds: Dataset): Record<string, string> {
  return Object.fromEntries(ds.schema.filter((c) => c.unit).map((c) => [c.name, c.unit!]));
}

/** Datasets grouped by source, for the sources page. */
export interface SourceGroup {
  key: string;
  source: Dataset["source"];
  datasets: Dataset[];
}

export function groupBySource(datasets: readonly Dataset[]): SourceGroup[] {
  const groups = new Map<string, SourceGroup>();
  for (const ds of datasets) {
    const key = `${ds.source.name}\u0000${ds.source.url}\u0000${ds.source.license}\u0000${ds.source.attribution}`;
    const group = groups.get(key);
    if (group) group.datasets.push(ds);
    else groups.set(key, { key, source: ds.source, datasets: [ds] });
  }
  return [...groups.values()].sort((a, b) => a.source.name.localeCompare(b.source.name));
}

/** Whether any dataset takes its location names from packages/geo, whose sources then need crediting. */
export function usesGeoRegistry(datasets: readonly Dataset[]): boolean {
  return datasets.some((ds) => ds.kind === "series" && (ds.locations.registry?.length ?? 0) > 0);
}

/** One sentence on the file formats a site offers, given the kinds of its datasets. */
export function formatsOffered(kinds: Iterable<string>): string {
  const set = new Set(kinds);
  const parts: string[] = [];
  if (set.has("series")) parts.push("each series for each place as a small JSON or CSV file");
  if (set.has("table")) parts.push("tables as JSON or CSV, one file per partition");
  if (set.has("files")) parts.push("ready-made files at stable paths");
  const bulk = set.has("series") || set.has("table") || set.size === 0;
  const text = parts.length ? parts.join("; ") : "small JSON and CSV files for each series or table";
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}${bulk ? ", plus every dataset as one CSV or Parquet file" : ""}.`;
}
