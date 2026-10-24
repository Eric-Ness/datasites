// Renders /v1/status: an overall line and one row per dataset, including registry datasets not published yet.

import { durationLabel, formatPeriod, relativeTime } from "../lib/format";
import { datasetPath } from "../lib/routes";
import { STATUS_INFO, statusLabel } from "../lib/status";
import { paths } from "../lib/urls";
import { api, describeFailure, getJson, hostOf, pageApiBase } from "./api";
import { el, notice, readConfig } from "./dom";

interface DatasetStatus {
  id: string;
  status: string;
  version: string | null;
  checked_at: string | null;
  source_updated_at: string | null;
  sla: string;
  consecutive_failures: number;
  last_error: string | null;
}

interface SiteStatus {
  generated_at: string;
  overall: "ok" | "degraded";
  datasets: DatasetStatus[];
}

const root = document.querySelector<HTMLElement>("[data-status-root]");
const config = readConfig<{ datasets: { id: string; title: string }[] }>("status-config") ?? { datasets: [] };
let attempt = 0;

function when(iso: string | null): Node {
  if (!iso) return document.createTextNode("—");
  return el("time", { datetime: iso, title: formatPeriod(iso) }, relativeTime(iso, Date.now()));
}

function overallText(status: SiteStatus): string {
  const published = status.datasets.filter((d) => d.status !== "unpublished").length;
  if (!published) return "No datasets have been published on this site yet.";
  if (status.overall === "ok") return published === 1 ? "The published dataset is current." : `All ${published} published datasets are current.`;
  const attention = status.datasets.filter((d) => !["ok", "discontinued", "unpublished"].includes(d.status)).length;
  return `${attention} of ${published} published datasets need attention.`;
}

async function load() {
  if (!root) return;
  const overall = root.querySelector<HTMLElement>("[data-status-overall]")!;
  const slot = root.querySelector<HTMLElement>("[data-status-table]")!;
  const refresh = root.querySelector<HTMLButtonElement>("[data-status-refresh]")!;
  const url = api(paths.status());
  refresh.disabled = true;
  try {
    // A refresh asks the API again instead of reusing this page's earlier answer.
    const status = await getJson<SiteStatus>(attempt++ ? `${url}${url.includes("?") ? "&" : "?"}refresh=${Date.now()}` : url);
    const titles = new Map(config.datasets.map((d) => [d.id, d.title]));
    const rows: DatasetStatus[] = [...status.datasets];
    for (const d of config.datasets) {
      if (!rows.some((r) => r.id === d.id)) {
        rows.push({
          id: d.id,
          status: "unpublished",
          version: null,
          checked_at: null,
          source_updated_at: null,
          sla: "",
          consecutive_failures: 0,
          last_error: null,
        });
      }
    }
    overall.className = `notice${status.overall === "ok" ? "" : " notice-warn"}`;
    overall.replaceChildren(
      el("p", {}, el("strong", { text: overallText(status) }), " ", el("span", { class: "muted small" }, "As of ", when(status.generated_at), ".")),
    );

    const headers = ["Dataset", "Status", "Version", "Last checked", "Source last updated", "Notes"];
    const body = el("tbody");
    for (const d of rows) {
      const title = titles.get(d.id);
      const notes: string[] = [];
      if (d.consecutive_failures) notes.push(`${d.consecutive_failures} failed run${d.consecutive_failures === 1 ? "" : "s"} in a row`);
      if (d.last_error) notes.push(d.last_error);
      if (d.sla) notes.push(`Expected to change at least every ${durationLabel(d.sla)}`);
      body.append(
        el(
          "tr",
          {},
          el("th", { scope: "row" }, title ? el("a", { href: datasetPath(d.id), text: title }) : el("code", { text: d.id })),
          el(
            "td",
            {},
            el("span", { class: `badge status-${d.status}`, title: STATUS_INFO[d.status]?.meaning ?? "", text: statusLabel(d.status) }),
          ),
          el("td", { class: "code-cell nowrap", text: d.version ?? "—" }),
          el("td", {}, when(d.checked_at)),
          el("td", {}, when(d.source_updated_at)),
          el("td", { class: "small", text: notes.join(". ") || "—" }),
        ),
      );
    }
    if (!rows.length) body.append(el("tr", {}, el("td", { colspan: headers.length, text: "Nothing to show yet." })));
    const table = el(
      "table",
      {},
      el("caption", { text: "Freshness of each dataset" }),
      el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { scope: "col", text: h })))),
      body,
    );
    slot.replaceChildren(el("div", { class: "table-wrap", tabindex: 0, role: "region", "aria-label": "Dataset status" }, table));
  } catch (err) {
    overall.className = "notice notice-error";
    overall.replaceChildren(
      el("p", {}, el("strong", { text: "The status API can't be reached right now." }), " ", describeFailure(err, hostOf(pageApiBase()))),
    );
    slot.replaceChildren(notice("info", "Use Refresh to try again in a minute. The status legend below still explains each state."));
  } finally {
    refresh.hidden = false;
    refresh.disabled = false;
  }
}

root?.querySelector("[data-status-refresh]")?.addEventListener("click", () => void load());
void load();
