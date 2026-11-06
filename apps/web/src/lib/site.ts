// The site being built. astro.config.ts picks it from the SITE environment variable and passes
// it in through Vite `define`, so every page and component sees the same site and API base.

import { datasets, datasetsForSite, siteById, sites, type Dataset, type Site } from "@datasites/registry";
import { apiBaseFor } from "./urls";

declare const __SITE_ID__: string;
declare const __API_OVERRIDE__: string;

function current(): Site {
  const site = siteById(__SITE_ID__);
  if (!site) throw new Error(`unknown site "${__SITE_ID__}"`);
  return site;
}

export const site: Site = current();

/** This site's datasets in registry order. */
export const siteDatasets: Dataset[] = datasetsForSite(site.id);

/** PUBLIC_API_BASE as given at build time, or "" for the production APIs. */
export const apiOverride: string = __API_OVERRIDE__;

/** The API base of any site: the override when set, otherwise https://api.<domain>. */
export function apiBaseForSite(other: Pick<Site, "domain">): string {
  return apiBaseFor(other.domain, apiOverride);
}

/** Where this site's pages fetch data from. */
export const apiBase: string = apiBaseForSite(site);

/** The other launched sites (a coming-soon site has no pages to link to yet). */
export const siblings: Site[] = sites.filter((s) => s.id !== site.id && s.status === "live");

export { datasets, sites };
