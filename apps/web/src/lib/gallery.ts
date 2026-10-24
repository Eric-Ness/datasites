// The visualizedata gallery: charts drawn in the browser from the sibling sites' APIs.
// Curated charts come first; every other series dataset in the registry gets a default chart,
// so new datasets appear in the gallery on the next build without code changes.

import type { Dataset, Site } from "@datasites/registry";
import { sampleLocation, seriesIds } from "./datasets";

export interface GalleryLine {
  series: string;
  /** null: use the first location the API lists for this series. */
  location: string | null;
  label: string;
}

export interface GalleryItem {
  id: string;
  title: string;
  description: string;
  site: string;
  dataset: string;
  lines: GalleryLine[];
  /** Column to chart; defaults to "value". */
  measure?: string;
  /** "yoy": change over the previous 12 months. */
  transform?: "yoy";
  /** Overrides the unit the API reports (for transformed values). */
  unit?: string;
}

/** Hand-picked charts. Each is shown only while its dataset, series and locations are in the registry. */
export const CURATED: GalleryItem[] = [
  {
    id: "keeling-curve",
    title: "The Keeling curve",
    description:
      "Monthly mean carbon dioxide at Mauna Loa Observatory since March 1958, the longest continuous direct record of atmospheric CO2.",
    site: "climatedata",
    dataset: "co2-monthly",
    lines: [{ series: "average", location: "noaa-mlo", label: "Mauna Loa" }],
  },
  {
    id: "co2-mauna-loa-vs-global",
    title: "Mauna Loa and the global average",
    description:
      "The global marine-surface average, which starts in 1979, next to the Mauna Loa record. A single high-altitude station follows the global picture closely.",
    site: "climatedata",
    dataset: "co2-monthly",
    lines: [
      { series: "average", location: "noaa-mlo", label: "Mauna Loa" },
      { series: "average", location: "world", label: "Global marine surface" },
    ],
  },
  {
    id: "co2-seasons-removed",
    title: "CO2 with the seasons taken out",
    description:
      "The monthly mean rises and falls with the Northern Hemisphere growing season. The trend series removes the average seasonal cycle, leaving the long-term rise.",
    site: "climatedata",
    dataset: "co2-monthly",
    lines: [
      { series: "average", location: "noaa-mlo", label: "Monthly mean" },
      { series: "trend", location: "noaa-mlo", label: "Seasonal cycle removed" },
    ],
  },
  {
    id: "co2-growth",
    title: "How fast CO2 is rising",
    description:
      "The change in Mauna Loa's seasonally adjusted CO2 over the previous 12 months, computed in your browser from the trend series.",
    site: "climatedata",
    dataset: "co2-monthly",
    lines: [{ series: "trend", location: "noaa-mlo", label: "12-month change" }],
    transform: "yoy",
    unit: "ppm per year",
  },
];

function hasLine(ds: Dataset, line: GalleryLine): boolean {
  if (!(line.series in ds.series)) return false;
  if (line.location === null) return true;
  const explicit = ds.locations.explicit ?? {};
  return line.location in explicit || line.location === sampleLocation(ds);
}

/** The gallery for the current registry: curated charts whose data exists, then one default chart per other series dataset. */
export function galleryItems(
  sites: readonly Pick<Site, "id">[],
  datasets: readonly Dataset[],
  locationName: (id: string) => string | undefined = () => undefined,
): GalleryItem[] {
  const siteIds = new Set(sites.map((s) => s.id));
  const find = (site: string, id: string) => datasets.find((d) => d.site === site && d.id === id);
  const curated = CURATED.filter((item) => {
    const ds = find(item.site, item.dataset);
    return ds && siteIds.has(item.site) && item.lines.every((line) => hasLine(ds, line));
  });
  const covered = new Set(curated.map((item) => `${item.site}/${item.dataset}`));
  const generated: GalleryItem[] = datasets
    .filter((ds) => ds.kind === "series" && !ds.discontinued && siteIds.has(ds.site) && !covered.has(`${ds.site}/${ds.id}`))
    .flatMap((ds) => {
      const series = seriesIds(ds)[0];
      if (!series) return [];
      const location = sampleLocation(ds);
      const place = location ? (ds.locations.explicit?.[location] ?? locationName(location) ?? location) : null;
      return [
        {
          id: `${ds.site}-${ds.id}`,
          title: place ? `${ds.title}: ${place}` : ds.title,
          description: ds.description,
          site: ds.site,
          dataset: ds.id,
          lines: [{ series, location, label: ds.series[series]! }],
        },
      ];
    });
  return [...curated, ...generated];
}
