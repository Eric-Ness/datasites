// A small responsive SVG line chart with a keyboard-readable cursor and a data-table fallback.
// Hand-built instead of a charting library: the sites need one chart type, and this keeps each
// page's JavaScript to a few kilobytes.

import {
  decimate,
  describeSeries,
  formatMeasure,
  formatTime,
  nearestIndex,
  niceTicks,
  segments,
  stepDecimals,
  timeTicks,
  valueExtent,
  type Point,
  type TimeType,
} from "./chart-math";

export interface ChartLine {
  label: string;
  points: Point[];
  /** A CSS color, usually a custom property such as var(--accent). */
  color: string;
}

export interface ChartConfig {
  /** The accessible name, also shown above the chart by the page. */
  title: string;
  unit?: string | null;
  timeType: TimeType;
  lines: ChartLine[];
}

/** Scales and margins of the last drawing, in box pixels. */
interface Frame {
  x: (t: number) => number;
  y: (v: number) => number;
  left: number;
  top: number;
  innerH: number;
  width: number;
}

export const LINE_COLORS = ["var(--accent)", "var(--chart-2)", "var(--muted)"];

const SVG = "http://www.w3.org/2000/svg";
let counter = 0;

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function fmtTick(v: number, decimals: number): string {
  // GDP-sized axes read better as "40T" than "40,000,000,000,000"; the tooltip and table keep exact values.
  if (Math.abs(v) >= 1e6) return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(v);
}

/** Draws `config` into `box` and keeps it sized to the box. Returns a function that removes it. */
export function mountChart(box: HTMLElement, config: ChartConfig): () => void {
  const id = `chart-${++counter}`;
  const lines = config.lines.filter((l) => l.points.length);
  box.replaceChildren();
  if (!lines.length || !valueExtent(lines.map((l) => l.points))) {
    const empty = document.createElement("div");
    empty.className = "chart-state";
    empty.textContent = "This series has no values to chart.";
    box.append(empty);
    return () => box.replaceChildren();
  }

  // The line with the most points drives the cursor.
  const key = lines.reduce((a, b) => (b.points.length > a.points.length ? b : a));
  const description = lines
    .map((l) => `${lines.length > 1 ? `${l.label}: ` : ""}${describeSeries(l.points, config.timeType, config.unit)}`)
    .join(" ");

  box.tabIndex = 0;
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", `${config.title}. Use the left and right arrow keys to read the values.`);

  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  const live = document.createElement("div");
  live.className = "visually-hidden";
  live.setAttribute("aria-live", "polite");

  let active = -1;
  let width = 0;
  let frame: Frame | null = null;
  let cursor: SVGGElement | null = null;

  const valuesAt = (t: number) =>
    lines.map((line) => {
      const point = line.points[nearestIndex(line.points, t)]!;
      return { line, value: point.t === t ? point.v : null };
    });

  function showCursor(index: number, announce: boolean) {
    if (!frame || !cursor) return;
    active = Math.max(0, Math.min(key.points.length - 1, index));
    const p = key.points[active]!;
    const x = frame.x(p.t);
    const rows = valuesAt(p.t);
    cursor.replaceChildren(svg("line", { x1: x, x2: x, y1: 0, y2: frame.innerH }));
    let top = frame.innerH / 2;
    for (const { line, value } of rows) {
      if (value === null) continue;
      const y = frame.y(value);
      top = Math.min(top, y);
      const dot = svg("circle", { cx: x, cy: y, r: 4.5 });
      dot.style.fill = line.color;
      cursor.append(dot);
    }
    cursor.style.display = "";

    const date = formatTime(p.t, config.timeType);
    const strong = document.createElement("strong");
    strong.textContent = date;
    tip.replaceChildren(strong);
    for (const { line, value } of rows) {
      const row = document.createElement("span");
      row.style.display = "block";
      row.textContent = `${lines.length > 1 ? `${line.label}: ` : ""}${formatMeasure(value, config.unit)}`;
      tip.append(row);
    }
    tip.hidden = false;
    // The tooltip is positioned in box pixels, so add the plot margins. Near an edge it flips sides.
    const left = frame.left + x;
    const edge = 80;
    tip.style.left = `${left}px`;
    tip.style.top = `${frame.top + Math.max(top, 24)}px`;
    tip.style.transform =
      left < edge ? "translate(0, calc(-100% - 12px))" : left > frame.width - edge ? "translate(-100%, calc(-100% - 12px))" : "";
    if (announce) {
      live.textContent = `${date}: ${rows
        .map(({ line, value }) => `${lines.length > 1 ? `${line.label} ` : ""}${formatMeasure(value, config.unit)}`)
        .join(", ")}`;
    }
  }

  function hideCursor() {
    if (cursor) cursor.style.display = "none";
    tip.hidden = true;
  }

  function draw() {
    width = Math.max(260, Math.round(box.clientWidth || 600));
    const height = Math.round(Math.min(380, Math.max(220, width * 0.46)));
    const extent = valueExtent(lines.map((l) => l.points))!;
    const yTicks = niceTicks(extent[0], extent[1], Math.max(3, Math.round(height / 60)));
    const decimals = stepDecimals((yTicks[1] ?? 1) - (yTicks[0] ?? 0));
    const labels = yTicks.map((v) => fmtTick(v, decimals));
    const m = { top: 22, right: 16, bottom: 30, left: Math.max(...labels.map((l) => l.length)) * 7.2 + 14 };
    const innerW = Math.max(40, width - m.left - m.right);
    const innerH = height - m.top - m.bottom;
    const t0 = Math.min(...lines.map((l) => l.points[0]!.t));
    const t1 = Math.max(...lines.map((l) => l.points[l.points.length - 1]!.t));
    const y0 = yTicks[0]!;
    const y1 = yTicks[yTicks.length - 1]!;
    const x = (t: number) => (t1 === t0 ? innerW / 2 : ((t - t0) / (t1 - t0)) * innerW);
    const y = (v: number) => (y1 === y0 ? innerH / 2 : innerH - ((v - y0) / (y1 - y0)) * innerH);
    frame = { x, y, left: m.left, top: m.top, innerH, width };

    const root = svg("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width,
      height,
      role: "img",
      "aria-labelledby": `${id}-title ${id}-desc`,
    });
    const title = svg("title", { id: `${id}-title` });
    title.textContent = config.title;
    const desc = svg("desc", { id: `${id}-desc` });
    desc.textContent = description;
    root.append(title, desc);

    const plot = svg("g", { transform: `translate(${m.left},${m.top})` });
    const grid = svg("g", { class: "chart-grid" });
    const axis = svg("g", { class: "chart-axis", "aria-hidden": "true" });
    yTicks.forEach((v, i) => {
      const yy = y(v);
      grid.append(svg("line", { x1: 0, x2: innerW, y1: yy, y2: yy }));
      const label = svg("text", { x: -10, y: yy, "text-anchor": "end", "dominant-baseline": "middle" });
      label.textContent = labels[i]!;
      axis.append(label);
    });
    if (config.unit) {
      const unit = svg("text", { x: 4, y: -9, "text-anchor": "start" });
      unit.textContent = config.unit;
      axis.append(unit);
    }
    const xt = timeTicks(t0, t1, Math.max(2, Math.floor(innerW / 84)));
    for (const t of xt.values) {
      const xx = x(t);
      const tick = svg("line", { x1: xx, x2: xx, y1: innerH, y2: innerH + 5 });
      tick.style.stroke = "var(--muted)";
      axis.append(tick);
      const label = svg("text", { x: xx, y: innerH + 20, "text-anchor": "middle" });
      label.textContent = xt.format(t);
      axis.append(label);
    }
    const baseline = svg("line", { x1: 0, x2: innerW, y1: innerH, y2: innerH });
    baseline.style.stroke = "var(--muted)";
    plot.append(grid, axis, baseline);

    for (const line of lines) {
      const g = svg("g", { "aria-hidden": "true" });
      for (const run of segments(decimate(line.points, Math.ceil(innerW)))) {
        if (run.length === 1) {
          const dot = svg("circle", { cx: x(run[0]!.t), cy: y(run[0]!.v!), r: 2 });
          dot.style.fill = line.color;
          g.append(dot);
          continue;
        }
        const d = run.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v!).toFixed(1)}`).join("");
        const path = svg("path", { d, class: "chart-line" });
        path.style.stroke = line.color;
        g.append(path);
      }
      plot.append(g);
    }

    cursor = svg("g", { class: "chart-cursor", "aria-hidden": "true" });
    cursor.style.display = "none";
    plot.append(cursor);

    const overlay = svg("rect", { x: 0, y: 0, width: innerW, height: innerH, fill: "transparent" });
    overlay.addEventListener("pointermove", (event) => {
      const rect = overlay.getBoundingClientRect();
      const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
      showCursor(nearestIndex(key.points, t0 + ratio * (t1 - t0)), false);
    });
    overlay.addEventListener("pointerleave", () => {
      if (document.activeElement !== box) hideCursor();
    });
    plot.append(overlay);
    root.append(plot);

    box.replaceChildren(root, tip, live);
    if (active >= 0 && document.activeElement === box) showCursor(active, false);
  }

  box.addEventListener("keydown", (event) => {
    const n = key.points.length;
    const big = Math.max(1, Math.round(n / 20));
    let next = active < 0 ? n - 1 : active;
    if (event.key === "ArrowLeft") next -= event.shiftKey ? big : 1;
    else if (event.key === "ArrowRight") next += event.shiftKey ? big : 1;
    else if (event.key === "PageUp") next -= big;
    else if (event.key === "PageDown") next += big;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = n - 1;
    else if (event.key === "Escape") {
      hideCursor();
      return;
    } else return;
    event.preventDefault();
    showCursor(next, true);
  });
  box.addEventListener("focus", () => showCursor(active < 0 ? key.points.length - 1 : active, true));
  box.addEventListener("blur", hideCursor);

  draw();
  let pending = 0;
  const observer = new ResizeObserver(() => {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => {
      if (Math.round(box.clientWidth) !== width) draw();
    });
  });
  observer.observe(box);
  return () => {
    observer.disconnect();
    box.replaceChildren();
  };
}

/** A plain table of the charted values (newest first), for people who cannot use the chart. */
export function renderDataTable(slot: HTMLElement, config: ChartConfig, maxRows = 1000): void {
  const times = new Map<number, string>();
  const byLine = config.lines.map((line) => {
    const map = new Map<number, number | null>();
    for (const p of line.points) {
      map.set(p.t, p.v);
      if (!times.has(p.t)) times.set(p.t, p.label);
    }
    return map;
  });
  const sorted = [...times.keys()].sort((a, b) => b - a);
  const shown = sorted.slice(0, maxRows);
  const count = (n: number) => n.toLocaleString("en-US");

  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.tabIndex = 0;
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-label", `${config.title}: data table`);
  const table = document.createElement("table");
  const caption = document.createElement("caption");
  caption.textContent =
    shown.length < sorted.length
      ? `The latest ${count(shown.length)} of ${count(sorted.length)} values, newest first. The CSV has all of them.`
      : `${count(sorted.length)} values, newest first.`;
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const th = (text: string, cls?: string) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = text;
    if (cls) cell.className = cls;
    return cell;
  };
  headRow.append(th("Date"), ...config.lines.map((l) => th(config.unit ? `${l.label} (${config.unit})` : l.label, "num")));
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const t of shown) {
    const tr = document.createElement("tr");
    const date = document.createElement("th");
    date.scope = "row";
    date.style.fontWeight = "400";
    date.textContent = times.get(t) ?? formatTime(t, config.timeType);
    tr.append(date);
    for (const map of byLine) {
      const td = document.createElement("td");
      td.className = "num";
      const v = map.get(t);
      td.textContent = v === undefined || v === null ? "—" : formatMeasure(v, null);
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(caption, head, body);
  wrap.append(table);
  slot.replaceChildren(wrap);
}
