// Console: a live stream of the assistant's bus events (what it heard, which command or tool
// ran, state changes). A first version; the full console with commands comes later.

import { h } from "../dom";
import { store } from "../store";
import type { BusEvent } from "../types";
import { pageShell } from "./common";

/** High-frequency or shown elsewhere: not listed. */
const SKIP = new Set(["audio_level", "log_record", "snapshot"]);
const LIMIT = 500;

function describe(e: BusEvent): string {
  const rest = Object.entries(e).filter(([k]) => k !== "type" && k !== "ts");
  return rest.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join("  ");
}

export function consolePage(root: HTMLElement): () => void {
  const body = pageShell(
    root,
    "Консоль",
    "События ассистента в реальном времени: распознанные фразы, команды, вызовы инструментов, смена состояния.",
  );
  const box = h("div", { class: "log console-log", role: "log", "aria-label": "События" });
  const filter = h("input", {
    type: "search",
    placeholder: "Фильтр, например tool_called",
    "aria-label": "Фильтр событий",
  });
  const typeFilter = h("select", { "aria-label": "Тип события", class: "type-filter" });
  const seen = new Map<string, number>();
  let paused = false;
  const pause = h(
    "button",
    { type: "button", class: "btn ghost", "aria-pressed": "false" },
    "Пауза",
  );
  const items: BusEvent[] = [];

  const line = (e: BusEvent): HTMLElement =>
    h(
      "div",
      { class: "console-line" },
      h(
        "span",
        { class: "console-time" },
        new Date(((e.ts as number) || Date.now() / 1000) * 1000).toLocaleTimeString("ru-RU"),
      ),
      h("span", { class: "console-type" }, e.type),
      h("span", { class: "console-data" }, describe(e)),
    );
  const fillTypes = (): void => {
    const current = typeFilter.value;
    typeFilter.replaceChildren(
      h("option", { value: "" }, "Все типы"),
      ...[...seen]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([type, n]) =>
          h("option", { value: type, selected: type === current }, `${type} (${n})`),
        ),
    );
  };
  const matches = (e: BusEvent): boolean => {
    if (typeFilter.value && e.type !== typeFilter.value) return false;
    const q = filter.value.trim().toLowerCase();
    return !q || `${e.type} ${describe(e)}`.toLowerCase().includes(q);
  };
  const empty = h(
    "div",
    { class: "console-empty" },
    "Скажите что-нибудь ассистенту — события появятся здесь.",
  );
  const renderAll = (): void => {
    const shown = items.filter(matches);
    box.replaceChildren(...(shown.length ? shown.map(line) : [empty]));
    box.scrollTop = box.scrollHeight;
  };

  filter.addEventListener("input", renderAll);
  typeFilter.addEventListener("change", renderAll);
  fillTypes();
  pause.addEventListener("click", () => {
    paused = !paused;
    pause.setAttribute("aria-pressed", String(paused));
    pause.textContent = paused ? "Продолжить" : "Пауза";
    if (!paused) renderAll();
  });
  body.append(
    h(
      "div",
      { class: "row toolbar" },
      h("div", { class: "grow" }, filter),
      typeFilter,
      pause,
      h(
        "button",
        {
          type: "button",
          class: "btn ghost",
          onclick: () => {
            items.length = 0;
            seen.clear();
            fillTypes();
            renderAll();
          },
        },
        "Очистить",
      ),
    ),
    box,
  );
  renderAll();

  return store.on("*", (e) => {
    if (SKIP.has(e.type)) return;
    items.push(e);
    if (items.length > LIMIT) items.shift();
    // the type list is rebuilt only when a new type appears or counts must be refreshed
    const first = !seen.has(e.type);
    seen.set(e.type, (seen.get(e.type) ?? 0) + 1);
    if (first || seen.get(e.type)! % 10 === 0) fillTypes();
    if (paused || !matches(e)) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    empty.remove();
    box.append(line(e));
    if (box.childElementCount > LIMIT) box.firstElementChild?.remove();
    if (atBottom) box.scrollTop = box.scrollHeight;
  });
}
