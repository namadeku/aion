import { api } from "../api";
import { field, h, range, select, text, toast, toggle } from "../dom";
import { store } from "../store";
import type { PluginInfo, SettingSpec } from "../types";
import { failure, pageShell, panel } from "./common";
import { mcpPanel } from "./mcp";

const PERMISSION_LABEL: Record<string, string> = {
  network: "сеть",
  system: "система",
  shell: "команды",
  files: "файлы",
  clipboard: "буфер обмена",
  notifications: "уведомления",
};

export function pluginsPage(root: HTMLElement): () => void {
  const body = pageShell(
    root,
    "Плагины",
    "Навыки ассистента. Свои плагины кладите в папку плагинов — они подхватываются на лету.",
  );
  const list = h("div");
  const source = text("", () => undefined, {
    placeholder: "Путь к папке, .zip или git-URL",
  });
  const install = async (): Promise<void> => {
    if (!source.value.trim()) return;
    if (
      !confirm(
        "Плагины выполняются с вашими правами. Устанавливайте только из доверенных источников. Продолжить?",
      )
    )
      return;
    try {
      const { name } = await api.post<{ name: string }>("/api/plugins/install", {
        source: source.value.trim(),
      });
      toast("Установлен", name, "success");
      source.value = "";
      await reload();
    } catch (err) {
      toast("Не установлен", err instanceof Error ? err.message : String(err), "error");
    }
  };
  body.append(
    panel(
      "Установить",
      h(
        "div",
        { class: "row" },
        h("div", { style: "flex:1;min-width:240px" }, source),
        h("button", { class: "btn", onclick: install }, "Установить"),
      ),
      h(
        "p",
        { class: "muted", style: "margin:10px 0 0;font-size:13px" },
        "Новый плагин из шаблона: ",
        h("code", { class: "mono" }, "aion plugin new my_plugin"),
        ". Документация — docs/PLUGINS.md.",
      ),
    ),
    mcpPanel(),
    list,
  );

  async function reload(): Promise<void> {
    try {
      const plugins = await api.get<PluginInfo[]>("/api/plugins");
      list.replaceChildren(...plugins.map(card));
    } catch (err) {
      failure(list, err);
    }
  }

  async function act(name: string, action: string): Promise<void> {
    try {
      await api.post(`/api/plugins/${name}/${action}`);
      await reload();
    } catch (err) {
      toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
    }
  }

  function card(p: PluginInfo): HTMLElement {
    const enabled = p.status !== "disabled";
    const status =
      p.status === "error"
        ? h("span", { class: "tag err" }, "ошибка")
        : p.status === "loaded"
          ? h("span", { class: "tag ok" }, "работает")
          : h("span", { class: "tag" }, "выключен");
    const settingsForm = Object.keys(p.settings_schema).length ? settings(p) : null;
    return h(
      "section",
      { class: "panel plugin" },
      h(
        "div",
        null,
        h(
          "div",
          { class: "row" },
          h("h3", null, p.title),
          status,
          p.builtin ? h("span", { class: "tag" }, "встроенный") : null,
        ),
        h(
          "div",
          { class: "muted mono", style: "font-size:12px" },
          `${p.name} · v${p.version}${p.author ? " · " + p.author : ""}`,
        ),
      ),
      h(
        "div",
        { class: "row" },
        toggle(enabled, (v) => void act(p.name, v ? "enable" : "disable")),
        h(
          "button",
          { class: "btn ghost", onclick: () => void act(p.name, "reload") },
          "Перезагрузить",
        ),
        p.builtin
          ? null
          : h(
              "button",
              {
                class: "btn danger",
                onclick: async () => {
                  if (!confirm(`Удалить плагин ${p.title}?`)) return;
                  try {
                    await api.del(`/api/plugins/${p.name}`);
                    await reload();
                  } catch (err) {
                    toast("Ошибка", String(err), "error");
                  }
                },
              },
              "Удалить",
            ),
      ),
      h("p", { class: "desc", style: "margin:4px 0 0" }, p.description),
      p.error
        ? h("div", { class: "banner", style: "grid-column:1/-1;margin:6px 0 0" }, p.error)
        : null,
      p.permissions.length
        ? h(
            "div",
            { class: "row", style: "grid-column:1/-1" },
            ...p.permissions.map((perm) =>
              h(
                "span",
                { class: `tag${p.dangerous_permissions.includes(perm) ? " warn" : ""}` },
                PERMISSION_LABEL[perm] ?? perm,
              ),
            ),
          )
        : null,
      p.commands.length || p.tools.length
        ? h(
            "details",
            null,
            h(
              "summary",
              null,
              `Команды (${p.commands.length}) и инструменты LLM (${p.tools.length})`,
            ),
            h(
              "ul",
              { class: "patterns" },
              ...p.commands.flatMap((c) =>
                c.patterns.map((pat) => h("li", null, pat, c.dangerous ? " ⚠" : "")),
              ),
              ...p.tools.map((t) => h("li", null, `🛠 ${t.name} — ${t.description}`)),
            ),
          )
        : null,
      settingsForm ? h("details", null, h("summary", null, "Настройки"), settingsForm) : null,
    );
  }

  function settings(p: PluginInfo): HTMLElement {
    const values = { ...(p.settings ?? {}) };
    const save = async (key: string, value: unknown): Promise<void> => {
      try {
        await api.put(`/api/plugins/${p.name}/settings`, { [key]: value });
        values[key] = value;
        toast("Сохранено", p.title, "success");
      } catch (err) {
        toast("Не сохранено", err instanceof Error ? err.message : String(err), "error");
      }
    };
    return h(
      "div",
      { class: "grid", style: "margin-top:12px" },
      ...Object.entries(p.settings_schema).map(([key, spec]) =>
        field(
          spec.title || key,
          control(spec, values[key], (v) => void save(key, v)),
          spec.description,
          spec.type === "text",
        ),
      ),
    );
  }

  void reload();
  const unsubscribe = store.on("plugin_status_changed", () => void reload());
  return unsubscribe;
}

function control(spec: SettingSpec, value: unknown, onChange: (v: unknown) => void): HTMLElement {
  switch (spec.type) {
    case "bool":
      return toggle(Boolean(value), onChange);
    case "enum":
      return select(spec.options ?? [], String(value ?? ""), onChange);
    case "int":
    case "float": {
      const step = spec.type === "int" ? 1 : 0.1;
      if (spec.min !== null && spec.max !== null) {
        return range(Number(value ?? spec.min), spec.min, spec.max, step, onChange, (v) =>
          spec.type === "int" ? String(Math.round(v)) : v.toFixed(1),
        );
      }
      const el = h("input", {
        type: "number",
        step,
        value: String(value ?? ""),
        onchange: () => onChange(Number(el.value)),
      });
      return el;
    }
    case "secret": {
      const el = h("input", {
        type: "password",
        placeholder: value ? "•••••• (задан)" : "не задан",
        autocomplete: "off",
        onchange: () => onChange(el.value),
      });
      return el;
    }
    case "text": {
      const el = h("textarea", { onchange: () => onChange(el.value) }, String(value ?? ""));
      return el;
    }
    default:
      return text(String(value ?? ""), onChange);
  }
}
