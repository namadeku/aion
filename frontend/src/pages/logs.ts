import { api } from "../api";
import { h, select } from "../dom";
import { store } from "../store";
import type { LogItem } from "../types";
import { pageShell } from "./common";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];

export function logsPage(root: HTMLElement): () => void {
  const body = pageShell(
    root,
    "Логи",
    "Что происходит внутри ассистента. Полный журнал — в папке данных, logs/aion.log.",
  );
  let minLevel = "INFO";
  let items: LogItem[] = [];
  const box = h("div", { class: "log", role: "log" });

  const visible = (i: LogItem): boolean =>
    LEVELS.indexOf(i.level) >= LEVELS.indexOf(minLevel) || !LEVELS.includes(i.level);
  const line = (i: LogItem): HTMLElement =>
    h(
      "div",
      { class: i.level },
      `${new Date(i.ts * 1000).toLocaleTimeString("ru-RU")} ${i.level.padEnd(7)} ${i.module}  ${i.message}`,
    );
  const renderAll = (): void => {
    box.replaceChildren(...items.filter(visible).map(line));
    box.scrollTop = box.scrollHeight;
  };

  body.append(
    h(
      "div",
      { class: "row", style: "margin-bottom:12px" },
      select(LEVELS, minLevel, (v) => {
        minLevel = v;
        renderAll();
      }),
      h("button", { class: "btn ghost", onclick: () => ((items = []), renderAll()) }, "Очистить"),
    ),
    box,
  );

  void api.get<LogItem[]>("/api/logs").then((data) => {
    items = data;
    renderAll();
  });

  return store.on("log_record", (e) => {
    const item: LogItem = {
      ts: e.ts as number,
      level: e.level as string,
      module: e.module as string,
      message: e.message as string,
    };
    items.push(item);
    if (items.length > 1000) items.shift();
    if (!visible(item)) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.append(line(item));
    if (atBottom) box.scrollTop = box.scrollHeight;
  });
}
