// Everything a dataset page shows, computed from one registry entry. The page renders it and passes
// `client` to the browser script, so the static HTML and the live explorer agree.

import type { ColumnType, Dataset, DatasetKind, Site } from "@datasites/registry";
import {
  bulkDownloads,
  datasetEndpoints,
  defaultMeasure,
  defaultSelection,
  describeLocations,
  timeColumn,
  valueColumns,
  type EndpointLink,
  type LocationSummary,
  type Selection,
} from "./datasets";
import { datasetSnippets, type Snippets } from "./snippets";
import { chartsFor, type DatasetCharts } from "./visualizedata";

export interface ClientColumn {
  name: string;
  type: ColumnType;
  unit: string | null;
}

/** The JSON the dataset page script reads from <script id="dataset-config">. */
export interface DatasetClientConfig {
  site: string;
  id: string;
  kind: DatasetKind;
  title: string;
  schema: ClientColumn[];
  series: Record<string, string>;
  explicit: Record<string, string>;
  key: string[];
  partition: string | null;
  timeColumn: string | null;
  timeType: "month" | "date" | "year" | "datetime" | null;
  measures: ClientColumn[];
  defaultMeasure: string | null;
  selection: Selection;
  sourceName: string;
  needsCredentials: boolean;
}

export interface Section {
  id: string;
  label: string;
}

export interface DatasetPageModel {
  sections: Section[];
  snippets: Snippets;
  endpoints: EndpointLink[];
  downloads: { name: string; format: string; url: string }[];
  locations: LocationSummary | null;
  client: DatasetClientConfig;
  /** The heading of the kind-specific explorer. */
  exploreTitle: string;
  /** visualizedata.org's indicators and pages built from this dataset, or null when it has none. */
  charts: DatasetCharts | null;
}

export function datasetPageModel(
  site: Pick<Site, "id">,
  ds: Dataset,
  base: string,
  levelCounts: Record<string, number> = {},
): DatasetPageModel {
  const selection = defaultSelection(ds);
  const time = timeColumn(ds);
  const measures = valueColumns(ds).map(({ name, type, unit }) => ({ name, type, unit }));
  const client: DatasetClientConfig = {
    site: site.id,
    id: ds.id,
    kind: ds.kind,
    title: ds.title,
    schema: ds.schema.map(({ name, type, unit }) => ({ name, type, unit })),
    series: ds.series,
    explicit: ds.locations.explicit ?? {},
    key: ds.key,
    partition: ds.partition,
    timeColumn: time?.name ?? null,
    timeType: (time?.type as DatasetClientConfig["timeType"]) ?? null,
    measures,
    defaultMeasure: defaultMeasure(ds),
    selection,
    sourceName: ds.source.publisher ?? ds.source.name,
    needsCredentials: ds.needs_credentials,
  };
  const exploreTitle = ds.kind === "series" ? "Chart" : ds.kind === "table" ? "Preview" : "Files";
  const charts = chartsFor(site.id, ds.id);
  const sections: Section[] = [
    { id: "explore", label: exploreTitle },
    ...(charts ? [{ id: "visualizedata", label: "Indicators and charts" }] : []),
    { id: "api", label: "Use the API" },
    ...(ds.kind === "files" ? [] : [{ id: "downloads", label: "Downloads" }]),
    { id: "schema", label: ds.kind === "series" ? "Schema and series" : "Schema" },
    { id: "source", label: "Source and license" },
    { id: "endpoints", label: "Endpoints" },
  ];
  return {
    sections,
    snippets: datasetSnippets({ siteId: site.id, base, id: ds.id, kind: ds.kind, partitionColumn: ds.partition, ...selection }),
    endpoints: datasetEndpoints(ds, base, site.id),
    downloads: bulkDownloads(ds, base, site.id),
    locations: ds.kind === "series" ? describeLocations(ds, levelCounts) : null,
    client,
    exploreTitle,
    charts,
  };
}

/** Serializes the client config for an inline JSON script (no value can close the tag). */
export function clientConfigJson(config: DatasetClientConfig): string {
  return JSON.stringify(config).replace(/</g, "\\u003c");
}
