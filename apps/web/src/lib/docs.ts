// Response-shape sketches for the docs, built from a real contract of the site when it has one.

import type { Column, Dataset } from "@datasites/registry";
import { sampleLocation, seriesIds } from "./datasets";

const TYPE_HINT: Record<string, string> = {
  month: '"YYYY-MM"',
  date: '"YYYY-MM-DD"',
  year: '"YYYY"',
  datetime: '"YYYY-MM-DDTHH:MM:SSZ"',
  float: "<number>",
  int: "<integer>",
  str: "<string>",
  bool: "<true|false>",
};

function hint(col: Column): string {
  const base = TYPE_HINT[col.type] ?? "<value>";
  return col.nullable ? `${base} or null` : base;
}

function row(columns: readonly Column[]): string {
  return `{ ${columns.map((c) => `"${c.name}": ${hint(c)}`).join(", ")} }`;
}

function units(columns: readonly Column[]): string {
  const entries = columns.filter((c) => c.unit).map((c) => `"${c.name}": ${JSON.stringify(c.unit)}`);
  return `{ ${entries.join(", ")} }`;
}

/** The shape of /v1/{dataset}/{series}/{location}.json, using `ds` for names when given. */
export function seriesShape(ds?: Dataset): string {
  const series = ds ? seriesIds(ds)[0] : undefined;
  const location = ds ? sampleLocation(ds) : null;
  const columns: Column[] = ds?.schema.length
    ? ds.schema
    : [
        { name: "date", type: "month", unit: null, nullable: false, description: null },
        { name: "value", type: "float", unit: "{unit}", nullable: false, description: null },
      ];
  return [
    "{",
    '  "meta": {',
    `    "dataset": ${JSON.stringify(ds?.id ?? "{dataset}")},`,
    `    "series": ${JSON.stringify(series ?? "{series}")},`,
    `    "series_name": ${JSON.stringify(series && ds ? ds.series[series] : "{series name}")},`,
    `    "location": ${JSON.stringify(location ?? "{location}")},`,
    `    "location_name": ${JSON.stringify((location && ds?.locations.explicit?.[location]) || "{location name}")},`,
    `    "units": ${units(columns)},`,
    '    "source": "…", "license": "…", "attribution": "…"',
    "  },",
    '  "data": [',
    `    ${row(columns)},`,
    "    …",
    "  ]",
    "}",
  ].join("\n");
}

/** The shape of /v1/{dataset}/table/{partition}.json. */
export function tableShape(ds?: Dataset): string {
  const columns: Column[] = ds?.schema.length
    ? ds.schema
    : [
        { name: "id", type: "str", unit: null, nullable: false, description: null },
        { name: "value", type: "float", unit: "{unit}", nullable: true, description: null },
      ];
  const key = ds?.key.length ? ds.key : [columns[0]!.name];
  const partition = ds?.partition
    ? `\n    "partition": { ${JSON.stringify(ds.partition)}: "…" },   // absent in table/all`
    : "";
  return [
    "{",
    '  "meta": {',
    `    "dataset": ${JSON.stringify(ds?.id ?? "{dataset}")},`,
    `    "key": ${JSON.stringify(key)},${partition}`,
    '    "rows": <integer>,',
    `    "units": ${units(columns)},`,
    '    "source": "…", "license": "…", "attribution": "…"',
    "  },",
    '  "rows": [',
    `    ${row(columns)},`,
    "    …",
    "  ]",
    "}",
  ].join("\n");
}

/** An example 404 problem document for a file that isn't part of a dataset. */
export function problemExample(domain: string, ds?: Dataset): string {
  const id = ds?.id ?? "{dataset}";
  const series = ds ? seriesIds(ds)[0] : undefined;
  const file = ds?.kind === "table" ? "table/nowhere.json" : `${series ?? "{series}"}/nowhere.json`;
  return JSON.stringify(
    {
      type: `https://${domain}/docs/errors#unknown-file`,
      title: "Not found",
      status: 404,
      detail: `${file} is not part of ${id} version 2026-10-23.3f9a1c2e4b.`,
      instance: `/v1/${id}/${file}`,
      request_id: "8d1f0c2b9e7a4f31-IAD",
      hint: `GET /v1/${id}/locations`,
    },
    null,
    2,
  );
}
