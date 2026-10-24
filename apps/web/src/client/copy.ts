// "Copy" buttons on code blocks and attribution text. The buttons stay hidden without JavaScript.

let live: HTMLElement | null = null;

function announce(message: string) {
  if (!live) {
    live = document.createElement("div");
    live.className = "visually-hidden";
    live.setAttribute("role", "status");
    document.body.append(live);
  }
  live.textContent = "";
  // A fresh text node after a tick makes screen readers announce repeated messages.
  setTimeout(() => {
    if (live) live.textContent = message;
  }, 50);
}

/** The Clipboard API needs a secure context, which the sites always are (HTTPS, or localhost in development). */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function enhanceCopyButtons(root: ParentNode = document): void {
  if (!navigator.clipboard) return;
  for (const button of root.querySelectorAll<HTMLButtonElement>("button[data-copy]")) {
    if (button.dataset.ready) continue;
    button.dataset.ready = "1";
    button.hidden = false;
    const label = button.textContent ?? "Copy";
    button.addEventListener("click", async () => {
      const targetId = button.dataset.copy;
      const target = targetId ? document.getElementById(targetId) : button.parentElement?.querySelector("pre, blockquote");
      const text = target?.textContent?.trim() ?? "";
      const ok = text ? await copyText(text) : false;
      button.textContent = ok ? "Copied" : "Copy failed";
      announce(ok ? "Copied to the clipboard" : "Couldn't copy; select the text and copy it instead");
      setTimeout(() => (button.textContent = label), 1800);
    });
  }
}

enhanceCopyButtons();
