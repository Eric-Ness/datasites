// Smoke tests over the real registry: every dataset the pipelines add must produce a page,
// endpoints and snippets without code changes.
import { describe, expect, it } from "vitest";
import { datasets, sites } from "@datasites/registry";
import { datasetPageModel } from "../src/lib/dataset-page";
import { datasetJsonLd } from "../src/lib/jsonld";
import { sitePages } from "../src/lib/routes";
import { apiBaseFor } from "../src/lib/urls";

describe("the real registry", () => {
  it("has sites with unique ids and domains", () => {
    expect(sites.length).toBeGreaterThan(0);
    expect(new Set(sites.map((s) => s.id)).size).toBe(sites.length);
    expect(new Set(sites.map((s) => s.domain)).size).toBe(sites.length);
  });

  it("gives every dataset a page model, JSON-LD and a sitemap entry", () => {
    for (const ds of datasets) {
      const site = sites.find((s) => s.id === ds.site);
      expect(site, `${ds.site}/${ds.id}: unknown site`).toBeDefined();
      const model = datasetPageModel(site!, ds, apiBaseFor(site!.domain));
      expect(model.endpoints.length, ds.id).toBeGreaterThan(2);
      expect(model.snippets.curl, ds.id).toContain(`https://api.${site!.domain}/v1/${ds.id}`);
      expect(datasetJsonLd(site!, ds).name).toBe(ds.title);
      expect(sitePages(site!, datasets).map((p) => p.path)).toContain(`/datasets/${ds.id}/`);
    }
  });
});
