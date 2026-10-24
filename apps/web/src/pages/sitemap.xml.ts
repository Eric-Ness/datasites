import type { APIRoute } from "astro";
import { datasets, site } from "../lib/site";
import { sitePages, sitemapXml } from "../lib/routes";

export const GET: APIRoute = () =>
  new Response(sitemapXml(site.domain, sitePages(site, datasets)), { headers: { "content-type": "application/xml; charset=utf-8" } });
