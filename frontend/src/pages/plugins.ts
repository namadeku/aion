// «Плагины»: a showcase of cards (icon, description, permissions with the dangerous ones
// highlighted, enable switch) and a settings drawer built from the plugin's settings schema.
// Install from a folder / .zip / git URL, reload and remove keep working as before.

import { api } from "../api";
import { openDrawer, type Drawer } from "../components/drawer";
import { field, h, icons, range, select, svgIcon, text, toast, toggle } from "../dom";
import { store } from "../store";
import type { PluginInfo, SettingSpec } from "../types";
import { failure, pageShell } from "./common";

const PERMISSION_LABEL: Record<string, string> = {
  network: "сеть",
  system: "система",
  shell: "команды",
  files: "файлы",
  clipboard: "буфер обмена",
  notifications: "уведомления",
};

const PERMISSION_HINT: Record<string, string> = {
  network: "Ходит в интернет",
  system: "Управляет системой: окна, громкость, питание",
  shell: "Запускает команды и программы",
  files: "Читает и пишет файлы",
  clipboard: "Читает и пишет буфер обмена",
  notifications: "Показывает уведомления",
};

/** A glyph per built-in plugin; others get their initial letter. */
const PLUGIN_ICON: Record<string, string> = {
  weather: icons.cloud,
  timers: icons.timer,
  clock: icons.clock,
  calc: icons.calc,
  notes: icons.note,
  clipboard: icons.clipboard,
  cs2: icons.gamepad,
  dota2: icons.gamepad,
  valorant: icons.gamepad,
  telegram: icons.plane,
  homeassistant: icons.home,
  screen: icons.desktop,
  system: icons.power,
  apps: icons.plugins,
  assistant: icons.brain,
  memory: icons.database,
  companion: icons.heart,
};

type Filter = "all" | "on" | "off" | "mine";

const FILTERS: [Filter, string][] = [
  ["all", "Все"],
  ["on", "Включённые"],
  ["off", "Выключенные"],
  ["mine", "Мои"],
];

const norm = (t: string): string => t.toLowerCase().replaceAll("ё", "е");

/** Steps to connect the Telegram bot (also shown in the plugin's drawer). */
function telegramHelp(open: boolean): HTMLElement {
  return h(
    "details",
    { class: "help-box", open },
    h("summary", null, "Как подключить бота"),
    h(
      "ol",
      null,
      h(
        "li",
        null,
        "В Telegram откройте @BotFather, отправьте ",
        h("code", null, "/newbot"),
        " и скопируйте токен.",
      ),
      h(
        "li",
        null,
        "Впишите токен в файл ",
        h("code", null, "%LOCALAPPDATA%\\Aion\\.env"),
        " строкой ",
        h("code", null, "AION_TELEGRAM_TOKEN=ваш_токен"),
        " и перезапустите Aion.",
      ),
      h("li", null, "Включите плагин и переключатель «Включить бота» в настройках."),
      h(
        "li",
        null,
        "Напишите своему боту ",
        h("code", null, "/start"),
        ": он ответит вашим chat id.",
      ),
      h(
        "li",
        null,
        "Вставьте этот chat id в поле «Разрешённые chat id». Пока список пуст, бот никому не отвечает.",
      ),
    ),
  );
}

export function pluginsPage(root: HTMLElement): () => void {
  const body = pageShell(
    root,
    "Плагины",
    "Навыки ассистента. Свои плагины кладите в папку плагинов — они подхватываются на лету.",
  );
  let plugins: PluginInfo[] = [];
  let query = "";
  let filter: Filter = "all";
  let drawer: Drawer | null = null;
  const grid = h("div", { class: "plugin-grid" });

  // -- install ---------------------------------------------------------------------------------
  const source = text("", () => undefined, {
    placeholder: "Папка, .zip или адрес git: C:\\plugins\\my_plugin, my.zip, https://github.com/…",
    "aria-label": "Источник плагина",
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
  source.addEventListener("keydown", (e) => {
    if (e.key === "Enter") void install();
  });

  const search = h("input", {
    type: "search",
    placeholder: "Найти плагин",
    "aria-label": "Поиск плагинов",
  });
  search.addEventListener("input", () => {
    query = norm(search.value.trim());
    renderGrid();
  });
  const chips = h(
    "div",
    { class: "chips", role: "group", "aria-label": "Показать" },
    ...FILTERS.map(([id, label]) =>
      h(
        "button",
        {
          type: "button",
          class: "chip",
          "aria-pressed": String(id === filter),
          "data-filter": id,
          onclick: () => {
            filter = id;
            for (const b of chips.querySelectorAll("button")) {
              b.setAttribute("aria-pressed", String(b.dataset.filter === filter));
            }
            renderGrid();
          },
        },
        label,
      ),
    ),
  );

  body.append(
    h(
      "div",
      { class: "row toolbar" },
      h("div", { class: "grow" }, search),
      chips,
      h("a", { class: "btn ghost", href: "#/settings/mcp" }, svgIcon(icons.link), "MCP-серверы"),
    ),
    grid,
    h(
      "details",
      { class: "panel install" },
      h("summary", null, "Установить плагин"),
      h(
        "div",
        { class: "row", style: "margin-top:12px" },
        h("div", { class: "grow" }, source),
        h("button", { type: "button", class: "btn", onclick: () => void install() }, "Установить"),
      ),
      h(
        "p",
        { class: "muted hint", style: "margin:10px 0 0" },
        "Новый плагин из шаблона: ",
        h("code", { class: "mono" }, "aion plugin new my_plugin"),
        ". Документация — docs/PLUGINS.md.",
      ),
    ),
  );

  // -- data --------------------------------------------------------------------------------------

  async function reload(): Promise<void> {
    try {
      plugins = await api.get<PluginInfo[]>("/api/plugins");
      renderGrid();
    } catch (err) {
      failure(grid, err);
    }
  }

  async function act(name: string, action: string): Promise<void> {
    try {
      await api.post(`/api/plugins/${name}/${action}`);
      if (action === "reload") toast("Перезагружен", name, "success");
      await reload();
    } catch (err) {
      toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
    }
  }

  async function remove(p: PluginInfo): Promise<void> {
    if (!confirm(`Удалить плагин ${p.title}?`)) return;
    try {
      await api.del(`/api/plugins/${p.name}`);
      drawer?.close();
      await reload();
    } catch (err) {
      toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
    }
  }

  // -- cards --------------------------------------------------------------------------------------

  function visible(p: PluginInfo): boolean {
    if (filter === "on" && p.status === "disabled") return false;
    if (filter === "off" && p.status !== "disabled") return false;
    if (filter === "mine" && p.builtin) return false;
    return !query || norm(`${p.title} ${p.name} ${p.description}`).includes(query);
  }

  function renderGrid(): void {
    const shown = plugins.filter(visible);
    grid.replaceChildren(
      ...(shown.length
        ? shown.map(card)
        : [
            h(
              "p",
              { class: "muted empty-state" },
              plugins.length ? "Ничего не найдено." : "Плагинов пока нет.",
            ),
          ]),
    );
  }

  function tile(p: PluginInfo): HTMLElement {
    const glyph = PLUGIN_ICON[p.name];
    return h(
      "span",
      { class: "plugin-tile", "aria-hidden": "true" },
      glyph ? svgIcon(glyph) : (p.title.trim()[0] ?? "?").toUpperCase(),
    );
  }

  function permissions(p: PluginInfo): HTMLElement | null {
    if (!p.permissions.length) return null;
    return h(
      "div",
      { class: "perms", role: "list", "aria-label": "Разрешения" },
      ...p.permissions.map((perm) => {
        const risky = p.dangerous_permissions.includes(perm);
        return h(
          "span",
          {
            class: `tag${risky ? " warn" : ""}`,
            role: "listitem",
            title: `${PERMISSION_HINT[perm] ?? perm}${risky ? " (опасное разрешение)" : ""}`,
          },
          risky ? svgIcon(icons.warning) : null,
          PERMISSION_LABEL[perm] ?? perm,
        );
      }),
    );
  }

  function statusTag(p: PluginInfo): HTMLElement {
    return p.status === "error"
      ? h("span", { class: "tag err" }, "ошибка")
      : p.status === "loaded"
        ? h("span", { class: "tag ok" }, "работает")
        : h("span", { class: "tag" }, "выключен");
  }

  function card(p: PluginInfo): HTMLElement {
    const enabled = p.status !== "disabled";
    const sw = toggle(enabled, (v) => void act(p.name, v ? "enable" : "disable"));
    sw.querySelector("input")?.setAttribute("aria-label", `Плагин «${p.title}» включён`);
    const hasSettings = Object.keys(p.settings_schema).length > 0;
    return h(
      "article",
      { class: `plugin-card${enabled ? "" : " off"}`, "data-name": p.name },
      h(
        "header",
        { class: "plugin-head" },
        tile(p),
        h(
          "div",
          { class: "plugin-titles" },
          h("h2", null, p.title),
          h(
            "div",
            { class: "muted mono plugin-meta" },
            `${p.name} · v${p.version}${p.author ? " · " + p.author : ""}`,
          ),
        ),
        sw,
      ),
      h(
        "div",
        { class: "row tags" },
        statusTag(p),
        p.builtin ? h("span", { class: "tag" }, "встроенный") : h("span", { class: "tag" }, "мой"),
      ),
      h("p", { class: "plugin-desc" }, p.description),
      p.error ? h("div", { class: "banner error-box" }, p.error) : null,
      permissions(p),
      p.name === "telegram" ? telegramHelp(!enabled) : null,
      h(
        "footer",
        { class: "plugin-foot" },
        h(
          "button",
          {
            type: "button",
            class: "btn ghost small",
            onclick: (e: MouseEvent) => openSettings(p, e.currentTarget as HTMLElement),
          },
          svgIcon(hasSettings ? icons.settings : icons.info),
          hasSettings ? "Настройки" : "Подробнее",
        ),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn",
            "aria-label": `Перезагрузить «${p.title}»`,
            title: "Перезагрузить",
            onclick: () => void act(p.name, "reload"),
          },
          svgIcon(icons.refresh),
        ),
        p.builtin
          ? null
          : h(
              "button",
              {
                type: "button",
                class: "icon-btn danger-hover",
                "aria-label": `Удалить «${p.title}»`,
                title: "Удалить",
                onclick: () => void remove(p),
              },
              svgIcon(icons.trash),
            ),
      ),
    );
  }

  // -- settings drawer -------------------------------------------------------------------------

  function openSettings(p: PluginInfo, opener: HTMLElement): void {
    drawer?.close();
    opener.focus();
    const d = openDrawer({ title: p.title, subtitle: `${p.name} · v${p.version}` });
    drawer = d;
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
    const entries = Object.entries(p.settings_schema);
    d.body.append(
      h(
        "div",
        { class: "drawer-content" },
        h("p", { class: "plugin-desc" }, p.description),
        p.name === "telegram" ? telegramHelp(true) : null,
        permissions(p),
        entries.length
          ? h(
              "div",
              { class: "grid drawer-form" },
              ...entries.map(([key, spec]) =>
                field(
                  spec.title || key,
                  control(spec, values[key], (v) => void save(key, v)),
                  spec.description,
                  spec.type === "text" || spec.type === "string",
                ),
              ),
            )
          : h("p", { class: "muted" }, "У этого плагина нет настроек."),
        p.commands.length || p.tools.length
          ? h(
              "details",
              { class: "patterns-box" },
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
                ...p.tools.map((t) => h("li", null, `${t.name} — ${t.description}`)),
              ),
            )
          : null,
        h(
          "div",
          { class: "row drawer-foot" },
          h(
            "button",
            { type: "button", class: "btn ghost", onclick: () => void act(p.name, "reload") },
            svgIcon(icons.refresh),
            "Перезагрузить",
          ),
          p.builtin
            ? null
            : h(
                "button",
                { type: "button", class: "btn danger", onclick: () => void remove(p) },
                "Удалить",
              ),
        ),
      ),
    );
  }

  void reload();
  const unsubscribe = store.on("plugin_status_changed", () => void reload());
  return () => {
    unsubscribe();
    drawer?.close();
  };
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
