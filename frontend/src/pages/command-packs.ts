// «Паки и режим»: ready-made command packs (enable + phrase language) and the voice settings of
// commands: prefix mode (only after the assistant's name) and the confirmation timeout.

import { api } from "../api";
import { field, h, toast, toggle } from "../dom";
import type { CommandLang, CommandPack, Config } from "../types";
import { ANTI_CHEAT_WARNING } from "./command-actions";
import { failure, loadConfig, pageShell, panel, patchConfig } from "./common";

const LANGS: [CommandLang, string][] = [
  ["ru", "Русский"],
  ["en", "English"],
  ["both", "Русский и English"],
];

/** Packs that press keys or move the mouse (games with an anti-cheat ignore those). */
const EMULATING = new Set(["games"]);

export function commandPacksPage(root: HTMLElement): void {
  const body = pageShell(
    root,
    "Паки и режим",
    "Готовые наборы команд и то, как команды запускаются голосом.",
  );
  const modeBox = h("div");
  const packsBox = h("div");
  body.append(modeBox, packsBox);

  async function loadPacks(): Promise<void> {
    try {
      const packs = await api.get<CommandPack[]>("/api/commands/packs");
      packsBox.replaceChildren(
        panel(
          "Готовые паки",
          h(
            "p",
            { class: "muted hint" },
            "Пак копируется в вашу папку команд при первом включении: после этого его команды можно менять.",
          ),
          packs.length
            ? h("div", { class: "pack-grid" }, ...packs.map(card))
            : h("p", { class: "muted" }, "Встроенных паков нет."),
        ),
      );
    } catch (err) {
      failure(packsBox, err);
    }
  }

  async function setPack(p: CommandPack, enabled: boolean, lang: CommandLang): Promise<void> {
    try {
      await api.post(`/api/commands/packs/${encodeURIComponent(p.name)}`, { enabled, lang });
      toast(enabled ? "Пак включён" : "Пак выключен", p.title, "success");
    } catch (err) {
      toast("Не получилось", err instanceof Error ? err.message : String(err), "error");
    }
    await loadPacks();
  }

  function card(p: CommandPack): HTMLElement {
    const sw = toggle(p.enabled, (v) => void setPack(p, v, p.selected_lang));
    sw.querySelector("input")?.setAttribute("aria-label", `Пак «${p.title}» включён`);
    const langId = `pack-lang-${p.name}`;
    const lang = h("select", { id: langId });
    for (const [v, label] of LANGS) {
      lang.append(h("option", { value: v, selected: v === p.selected_lang }, label));
    }
    lang.addEventListener("change", () => void setPack(p, p.enabled, lang.value as CommandLang));
    return h(
      "article",
      { class: `pack-card${p.enabled ? " on" : ""}` },
      h(
        "div",
        { class: "pack-head" },
        h("h3", null, p.title),
        h("span", { class: "tag" }, p.installed ? `команд: ${p.commands}` : "не установлен"),
        h("span", { class: "spacer" }),
        sw,
      ),
      h("p", { class: "muted" }, p.description),
      EMULATING.has(p.name) && !p.description.includes("античит")
        ? h("p", { class: "warn-text anti-cheat" }, ANTI_CHEAT_WARNING)
        : null,
      h(
        "div",
        { class: "field" },
        h("label", { for: langId, class: "field-label" }, "Язык фраз"),
        lang,
        p.lang !== "both"
          ? h("small", null, `Пак написан для языка: ${p.lang === "ru" ? "русский" : "English"}`)
          : null,
      ),
    );
  }

  function renderMode(cfg: Config): void {
    const save = async (patch: Record<string, unknown>): Promise<void> => {
      const next = await patchConfig({ commands: patch });
      if (next) renderMode(next);
    };
    const timeout = h("input", {
      id: "cmd-timeout",
      type: "number",
      min: 3,
      max: 120,
      step: 1,
      value: String(cfg.commands.confirm_timeout),
      onchange: () => {
        const v = Math.min(120, Math.max(3, Number(timeout.value) || 15));
        void save({ confirm_timeout: v });
      },
    });
    const prefix = toggle(
      cfg.commands.require_wake_name,
      (v) => void save({ require_wake_name: v }),
    );
    prefix.querySelector("input")?.setAttribute("aria-label", "Режим префикса");
    modeBox.replaceChildren(
      panel(
        "Как запускаются команды",
        h(
          "div",
          { class: "grid" },
          field(
            "Режим префикса",
            prefix,
            "Включено: голосом команды выполняются только после имени ассистента («Аион, протокол гостей»). Из текста в окне они работают всегда.",
            true,
          ),
          field(
            "Ждать ответа на вопрос, секунд",
            timeout,
            "Опасное действие без ответа за это время отменяется (от 3 до 120).",
          ),
        ),
      ),
    );
  }

  void loadConfig().then(renderMode, (err) => failure(modeBox, err));
  void loadPacks();
}
