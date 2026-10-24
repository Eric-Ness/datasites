import { describe, expect, it } from "vitest";
import { datasets, sites } from "./fixtures/registry";
import { COMMON_PAGES, datasetPath, isCurrent, navItems, robotsTxt, sitePages, sitemapXml } from "../src/lib/routes";

describe("sitePages", () => {
  it("gives every site the common pages", () => {
    for (const site of sites) {
      const pages = sitePages(site, datasets).map((p) => p.path);
      for (const p of ["/", "/datasets/", "/docs/", "/docs/reference/", "/docs/errors/", "/status/", "/sources/", "/privacy/"]) {
        expect(pages, `${site.id} ${p}`).toContain(p);
      }
    }
  });

  it("adds one page per dataset of the site, of every kind", () => {
    const climate = sitePages({ id: "climatedata" }, datasets).map((p) => p.path);
    expect(climate).toContain("/datasets/co2-monthly/");
    expect(climate).toContain("/datasets/county-series/");
    expect(climate).not.toContain("/datasets/factor-table/");
    const carbon = sitePages({ id: "carbondata" }, datasets).map((p) => p.path);
    expect(carbon).toEqual(expect.arrayContaining(["/datasets/factor-table/", "/datasets/grid-table/"]));
    const viz = sitePages({ id: "visualizedata" }, datasets).map((p) => p.path);
    expect(viz).toContain("/datasets/boundary-files/");
  });

  it("gives only visualizedata a gallery", () => {
    expect(sitePages({ id: "visualizedata" }, datasets).map((p) => p.path)).toContain("/gallery/");
    expect(sitePages({ id: "climatedata" }, datasets).map((p) => p.path)).not.toContain("/gallery/");
    expect(navItems("visualizedata").map((n) => n.href)).toContain("/gallery/");
    expect(navItems("waterdata").map((n) => n.href)).not.toContain("/gallery/");
  });

  it("covers a site with no datasets", () => {
    expect(sitePages({ id: "airdata" }, datasets)).toHaveLength(COMMON_PAGES.length);
  });

  it("has no duplicate paths", () => {
    for (const site of sites) {
      const paths = sitePages(site, datasets).map((p) => p.path);
      expect(new Set(paths).size).toBe(paths.length);
    }
  });
});

describe("navigation", () => {
  it("marks the current section", () => {
    expect(isCurrent("/docs/", "/docs/reference/")).toBe(true);
    expect(isCurrent("/docs/", "/docs")).toBe(true);
    expect(isCurrent("/datasets/", "/docs/")).toBe(false);
    expect(isCurrent("/", "/")).toBe(true);
    expect(isCurrent("/", "/status/")).toBe(false);
  });
});

describe("sitemap and robots", () => {
  it("lists every page on the apex domain", () => {
    const pages = sitePages({ id: "climatedata" }, datasets);
    const xml = sitemapXml("climatedata.dev", pages);
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
    const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
    expect(locs).toEqual(pages.map((p) => `https://climatedata.dev${p.path}`));
    expect(locs).toContain(`https://climatedata.dev${datasetPath("co2-monthly")}`);
  });

  it("points robots.txt at the sitemap", () => {
    expect(robotsTxt("waterdata.dev")).toContain("Sitemap: https://waterdata.dev/sitemap.xml");
  });
});
