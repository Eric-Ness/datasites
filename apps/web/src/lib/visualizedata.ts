// visualizedata.org draws charts, maps and indicators from these sites' APIs. visualizedata.json records which
// of its pages use each dataset, and each indicator's definition and a summary of its values
// (scripts/sync-visualizedata.mjs copies them from visualizedata.org's repository).

import { location } from "@datasites/geo";
import type { Dataset } from "@datasites/registry";
import doc from "./visualizedata.json";

export const VISUALIZEDATA_ORG = "https://visualizedata.org";

export interface ChartLink {
  label: string;
  /** Absolute URL on visualizedata.org. */
  href: string;
}

export interface IndicatorLink extends ChartLink {
  /** The indicator's id, also its page on this site: /indicators/<id>/. */
  id: string;
}

export interface DatasetCharts {
  /** Indicators: one value per country, state or county, charted and mapped. */
  indicators: IndicatorLink[];
  /** Pages that draw on the dataset (the planet, space, topic pages). */
  pages: ChartLink[];
}

export interface PlaceValue {
  id: string;
  name: string;
  value: number;
}

export interface Indicator {
  id: string;
  site: string;
  dataset: string;
  name: string;
  unit: string;
  decimals: number;
  /** How visualizedata.org formats values: "margin", "signed", "compact", "dollars" or null. */
  format: string | null;
  better: "up" | "down" | null;
  /** "us": states and counties; "world": countries. */
  scope: "us" | "world";
  /** Absolute URL of the indicator on visualizedata.org. */
  href: string;
  /** How it is computed from the dataset. */
  method: string | null;
  note: string | null;
  /** The national or world series, or the median place each year. [year, value] pairs. */
  trend: { label: string; points: [number, number][] };
  latest: {
    year: number;
    level: string;
    count: number;
    highest: PlaceValue[];
    lowest: PlaceValue[];
    /** How many places share the highest and the lowest value (often more than the five listed). */
    highestTies: number;
    lowestTies: number;
  } | null;
}

type Link = { label: string; href: string };
type Entry = { pages: Link[]; indicators: (Link & { id: string })[] };
const entries = doc.datasets as Record<string, Entry>;
const all = Object.values(doc.indicators as unknown as Record<string, Omit<Indicator, "href"> & { href: string }>).map(
  (i): Indicator => ({ ...i, href: `${VISUALIZEDATA_ORG}${i.href}` }),
);

const absolute = <T extends Link>(link: T): T => ({ ...link, href: `${VISUALIZEDATA_ORG}${link.href}` });

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

/** A site's indicators whose dataset is among `datasets`, US ones first, then by name. */
export function indicatorsForSite(siteId: string, datasets: readonly Pick<Dataset, "id" | "site">[]): Indicator[] {
  const have = new Set(datasets.filter((d) => d.site === siteId).map((d) => d.id));
  return all
    .filter((i) => i.site === siteId && have.has(i.dataset))
    .sort((a, b) => (a.scope === b.scope ? a.name.localeCompare(b.name) : a.scope === "us" ? -1 : 1));
}

export function indicatorPath(id: string): string {
  return `/indicators/${id}/`;
}

/** "Adams County, CO": county names repeat across states, so counties get their state's code. */
export function placeLabel(place: Pick<PlaceValue, "id" | "name">): string {
  if (!place.id.startsWith("us-county-")) return place.name;
  const parent = location(place.id)?.parent;
  const state = parent ? location(parent)?.usps : undefined;
  return state ? `${place.name}, ${state}` : place.name;
}

/** visualizedata.org's notes are fragments ("the AQI that only ..."); as a sentence: "The AQI that only ....". */
export function sentence(text: string): string {
  const t = text.trim();
  if (!t) return t;
  const capital = t[0]!.toUpperCase() + t.slice(1);
  return /[.!?)]$/.test(capital) ? capital : `${capital}.`;
}

/** A value the way visualizedata.org shows it: "R+12.3 pts", "+1.2 °F", "4.5M jobs", "$1,234", "45.2%". */
export function formatIndicator(value: number, ind: Pick<Indicator, "unit" | "decimals" | "format">): string {
  const fixed = (v: number) =>
    new Intl.NumberFormat("en-US", { minimumFractionDigits: ind.decimals, maximumFractionDigits: ind.decimals }).format(v);
  if (ind.format === "margin") {
    // (Republican − Democratic) / all votes: positive is a Republican lead.
    if (value === 0) return "Even";
    return `${value > 0 ? "R" : "D"}+${fixed(Math.abs(value))}`;
  }
  if (ind.format === "dollars") return `$${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value)}`;
  const unit = ind.unit.replace(/^US\$\s*/, "");
  const prefix = ind.unit.startsWith("US$") ? "$" : "";
  let number: string;
  if (ind.format === "compact") {
    number = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
  } else if (ind.format === "signed") {
    number = `${value > 0 ? "+" : value < 0 ? "−" : ""}${fixed(Math.abs(value))}`;
  } else {
    number = fixed(value);
  }
  if (!unit) return `${prefix}${number}`;
  return unit.startsWith("%") ? `${prefix}${number}${unit}` : `${prefix}${number} ${unit}`;
}
