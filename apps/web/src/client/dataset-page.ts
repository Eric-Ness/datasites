// The live parts of a dataset page: current version and coverage, plus the explorer for its kind
// (a chart for series, a paginated preview for tables, a file list for files).

import type { DatasetClientConfig } from "../lib/dataset-page";
import type { Selection } from "../lib/datasets";
import { displayUnit, formatBytes, formatCoverage, formatInt, formatPeriod, formatValue, relativeTime } from "../lib/format";
import { datasetSnippets, SNIPPET_LANGUAGES } from "../lib/snippets";
import { paths, resolveApiLink } from "../lib/urls";
import { api, ApiError, describeFailure, getJson, hostOf, pageApiBase, pageSiteId } from "./api";
import { LINE_COLORS, mountChart, renderDataTable, type ChartConfig } from "./chart";
import { describeSeries, toPoints } from "./chart-math";
import { el, notice, readConfig, setText } from "./dom";

interface DatasetMeta {
  version: string;
  current_version: string;
  published_at: string | null;
  coverage: { start: string | null; end: string | null };
  validation: { status: "pass" | "warn" | "blocked"; warnings: string[] };
  downloads: { name: string; bytes: number; url: string }[];
  /** Not served yet; used when a future API version lists every file of a files dataset. */
  files?: { path: string; bytes?: number; url?: string }[] | Record<string, { bytes?: number }>;
}

interface LocationsDoc {
  series: { id: string; name: string; locations: string[] }[];
  locations: { id: string; name: string }[];
}

interface SeriesDoc {
  meta: { units?: Record<string, string>; series_name?: string; location_name?: string };
  data: Record<string, unknown>[];
}

interface TableDoc {
  meta: { rows?: number; units?: Record<string, string> };
  rows: Record<string, unknown>[];
}

const cfg = readConfig<DatasetClientConfig>("dataset-config");
const host = hostOf(pageApiBase());

if (cfg) {
  void loadMeta(cfg);
  if (cfg.kind === "series") void initSeries(cfg);
  else if (cfg.kind === "table") void initTable(cfg);
  else void initFiles(cfg);
}

function updateSnippets(c: DatasetClientConfig, selection: Partial<Selection>) {
  const snippets = datasetSnippets({ siteId: pageSiteId(), base: pageApiBase(), id: c.id, kind: c.kind, partitionColumn: c.partition, ...c.selection, ...selection });
  for (const { key } of SNIPPET_LANGUAGES) {
    const code = document.querySelector(`#snippets-code-${key} code`);
    if (code) code.textContent = snippets[key];
  }
}

/* Metadata: version, coverage, validation and download sizes. */

async function loadMeta(c: DatasetClientConfig) {
  const facts = document.querySelector("[data-meta]");
  const slot = document.querySelector("[data-meta-notice]");
  if (!facts) return;
  setText(facts, "[data-live]", "Loading…");
  try {
    const meta = await getJson<DatasetMeta>(api(paths.dataset(c.id)));
    setText(facts, '[data-live="coverage"]', formatCoverage(meta.coverage.start, meta.coverage.end) || "—");
    setText(facts, '[data-live="version"]', meta.version);
    setText(
      facts,
      '[data-live="published"]',
      meta.published_at ? `${formatPeriod(meta.published_at)} (${relativeTime(meta.published_at, Date.now())})` : "Unknown",
    );
    const warnings = meta.validation.warnings.length;
    setText(
      facts,
      '[data-live="validation"]',
      meta.validation.status === "pass"
        ? "Passed"
        : meta.validation.status === "warn"
          ? `Passed with ${warnings} warning${warnings === 1 ? "" : "s"}`
          : "Blocked",
    );
    setText(document, '[data-live="pinned"]', `/v1/${c.id}@${meta.version}`);
    for (const d of meta.downloads) setText(document, `[data-download-size="${CSS.escape(d.name)}"]`, `· ${formatBytes(d.bytes)}`);
  } catch (err) {
    const unpublished = err instanceof ApiError && err.slug === "unknown-dataset";
    setText(facts, "[data-live]", unpublished ? "Not published yet" : "Unavailable");
    slot?.replaceChildren(
      notice(
        unpublished ? "info" : "error",
        unpublished
          ? "This dataset hasn't been published yet. Its first version appears here as soon as its pipeline runs."
          : `${describeFailure(err, host)} The description, schema and URLs on this page don't depend on it.`,
        unpublished ? undefined : () => void loadMeta(c),
      ),
    );
  }
}

function stateBox(message: string): HTMLElement {
  return el("div", { class: "chart-state", role: "status", text: message });
}

function errorBox(err: unknown, retry: () => void): HTMLElement {
  const unpublished = err instanceof ApiError && err.slug === "unknown-dataset";
  const box = el("div", { class: "chart-state" });
  box.append(notice(unpublished ? "info" : "error", describeFailure(err, host), unpublished ? undefined : retry));
  return box;
}

function option(value: string, label: string, selected = false): HTMLOptionElement {
  const o = el("option", { value, text: label });
  o.selected = selected;
  return o;
}

function readParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

function writeParams(values: Record<string, string | null>) {
  const params = readParams();
  for (const [k, v] of Object.entries(values)) {
    if (v) params.set(k, v);
    else params.delete(k);
  }
  const query = params.toString();
  history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
}

/* Series: series, location and value pickers, a line chart and a data table. */

async function initSeries(c: DatasetClientConfig) {
  const root = document.querySelector<HTMLElement>('[data-explorer="series"]');
  if (!root) return;
  const seriesSel = root.querySelector<HTMLSelectElement>("#ex-series")!;
  const locSel = root.querySelector<HTMLSelectElement>("#ex-location")!;
  const measureSel = root.querySelector<HTMLSelectElement>("#ex-measure");
  const filterField = root.querySelector<HTMLElement>("[data-filter-field]")!;
  const filter = root.querySelector<HTMLInputElement>("#ex-filter")!;
  const box = root.querySelector<HTMLElement>("[data-chart]")!;
  const title = root.querySelector<HTMLElement>("#ex-title")!;
  const summary = root.querySelector<HTMLElement>("[data-chart-summary]")!;
  const links = root.querySelector<HTMLElement>("[data-chart-links]")!;
  const details = root.querySelector<HTMLDetailsElement>("[data-table-details]")!;
  const tableSlot = root.querySelector<HTMLElement>("[data-table-slot]")!;

  box.replaceChildren(stateBox("Loading the list of places…"));
  let doc: LocationsDoc;
  try {
    doc = await getJson<LocationsDoc>(api(paths.locations(c.id)));
  } catch (err) {
    box.replaceChildren(errorBox(err, () => void initSeries(c)));
    locSel.replaceChildren(option("", "Unavailable"));
    return;
  }

  const names = new Map(doc.locations.map((l) => [l.id, l.name]));
  const nameOf = (id: string) => names.get(id) ?? c.explicit[id] ?? id;
  const available = doc.series.filter((s) => s.locations.length);
  if (!available.length) {
    box.replaceChildren(stateBox("No series have been published for this dataset yet."));
    locSel.replaceChildren(option("", "None yet"));
    return;
  }
  const seriesName = (id: string) => c.series[id] ?? available.find((s) => s.id === id)?.name ?? id;
  const params = readParams();
  const pick = (wanted: (string | null | undefined)[], ids: string[]) => wanted.find((w) => w && ids.includes(w)) ?? ids[0]!;

  seriesSel.replaceChildren(...available.map((s) => option(s.id, seriesName(s.id))));
  seriesSel.value = pick([params.get("series"), c.selection.series], available.map((s) => s.id));

  let locationIds: string[] = [];
  const fillLocations = (wanted: (string | null | undefined)[]) => {
    const entry = available.find((s) => s.id === seriesSel.value)!;
    locationIds = [...entry.locations].sort((a, b) => nameOf(a).localeCompare(nameOf(b), "en", { numeric: true }));
    const chosen = pick(wanted, locationIds);
    const query = filter.value.trim().toLowerCase();
    const shown = query
      ? locationIds.filter((id) => id === chosen || id.toLowerCase().includes(query) || nameOf(id).toLowerCase().includes(query))
      : locationIds;
    locSel.replaceChildren(...shown.map((id) => option(id, nameOf(id) === id ? id : `${nameOf(id)} (${id})`, id === chosen)));
    filterField.hidden = locationIds.length <= 30;
  };
  fillLocations([params.get("location"), c.selection.location]);
  if (measureSel) {
    const wanted = params.get("value");
    if (wanted && [...measureSel.options].some((o) => o.value === wanted)) measureSel.value = wanted;
  }

  for (const control of [seriesSel, locSel, measureSel]) if (control) control.disabled = false;

  let request = 0;
  let unmount: (() => void) | null = null;
  let tableConfig: ChartConfig | null = null;

  const renderTable = () => {
    if (details.open && tableConfig) renderDataTable(tableSlot, tableConfig);
  };
  details.addEventListener("toggle", renderTable);

  async function update(push: boolean) {
    const series = seriesSel.value;
    const location = locSel.value;
    const measure = measureSel?.value ?? c.defaultMeasure ?? "value";
    if (!series || !location) return;
    if (push) writeParams({ series, location, value: measureSel ? measure : null });
    updateSnippets(c, { series, location });
    const jsonUrl = api(paths.series(c.id, series, location, "json"));
    links.querySelector<HTMLAnchorElement>('[data-link="json"]')!.href = jsonUrl;
    links.querySelector<HTMLAnchorElement>('[data-link="csv"]')!.href = api(paths.series(c.id, series, location, "csv"));
    links.hidden = false;
    const heading = `${seriesName(series)}: ${nameOf(location)}`;
    title.textContent = heading;
    const id = ++request;
    box.setAttribute("aria-busy", "true");
    if (!unmount) box.replaceChildren(stateBox("Loading the chart…"));
    try {
      const body = await getJson<SeriesDoc>(jsonUrl);
      if (id !== request) return;
      const unit = displayUnit(body.meta.units?.[measure] ?? c.measures.find((m) => m.name === measure)?.unit);
      const points = toPoints(body.data, c.timeColumn ?? "date", measure, c.timeType ?? "date");
      const label = measureSel ? measure : seriesName(series);
      tableConfig = { title: heading, unit, timeType: c.timeType ?? "date", lines: [{ label, points, color: LINE_COLORS[0]! }] };
      unmount?.();
      unmount = mountChart(box, tableConfig);
      summary.textContent = `${describeSeries(points, c.timeType ?? "date", unit)} `;
      renderTable();
      if (!details.open) tableSlot.replaceChildren(el("p", { class: "muted small", text: "Open to see every value in a table." }));
    } catch (err) {
      if (id !== request) return;
      unmount?.();
      unmount = null;
      tableConfig = null;
      summary.textContent = "";
      box.replaceChildren(errorBox(err, () => void update(false)));
    } finally {
      if (id === request) box.removeAttribute("aria-busy");
    }
  }

  seriesSel.addEventListener("change", () => {
    fillLocations([locSel.value, c.selection.location]);
    void update(true);
  });
  locSel.addEventListener("change", () => void update(true));
  measureSel?.addEventListener("change", () => void update(true));
  let timer = 0;
  filter.addEventListener("input", () => {
    clearTimeout(timer);
    timer = window.setTimeout(() => fillLocations([locSel.value]), 120);
  });
  await update(false);
}

/* Table: a partition picker, a text filter and a paginated preview. */

const PAGE_SIZE = 25;

async function initTable(c: DatasetClientConfig) {
  const root = document.querySelector<HTMLElement>('[data-explorer="table"]');
  if (!root) return;
  const partSel = root.querySelector<HTMLSelectElement>("#ex-partition")!;
  const find = root.querySelector<HTMLInputElement>("#ex-find")!;
  const preview = root.querySelector<HTMLElement>("[data-table-preview]")!;
  const pager = root.querySelector<HTMLElement>("[data-pager]")!;
  const status = root.querySelector<HTMLElement>("[data-pager-status]")!;

  preview.replaceChildren(stateBox("Loading the list of partitions…"));
  let doc: LocationsDoc;
  try {
    doc = await getJson<LocationsDoc>(api(paths.locations(c.id)));
  } catch (err) {
    preview.replaceChildren(errorBox(err, () => void initTable(c)));
    partSel.replaceChildren(option("", "Unavailable"));
    return;
  }
  const parts = (doc.series.find((s) => s.id === "table")?.locations ?? []).slice();
  parts.sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b, "en", { numeric: true })));
  if (!parts.length) {
    preview.replaceChildren(
      el("div", { class: "chart-state" }, notice("info", "This table is too big for a preview (over 50,000 rows and no partitions). Use the downloads below.")),
    );
    partSel.replaceChildren(option("", "No preview"));
    return;
  }
  const params = readParams();
  const wanted = params.get("rows");
  const first = wanted && parts.includes(wanted) ? wanted : parts[0]!;
  partSel.replaceChildren(...parts.map((p) => option(p, p === "all" ? "All rows" : p, p === first)));
  partSel.disabled = false;

  const columns = c.schema.length ? c.schema : null;
  let rows: Record<string, unknown>[] = [];
  let filtered: Record<string, unknown>[] = [];
  let page = 0;
  let unit: Record<string, string> = {};
  let request = 0;

  const buttons = Object.fromEntries(
    [...pager.querySelectorAll<HTMLButtonElement>("[data-page]")].map((b) => [b.dataset.page!, b]),
  ) as Record<"first" | "prev" | "next" | "last", HTMLButtonElement>;

  function render() {
    const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    page = Math.min(page, pages - 1);
    const slice = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    const cols = columns ?? Object.keys(rows[0] ?? {}).map((name) => ({ name, type: "str" as const, unit: null }));
    const numeric = new Set(cols.filter((col) => col.type === "float" || col.type === "int").map((col) => col.name));
    const table = el("table");
    const caption = el("caption", {
      text: `${c.title}${partSel.value === "all" ? "" : `, ${c.partition ?? "partition"} ${partSel.value}`}: page ${page + 1} of ${pages}`,
    });
    const headRow = el("tr");
    for (const col of cols) {
      const u = displayUnit(unit[col.name] ?? col.unit);
      headRow.append(el("th", { scope: "col", class: numeric.has(col.name) ? "num" : undefined, text: u ? `${col.name} (${u})` : col.name }));
    }
    const body = el("tbody");
    for (const row of slice) {
      const tr = el("tr");
      for (const col of cols) {
        tr.append(el("td", { class: numeric.has(col.name) ? "num" : undefined, text: formatValue(row[col.name], col.type) }));
      }
      body.append(tr);
    }
    if (!slice.length) {
      body.append(el("tr", {}, el("td", { colspan: cols.length, text: rows.length ? "No rows match the filter." : "This partition has no rows." })));
    }
    table.append(caption, el("thead", {}, headRow), body);
    preview.replaceChildren(el("div", { class: "table-wrap", tabindex: 0, role: "region", "aria-label": `${c.title}: preview` }, table));

    const from = filtered.length ? page * PAGE_SIZE + 1 : 0;
    const to = Math.min(filtered.length, (page + 1) * PAGE_SIZE);
    const of = filtered.length === rows.length ? formatInt(rows.length) : `${formatInt(filtered.length)} matching (${formatInt(rows.length)} in all)`;
    status.textContent = filtered.length ? `Rows ${formatInt(from)}–${formatInt(to)} of ${of}` : rows.length ? `No rows match (${formatInt(rows.length)} in all)` : "No rows";
    buttons.first.disabled = buttons.prev.disabled = page === 0;
    buttons.next.disabled = buttons.last.disabled = page >= pages - 1;
    pager.hidden = false;
  }

  function applyFilter() {
    const q = find.value.trim().toLowerCase();
    filtered = q ? rows.filter((row) => Object.values(row).some((v) => v !== null && String(v).toLowerCase().includes(q))) : rows;
    page = 0;
    render();
  }

  async function load(push: boolean) {
    const partition = partSel.value;
    if (!partition) return;
    if (push) writeParams({ rows: partition === parts[0] ? null : partition });
    updateSnippets(c, { partition });
    const id = ++request;
    preview.setAttribute("aria-busy", "true");
    try {
      const body = await getJson<TableDoc>(api(paths.table(c.id, partition, "json")));
      if (id !== request) return;
      rows = body.rows;
      unit = body.meta.units ?? {};
      find.disabled = false;
      applyFilter();
    } catch (err) {
      if (id !== request) return;
      pager.hidden = true;
      preview.replaceChildren(errorBox(err, () => void load(false)));
    } finally {
      if (id === request) preview.removeAttribute("aria-busy");
    }
  }

  const pageCount = () => Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  buttons.first.addEventListener("click", () => ((page = 0), render()));
  buttons.prev.addEventListener("click", () => ((page = Math.max(0, page - 1)), render()));
  buttons.next.addEventListener("click", () => ((page = Math.min(pageCount() - 1, page + 1)), render()));
  buttons.last.addEventListener("click", () => ((page = pageCount() - 1), render()));
  partSel.addEventListener("change", () => void load(true));
  let timer = 0;
  find.addEventListener("input", () => {
    clearTimeout(timer);
    timer = window.setTimeout(applyFilter, 150);
  });
  await load(false);
}

/* Files: the list of files in the current version. */

interface FileRow {
  path: string;
  bytes: number | null;
  url: string;
}

async function initFiles(c: DatasetClientConfig) {
  const list = document.querySelector<HTMLElement>("[data-file-list]");
  if (!list) return;
  list.replaceChildren(el("li", { text: "Loading the file list…" }));
  let meta: DatasetMeta;
  try {
    meta = await getJson<DatasetMeta>(api(paths.dataset(c.id)));
  } catch (err) {
    list.replaceChildren(el("li", {}, errorBox(err, () => void initFiles(c))));
    return;
  }
  const base = pageApiBase();
  const site = pageSiteId();
  const rows = new Map<string, FileRow>();
  const add = (path: string, bytes: number | null, url?: string) => {
    if (!rows.has(path)) rows.set(path, { path, bytes, url: url ? resolveApiLink(base, site, url) : api(paths.file(c.id, path)) });
  };
  if (Array.isArray(meta.files)) for (const f of meta.files) add(f.path, f.bytes ?? null, f.url);
  else if (meta.files) for (const [path, f] of Object.entries(meta.files)) add(path, f.bytes ?? null);
  for (const d of meta.downloads) add(`download/${d.name}`, d.bytes, d.url);
  try {
    const doc = await getJson<LocationsDoc>(api(paths.locations(c.id)));
    for (const s of doc.series) for (const loc of s.locations) add(`${s.id}/${loc}.json`, null);
  } catch {
    // The list of JSON files is a bonus; the downloads above are enough.
  }
  const sorted = [...rows.values()].sort((a, b) => a.path.localeCompare(b.path));
  if (!sorted.length) {
    list.replaceChildren(el("li", { text: "The current version lists no downloadable files." }));
    return;
  }
  list.replaceChildren(
    ...sorted.map((f) =>
      el("li", {}, el("a", { href: f.url, text: f.path }), el("span", { class: "muted small", text: f.bytes === null ? "" : formatBytes(f.bytes) })),
    ),
  );
  updateSnippets(c, { file: sorted[0]!.path });
}
