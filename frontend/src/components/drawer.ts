// Side drawer (a modal dialog): Esc and the backdrop close it, Tab stays inside, the focus
// goes back to the element that opened it.

import { h, iconButton, icons } from "../dom";

export interface Drawer {
  /** Put the content here. */
  body: HTMLElement;
  close(): void;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function openDrawer(options: { title: string; subtitle?: string }): Drawer {
  const opener = document.activeElement as HTMLElement | null;
  const titleId = `drawer-${Math.random().toString(36).slice(2)}`;
  const body = h("div", { class: "drawer-body" });
  const panel = h(
    "aside",
    { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h(
      "header",
      { class: "drawer-head" },
      h(
        "div",
        { class: "drawer-titles" },
        h("h2", { id: titleId }, options.title),
        options.subtitle ? h("p", { class: "muted mono" }, options.subtitle) : null,
      ),
      iconButton("Закрыть", icons.close, () => close()),
    ),
    body,
  );
  const backdrop = h("div", { class: "drawer-backdrop" }, panel);
  backdrop.addEventListener("mousedown", (e) => {
    if (e.target === backdrop) close();
  });

  function focusable(): HTMLElement[] {
    return [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.hidden);
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const items = focusable();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !panel.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  let closed = false;
  function close(): void {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    if (opener?.isConnected) opener.focus();
  }

  document.addEventListener("keydown", onKey, true);
  document.body.append(backdrop);
  // the first control after the close button
  (focusable()[1] ?? focusable()[0])?.focus();
  return { body, close };
}
