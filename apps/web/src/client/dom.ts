// Tiny DOM helpers for the page scripts.

type Child = Node | string | null | undefined | false;

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<Record<string, string | number | boolean>> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === false) continue;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}

/** An error or info box, with an optional retry button. */
export function notice(kind: "error" | "info" | "warn", message: string, retry?: () => void): HTMLElement {
  const box = el("div", { class: `notice${kind === "info" ? "" : ` notice-${kind}`}`, role: kind === "error" ? "alert" : "status" });
  box.append(el("p", { text: message }));
  if (retry) {
    const button = el("button", { type: "button", class: "btn btn-quiet btn-small", text: "Try again" });
    button.addEventListener("click", retry);
    box.append(el("p", {}, button));
  }
  return box;
}

/** Reads a JSON config the page rendered into <script type="application/json" id="...">. */
export function readConfig<T>(id: string): T | null {
  const node = document.getElementById(id);
  if (!node?.textContent) return null;
  try {
    return JSON.parse(node.textContent) as T;
  } catch {
    return null;
  }
}

export function setText(root: ParentNode, selector: string, text: string): void {
  for (const node of root.querySelectorAll<HTMLElement>(selector)) node.textContent = text;
}
