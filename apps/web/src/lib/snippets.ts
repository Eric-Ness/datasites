// Copyable code examples with exact URLs. Pure, and bundled into the dataset page script so the
// snippets follow the series, location or partition the reader picks.

import type { DatasetKind } from "@datasites/registry";
import { apiUrl, paths } from "./urls";

export interface SnippetInput {
  siteId: string;
  base: string;
  id: string;
  kind: DatasetKind;
  series?: string | null;
  location?: string | null;
  partition?: string | null;
  /** Table datasets: the partition column, for the snippet comments. */
  partitionColumn?: string | null;
  /** files datasets: one file path inside the dataset, once known. */
  file?: string | null;
}

export interface Snippets {
  curl: string;
  python: string;
  javascript: string;
}

export const SNIPPET_LANGUAGES = [
  { key: "curl", label: "curl" },
  { key: "python", label: "Python (polars)" },
  { key: "javascript", label: "JavaScript" },
] as const;

/** Stand-ins for ids that only the API knows yet; shown as {series} or {location} in the snippet. */
const PLACEHOLDER = { series: "__series__", location: "__location__" } as const;

function q(url: string): string {
  return JSON.stringify(url.replace(/__(series|location)__/g, "{$1}"));
}

export function datasetSnippets(input: SnippetInput): Snippets {
  const u = (path: string) => apiUrl(input.base, input.siteId, path);
  const meta = u(paths.dataset(input.id));
  const csvAll = u(paths.download(input.id, "csv"));
  const parquetAll = u(paths.download(input.id, "parquet"));

  if (input.kind === "series") {
    const series = input.series ?? PLACEHOLDER.series;
    const location = input.location ?? PLACEHOLDER.location;
    const json = u(paths.series(input.id, series, location, "json"));
    const csv = u(paths.series(input.id, series, location, "csv"));
    return {
      curl: [
        "# One series for one place (.json or .csv)",
        `curl -s ${q(json)}`,
        "",
        "# Every series and place in one file",
        `curl -sO ${q(csvAll)}`,
      ].join("\n"),
      python: [
        "import polars as pl",
        "",
        "# One series for one place",
        `df = pl.read_csv(${q(csv)})`,
        "",
        "# Every series and place, with series and location columns",
        `everything = pl.read_parquet(${q(parquetAll)})`,
      ].join("\n"),
      javascript: [
        `const res = await fetch(${q(json)});`,
        "if (!res.ok) throw new Error((await res.json()).detail); // errors are problem+json",
        "const { meta, data } = await res.json();",
        "console.log(meta.location_name, data.at(-1)); // { date, value, ... }",
      ].join("\n"),
    };
  }

  if (input.kind === "table") {
    const partition = input.partition ?? "all";
    const json = u(paths.table(input.id, partition, "json"));
    const csv = u(paths.table(input.id, partition, "csv"));
    const label = partition === "all" ? "All rows" : `Rows where ${input.partitionColumn ?? "the partition"} is ${partition}`;
    return {
      curl: [`# ${label} (.json or .csv)`, `curl -s ${q(json)}`, "", "# The whole table in one file", `curl -sO ${q(csvAll)}`].join("\n"),
      python: [
        "import polars as pl",
        "",
        `# ${label}`,
        `df = pl.read_csv(${q(csv)})`,
        "",
        "# The whole table",
        `everything = pl.read_parquet(${q(parquetAll)})`,
      ].join("\n"),
      javascript: [
        `const res = await fetch(${q(json)});`,
        "if (!res.ok) throw new Error((await res.json()).detail); // errors are problem+json",
        "const { meta, rows } = await res.json();",
        "console.log(meta.rows, rows[0]);",
      ].join("\n"),
    };
  }

  // files
  const file = input.file ?? null;
  const fileUrl = file ? u(paths.file(input.id, file)) : null;
  const tabular = file && /\.(csv|parquet)$/i.test(file);
  return {
    curl: [
      "# The current version and its files",
      `curl -s ${q(meta)}`,
      ...(fileUrl ? ["", `# One file: ${file}`, `curl -sO ${q(fileUrl)}`] : []),
    ].join("\n"),
    python: tabular
      ? [
          "import polars as pl",
          "",
          `df = pl.${file!.toLowerCase().endsWith(".parquet") ? "read_parquet" : "read_csv"}(${q(fileUrl!)})`,
        ].join("\n")
      : [
          "import json",
          "from urllib.request import urlopen",
          "",
          `meta = json.load(urlopen(${q(meta)}))`,
          'for f in meta["downloads"]:',
          '    print(f["name"], f["bytes"], f["url"])',
          ...(fileUrl ? ["", `data = json.load(urlopen(${q(fileUrl)}))`] : []),
        ].join("\n"),
    javascript: fileUrl
      ? [
          `const res = await fetch(${q(fileUrl)});`,
          "if (!res.ok) throw new Error(`HTTP ${res.status}`);",
          `const body = await res.${/\.(json|geojson|topojson)$/i.test(file!) ? "json" : "arrayBuffer"}();`,
        ].join("\n")
      : [`const meta = await (await fetch(${q(meta)})).json();`, "console.log(meta.version, meta.downloads);"].join("\n"),
  };
}

/** Three commands for the home page: the catalog, one dataset's metadata and one data file. */
export function quickstart(input: { siteId: string; base: string; first?: SnippetInput | null }): string[] {
  const u = (path: string) => apiUrl(input.base, input.siteId, path);
  const lines = [`curl -s ${q(u(paths.catalog()))}`];
  const ds = input.first;
  if (!ds) {
    lines.push(`curl -s ${q(u(paths.status()))}`, `curl -s ${q(u(paths.root()))}`);
    return lines;
  }
  lines.push(`curl -s ${q(u(paths.dataset(ds.id)))}`);
  if (ds.kind === "series" && ds.series && ds.location) {
    lines.push(`curl -s ${q(u(paths.series(ds.id, ds.series, ds.location, "csv")))}`);
  } else if (ds.kind === "files") {
    lines.push(`curl -s ${q(u(paths.locations(ds.id)))}`);
  } else {
    lines.push(`curl -sO ${q(u(paths.download(ds.id, "csv")))}`);
  }
  return lines;
}
