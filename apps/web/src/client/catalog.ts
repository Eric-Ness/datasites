// Adds live coverage and publish dates to dataset cards from /v1/catalog. The cards stand on their own without it.

import { formatCoverage, relativeTime } from "../lib/format";
import { paths } from "../lib/urls";
import { api, getJson } from "./api";

interface Catalog {
  datasets: { id: string; current: string | null; published_at: string | null; coverage: { start: string | null; end: string | null } }[];
}

async function run() {
  const cards = document.querySelectorAll<HTMLElement>("[data-dataset-card]");
  if (!cards.length) return;
  let catalog: Catalog;
  try {
    catalog = await getJson<Catalog>(api(paths.catalog()));
  } catch {
    return;
  }
  const byId = new Map(catalog.datasets.map((d) => [d.id, d]));
  for (const card of cards) {
    const slot = card.querySelector<HTMLElement>("[data-coverage]");
    const entry = byId.get(card.dataset.datasetCard ?? "");
    if (!slot) continue;
    if (!entry?.current) {
      slot.textContent = "Not published yet";
      continue;
    }
    const coverage = formatCoverage(entry.coverage.start, entry.coverage.end);
    const updated = entry.published_at ? `updated ${relativeTime(entry.published_at, Date.now())}` : "";
    slot.textContent = [coverage, updated].filter(Boolean).join(" · ");
  }
}

void run();
