// Draws an indicator page's trend from the values built into the page (no API request).

import { displayUnit } from "../lib/format";
import { LINE_COLORS, mountChart, renderDataTable, type ChartConfig } from "./chart";
import { parseTime } from "./chart-math";
import { readConfig } from "./dom";

interface TrendConfig {
  title: string;
  unit: string;
  label: string;
  /** [year, value] pairs. */
  points: [number, number][];
}

const trend = readConfig<TrendConfig>("indicator-chart");
const box = document.querySelector<HTMLElement>("[data-chart]");
if (trend && box) {
  const config: ChartConfig = {
    title: trend.title,
    unit: displayUnit(trend.unit),
    timeType: "year",
    lines: [
      {
        label: trend.label,
        color: LINE_COLORS[0]!,
        points: trend.points.map(([year, v]) => ({ t: parseTime(String(year), "year"), v, label: String(year) })),
      },
    ],
  };
  mountChart(box, config);
  const details = document.querySelector<HTMLDetailsElement>("details.data-table");
  const slot = document.querySelector<HTMLElement>("[data-table-slot]");
  if (details && slot) details.addEventListener("toggle", () => details.open && renderDataTable(slot, config));
}
