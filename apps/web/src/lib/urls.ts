// URL building for the API and the sites. Pure: used at build time for the static pages and
// bundled into the browser scripts, so both always produce the same URLs.

/** Hosts where the API cannot tell the site from the hostname and needs ?site=<id> (matches apps/api middleware). */
export function isDevHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host.endsWith(".localhost") ||
    host.endsWith(".workers.dev")
  );
}

/** The API base for a site: PUBLIC_API_BASE when set at build time, otherwise https://api.<domain>. */
export function apiBaseFor(domain: string, override?: string | null): string {
  const raw = override?.trim();
  const base = raw ? raw : `https://api.${domain}`;
  const url = new URL(base);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error(`PUBLIC_API_BASE must be http(s): ${base}`);
  return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
}

export function needsSiteParam(base: string): boolean {
  return isDevHost(new URL(base).hostname);
}

/**
 * The full URL of an API path ("/v1/catalog") for a site. Local and workers.dev bases get ?site=<id>,
 * because the local API serves every site from one host.
 */
export function apiUrl(base: string, siteId: string, path: string): string {
  if (!path.startsWith("/")) throw new Error(`API paths start with "/": ${path}`);
  const url = new URL(base.replace(/\/+$/, "") + path);
  if (needsSiteParam(base)) url.searchParams.set("site", siteId);
  return url.href;
}

/** One path segment, percent-encoded (dataset ids are kebab-case, but partition values can be anything). */
export function seg(value: string): string {
  return encodeURIComponent(value);
}

/** "co2-monthly" or "co2-monthly@2026-10-24.d0afea5995". */
export function datasetRef(id: string, version?: string | null): string {
  return version ? `${seg(id)}@${seg(version)}` : seg(id);
}

export type DataFormat = "json" | "csv";
export type BulkFormat = "csv" | "parquet";

/** Every API path the sites use, in one place. */
export const paths = {
  root: () => "/v1",
  catalog: () => "/v1/catalog",
  status: () => "/v1/status",
  dataset: (id: string, version?: string | null) => `/v1/${datasetRef(id, version)}`,
  versions: (id: string) => `/v1/${seg(id)}/versions`,
  locations: (id: string) => `/v1/${seg(id)}/locations`,
  series: (id: string, series: string, location: string, format: DataFormat = "json", version?: string | null) =>
    `/v1/${datasetRef(id, version)}/${seg(series)}/${seg(location)}.${format}`,
  table: (id: string, partition: string, format: DataFormat = "json", version?: string | null) =>
    `/v1/${datasetRef(id, version)}/table/${seg(partition)}.${format}`,
  download: (id: string, format: BulkFormat, version?: string | null) =>
    `/v1/${datasetRef(id, version)}/download/${seg(id)}.${format}`,
  /** A file of a files-kind dataset, at the path the pipeline gave it. */
  file: (id: string, path: string, version?: string | null) =>
    `/v1/${datasetRef(id, version)}/${path.split("/").map(seg).join("/")}`,
};

/** A page on a site's own domain (canonical URLs, sitemap, JSON-LD). */
export function siteUrl(domain: string, path = "/"): string {
  return `https://${domain}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Turns an API-relative link returned by the API ("/v1/x/download/x.csv") into a full URL. */
export function resolveApiLink(base: string, siteId: string, link: string): string {
  if (/^https?:\/\//i.test(link)) return link;
  const [path, query] = link.split("?", 2) as [string, string | undefined];
  const url = new URL(apiUrl(base, siteId, path));
  if (query) for (const [k, v] of new URLSearchParams(query)) url.searchParams.set(k, v);
  return url.href;
}
