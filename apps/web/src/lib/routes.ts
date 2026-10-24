// Which pages each site has. The astro config, the sitemap, the navigation and the tests all read
// this, so adding a page here is the only change needed to list it everywhere.

import type { Dataset, Site } from "@datasites/registry";

export interface PageInfo {
  path: string;
  title: string;
}

/** Pages only some sites have, by site id. Each one is an entry point in src/routes/. */
export const EXTRA_PAGES: Record<string, { path: string; title: string; entrypoint: string }[]> = {
  visualizedata: [{ path: "/gallery/", title: "Gallery", entrypoint: "./src/routes/gallery.astro" }],
};

/** The pages every site has, in sitemap order (404 is served for missing paths and is not listed). */
export const COMMON_PAGES: PageInfo[] = [
  { path: "/", title: "Home" },
  { path: "/datasets/", title: "Datasets" },
  { path: "/docs/", title: "Docs" },
  { path: "/docs/reference/", title: "API reference" },
  { path: "/docs/errors/", title: "Errors" },
  { path: "/status/", title: "Status" },
  { path: "/sources/", title: "Sources and licenses" },
  { path: "/privacy/", title: "Privacy" },
];

export function datasetPath(id: string): string {
  return `/datasets/${id}/`;
}

export function extraPages(siteId: string): PageInfo[] {
  return (EXTRA_PAGES[siteId] ?? []).map(({ path, title }) => ({ path, title }));
}

/** Every indexable page of a site: the common pages, its extra pages and one page per dataset. */
export function sitePages(site: Pick<Site, "id">, datasets: readonly Pick<Dataset, "id" | "title" | "site">[]): PageInfo[] {
  const own = datasets.filter((d) => d.site === site.id);
  return [
    ...COMMON_PAGES,
    ...extraPages(site.id),
    ...own.map((d) => ({ path: datasetPath(d.id), title: d.title })),
  ];
}

export interface NavItem {
  href: string;
  label: string;
}

export function navItems(siteId: string): NavItem[] {
  return [
    { href: "/datasets/", label: "Datasets" },
    ...extraPages(siteId).map((p) => ({ href: p.path, label: p.title })),
    { href: "/docs/", label: "Docs" },
    { href: "/status/", label: "Status" },
    { href: "/sources/", label: "Sources" },
  ];
}

/** Whether a nav item is the current section ("/docs/reference/" is inside "Docs"). */
export function isCurrent(href: string, pathname: string): boolean {
  const path = pathname.endsWith("/") ? pathname : `${pathname}/`;
  return href === "/" ? path === "/" : path.startsWith(href);
}

/** The sitemap.xml body for a site. */
export function sitemapXml(domain: string, pages: readonly PageInfo[]): string {
  const urls = pages
    .map((p) => `  <url><loc>https://${domain}${p.path}</loc></url>`)
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
}

export function robotsTxt(domain: string): string {
  return `User-agent: *\nAllow: /\n\nSitemap: https://${domain}/sitemap.xml\n`;
}
