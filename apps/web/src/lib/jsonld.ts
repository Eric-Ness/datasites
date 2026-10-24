// schema.org Dataset metadata for dataset pages (https://schema.org/Dataset, and Google's dataset guidelines).

import type { Dataset, Site } from "@datasites/registry";
import { licenseInfo } from "./format";
import { datasetPath } from "./routes";
import { apiBaseFor, apiUrl, paths, siteUrl } from "./urls";

const ENCODING: Record<string, string> = { csv: "text/csv", parquet: "application/vnd.apache.parquet" };

/**
 * Always describes the public production API (https://api.<domain>), even in a local build, because
 * search engines should only ever see the canonical URLs.
 */
export function datasetJsonLd(site: Site, ds: Dataset): Record<string, unknown> {
  const base = apiBaseFor(site.domain);
  const licenseUrls = licenseInfo(ds.source.license)
    .parts.map((p) => p.url)
    .filter((u): u is string => u !== null);
  const distribution =
    ds.kind === "files"
      ? [
          {
            "@type": "DataDownload",
            name: `${ds.title}: file index and metadata`,
            encodingFormat: "application/json",
            contentUrl: apiUrl(base, site.id, paths.dataset(ds.id)),
          },
        ]
      : (["csv", "parquet"] as const).map((format) => ({
          "@type": "DataDownload",
          name: `${ds.id}.${format}`,
          encodingFormat: ENCODING[format],
          contentUrl: apiUrl(base, site.id, paths.download(ds.id, format)),
        }));
  const variables = ds.schema
    .filter((c) => !["date", "series", "location"].includes(c.name))
    .map((c) => ({
      "@type": "PropertyValue",
      name: c.name,
      ...(c.unit ? { unitText: c.unit } : {}),
      ...(c.description ? { description: c.description } : {}),
    }));
  return {
    "@context": "https://schema.org",
    "@type": "Dataset",
    name: ds.title,
    description: ds.description,
    url: siteUrl(site.domain, datasetPath(ds.id)),
    identifier: `${site.id}/${ds.id}`,
    isAccessibleForFree: true,
    license: licenseUrls.length === 1 ? licenseUrls[0] : licenseUrls.length ? licenseUrls : ds.source.url,
    creditText: ds.source.attribution,
    creator: {
      "@type": "Organization",
      name: ds.source.publisher ?? ds.source.name,
      url: ds.source.url,
    },
    publisher: { "@type": "Organization", name: site.name, url: siteUrl(site.domain) },
    isBasedOn: ds.source.url,
    includedInDataCatalog: { "@type": "DataCatalog", name: `${site.name} datasets`, url: siteUrl(site.domain, "/datasets/") },
    distribution,
    ...(variables.length ? { variableMeasured: variables } : {}),
  };
}

/** Serializes JSON-LD for a <script> element: "<" is escaped so no value can close the tag. */
export function jsonLdScript(data: unknown): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
