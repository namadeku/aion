import { field, h, toggle } from "../dom";
import { store } from "../store";
import type { Config } from "../types";
import { failure, loadConfig, pageShell, panel, patchConfig } from "./common";
import { updatePanel } from "./maintenance";

export function aboutPage(root: HTMLElement): () => void {
  const body = pageShell(root, "О программе", "Версия Aion и обновления.");
  const [updates, unsubscribe] = updatePanel();

  const info = panel(
    "Aion",
    h(
      "div",
      { class: "grid" },
      field("Версия", h("div", { class: "mono" }, store.version || "—")),
      field("Редакция", h("div", null, store.edition === "dev" ? "для разработки" : "публичная")),
    ),
  );
  body.append(info, updates);

  if (store.edition === "public") {
    loadConfig().then(
      (cfg: Config) =>
        updates.append(
          h(
            "div",
            { class: "grid", style: "margin-top:16px" },
            field(
              "Проверять при запуске",
              toggle(
                cfg.updates.auto_check,
                (v) => void patchConfig({ updates: { auto_check: v } }),
              ),
              "Раз в несколько часов Aion спрашивает GitHub, вышла ли новая версия",
            ),
          ),
        ),
      (err) => failure(body, err),
    );
  }
  return unsubscribe;
}
