// Draws each gallery chart from its own site's API when it scrolls into view. One API being down
// only affects its own charts, each of which offers a retry.

import { displayUnit } from "../lib/format";
import type { GalleryLine } from "../lib/gallery";
import { paths } from "../lib/urls";
import { api, describeFailure, getJson, hostOf } from "./api";
import { LINE_COLORS, mountChart, renderDataTable, type ChartConfig } from "./chart";
import { toPoints, yearOverYear, type TimeType } from "./chart-math";
import { el, notice, readConfig } from "./dom";

interface Item {
  id: string;
  title: string;
  site: string;
  siteName: string;
  base: string;
  dataset: string;
  lines: GalleryLine[];
  measure: string;
  transform?: "yoy";
  unit?: string;
  timeColumn: string;
  timeType: TimeType;
}

interface LocationsDoc {
  series: { id: string; locations: string[] }[];
}

interface SeriesDoc {
  meta: { units?: Record<string, string> };
  data: Record<string, unknown>[];
}

const items = readConfig<Item[]>("gallery-config") ?? [];

async function locationFor(item: Item, line: GalleryLine): Promise<string> {
  if (line.location) return line.location;
  const doc = await getJson<LocationsDoc>(api(paths.locations(item.dataset), item.site, item.base));
  const first = doc.series.find((s) => s.id === line.series)?.locations[0];
  if (!first) throw new Error(`no locations for ${line.series}`);
  return first;
}

async function draw(item: Item, card: HTMLElement) {
  const box = card.querySelector<HTMLElement>("[data-chart]")!;
  const legend = card.querySelector<HTMLElement>("[data-legend]")!;
  const slot = card.querySelector<HTMLElement>("[data-table-slot]")!;
  const details = card.querySelector<HTMLDetailsElement>("details")!;
  box.replaceChildren(el("div", { class: "chart-state", role: "status", text: `Loading from ${item.siteName}…` }));
  try {
    const loaded = await Promise.all(
      item.lines.map(async (line) => {
        const location = await locationFor(item, line);
        const doc = await getJson<SeriesDoc>(api(paths.series(item.dataset, line.series, location, "json"), item.site, item.base));
        return { line, doc };
      }),
    );
    let unit = item.unit ?? null;
    const lines = loaded.map(({ line, doc }, i) => {
      unit ??= doc.meta.units?.[item.measure] ?? null;
      let points = toPoints(doc.data, item.timeColumn, item.measure, item.timeType);
      if (item.transform === "yoy") points = yearOverYear(points);
      return { label: line.label, points, color: LINE_COLORS[i % LINE_COLORS.length]! };
    });
    const config: ChartConfig = { title: item.title, unit: displayUnit(unit), timeType: item.timeType, lines };
    mountChart(box, config);
    if (lines.length > 1) {
      legend.replaceChildren(
        ...lines.map((l) => {
          const swatch = el("span", { class: "swatch", "aria-hidden": "true" });
          swatch.style.background = l.color;
          return el("li", {}, swatch, l.label);
        }),
      );
      legend.hidden = false;
    }
    const table = () => details.open && renderDataTable(slot, config);
    details.addEventListener("toggle", table);
    table();
    if (!details.open) slot.replaceChildren(el("p", { class: "muted small", text: "Open to see every value in a table." }));
  } catch (err) {
    const message = `${item.siteName}'s data couldn't be loaded. ${describeFailure(err, hostOf(item.base))}`;
    box.replaceChildren(el("div", { class: "chart-state" }, notice("error", message, () => void draw(item, card))));
  }
}

const observer =
  "IntersectionObserver" in window
    ? new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            observer?.unobserve(entry.target);
            const item = items.find((i) => i.id === (entry.target as HTMLElement).dataset.galleryItem);
            if (item) void draw(item, entry.target as HTMLElement);
          }
        },
        { rootMargin: "300px 0px" },
      )
    : null;

// The first charts load right away; the rest when they come near the viewport.
const EAGER = 4;
items.forEach((item, i) => {
  const card = document.querySelector<HTMLElement>(`[data-gallery-item="${CSS.escape(item.id)}"]`);
  if (!card) return;
  if (observer && i >= EAGER) observer.observe(card);
  else void draw(item, card);
});
