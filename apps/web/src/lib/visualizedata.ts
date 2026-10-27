// visualizedata.org draws charts, maps and indicators from these sites' APIs. visualizedata.json records which
// of its pages use each dataset (scripts/sync-visualizedata.mjs copies it from visualizedata.org's repository).

import doc from "./visualizedata.json";

export const VISUALIZEDATA_ORG = "https://visualizedata.org";

export interface ChartLink {
  label: string;
  /** Absolute URL on visualizedata.org. */
  href: string;
}

export interface DatasetCharts {
  /** Indicators: one value per country, state or county, charted and mapped. */
  indicators: ChartLink[];
  /** Pages that draw on the dataset (the planet, space, topic pages). */
  pages: ChartLink[];
}

type Entry = { pages: { label: string; href: string }[]; indicators: { label: string; href: string }[] };
const entries = doc.datasets as Record<string, Entry>;

const absolute = (link: { label: string; href: string }): ChartLink => ({ label: link.label, href: `${VISUALIZEDATA_ORG}${link.href}` });

/** The visualizedata.org pages built from a dataset, or null when it has none. */
export function chartsFor(siteId: string, datasetId: string): DatasetCharts | null {
  const entry = entries[`${siteId}/${datasetId}`];
  if (!entry) return null;
  return { indicators: entry.indicators.map(absolute), pages: entry.pages.map(absolute) };
}

/** Every dataset key (site/id) that visualizedata.org uses. */
export function chartedDatasets(): string[] {
  return Object.keys(entries);
}
