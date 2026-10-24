// ARIA tabs (https://www.w3.org/WAI/ARIA/apg/patterns/tabs/). Without JavaScript every panel shows, each with a heading.

export function enhanceTabs(root: ParentNode = document): void {
  for (const container of root.querySelectorAll<HTMLElement>("[data-tabs]")) {
    if (container.classList.contains("is-enhanced")) continue;
    const tabs = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls") ?? ""));
    const select = (index: number, focus: boolean) => {
      tabs.forEach((tab, i) => {
        const on = i === index;
        tab.setAttribute("aria-selected", String(on));
        tab.tabIndex = on ? 0 : -1;
        const panel = panels[i];
        if (panel) panel.hidden = !on;
      });
      if (focus) tabs[index]?.focus();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(i, false));
      tab.addEventListener("keydown", (event) => {
        const last = tabs.length - 1;
        const next =
          event.key === "ArrowRight" ? (i === last ? 0 : i + 1)
          : event.key === "ArrowLeft" ? (i === 0 ? last : i - 1)
          : event.key === "Home" ? 0
          : event.key === "End" ? last
          : -1;
        if (next < 0) return;
        event.preventDefault();
        select(next, true);
      });
    });
    container.classList.add("is-enhanced");
    select(Math.max(0, tabs.findIndex((t) => t.getAttribute("aria-selected") === "true")), false);
  }
}

enhanceTabs();
