import sitesJson from "./sites.json";
import datasetsJson from "./datasets.json";

export interface Site {
  id: string;
  domain: string;
  name: string;
  tagline: string;
  description: string;
  status: "coming-soon" | "live";
  phase: number;
}

export type DatasetKind = "series" | "table" | "files";
export type ColumnType = "month" | "date" | "year" | "datetime" | "float" | "int" | "str" | "bool";

export interface Column {
  name: string;
  type: ColumnType;
  unit: string | null;
  nullable: boolean;
  description: string | null;
}

export interface Dataset {
  site: string;
  id: string;
  kind: DatasetKind;
  title: string;
  description: string;
  cadence: string;
  check_every: string;
  sla: string;
  discontinued: boolean;
  /** The pipeline needs a key or account (contract requires_env); until one is set up it never publishes. */
  needs_credentials: boolean;
  source: {
    name: string;
    publisher: string | null;
    url: string;
    license: string;
    commercial_ok: boolean | null;
    attribution: string;
  };
  /** Series datasets: series id -> display name. */
  series: Record<string, string>;
  /** Series datasets: where location ids come from (see contracts.py Locations). */
  locations: { explicit?: Record<string, string>; registry?: string[]; pattern?: string };
  schema: Column[];
  /** Table datasets */
  key: string[];
  partition: string | null;
}

export const sites: readonly Site[] = sitesJson as Site[];
export const datasets: readonly Dataset[] = datasetsJson as Dataset[];

const byId = new Map(sites.map((site) => [site.id, site]));

const byHost = new Map<string, Site>();
for (const site of sites) {
  byHost.set(site.domain, site);
  byHost.set(`www.${site.domain}`, site);
  byHost.set(`api.${site.domain}`, site);
}

export function siteById(id: string): Site | undefined {
  return byId.get(id);
}

/** Resolves a site, www or api hostname to its site. */
export function siteByHost(hostname: string): Site | undefined {
  return byHost.get(hostname.toLowerCase());
}

export function datasetsForSite(siteId: string): Dataset[] {
  return datasets.filter((d) => d.site === siteId);
}

export function dataset(siteId: string, id: string): Dataset | undefined {
  return datasets.find((d) => d.site === siteId && d.id === id);
}
