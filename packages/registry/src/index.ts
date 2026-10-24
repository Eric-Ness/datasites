import sitesJson from "./sites.json";

export interface Site {
  id: string;
  domain: string;
  name: string;
  tagline: string;
  description: string;
  status: "coming-soon" | "live";
  phase: number;
}

export const sites: readonly Site[] = sitesJson as Site[];

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
