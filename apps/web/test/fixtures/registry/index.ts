// A stand-in for @datasites/registry with example datasets of every kind (series with explicit,
// registry and pattern locations; partitioned and unpartitioned tables; files). The contracts are
// test fixtures, not real sources. Unit tests import it directly; `build-all.mjs --fixtures`
// builds the sites from it by aliasing @datasites/registry here.
import type { Dataset, Site } from "@datasites/registry";
import sitesJson from "./sites.json";
import datasetsJson from "./datasets.json";

export type { Column, ColumnType, Dataset, DatasetKind, Site } from "@datasites/registry";

export const sites: readonly Site[] = sitesJson as Site[];
export const datasets: readonly Dataset[] = datasetsJson as Dataset[];

export function siteById(id: string): Site | undefined {
  return sites.find((s) => s.id === id);
}

export function siteByHost(hostname: string): Site | undefined {
  const host = hostname.toLowerCase().replace(/^(www|api)\./, "");
  return sites.find((s) => s.domain === host);
}

export function datasetsForSite(siteId: string): Dataset[] {
  return datasets.filter((d) => d.site === siteId);
}

export function dataset(siteId: string, id: string): Dataset | undefined {
  return datasets.find((d) => d.site === siteId && d.id === id);
}
