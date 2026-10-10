// MCP servers: external tools for the LLM (a panel on the plugins page).

import { api } from "../api";
import { field, h, select, text, toast, toggle } from "../dom";
import { panel } from "./common";

interface McpServerConfig {
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  enabled: boolean;
  tools: string[];
  confirm: "auto" | "always" | "never";
}

interface McpServer {
  name: string;
  config: McpServerConfig;
  state: "connected" | "connecting" | "error" | "off";
  error: string | null;
  tools: string[];
}

const STATE: Record<McpServer["state"], [string, string]> = {
  connected: ["ok", "подключён"],
  connecting: ["warn", "подключается"],
  error: ["err", "ошибка"],
  off: ["", "выключен"],
};

/** Split a command line, keeping "quoted parts" together. */
export function splitCommand(line: string): string[] {
  return [...line.matchAll(/"([^"]*)"|(\S+)/g)].map((m) => m[1] ?? m[2]);
}

export function mcpPanel(): HTMLElement {
  const list = h("div", { class: "mcp-list" });
  const name = text("", () => undefined, { placeholder: "имя, например time" });
  const command = text("", () => undefined, {
    placeholder: "uvx mcp-server-time  ·  или URL http://…/mcp",
  });
  let timer = 0;

  async function reload(): Promise<void> {
    window.clearTimeout(timer);
    if (timer && !list.isConnected) return; // the page was left
    try {
      const servers = await api.get<McpServer[]>("/api/mcp");
      list.replaceChildren(
        ...(servers.length ? servers.map(row) : [h("p", { class: "muted" }, "Серверов пока нет.")]),
      );
      // a server starting up (uvx may download it first): check again soon
      if (servers.some((s) => s.state === "connecting")) {
        timer = window.setTimeout(() => void reload(), 2500);
      }
    } catch (err) {
      toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
    }
  }

  async function save(serverName: string, config: Partial<McpServerConfig>): Promise<void> {
    try {
      await api.put(`/api/mcp/${encodeURIComponent(serverName)}`, config);
      await reload();
    } catch (err) {
      toast("Не сохранено", err instanceof Error ? err.message : String(err), "error");
    }
  }

  async function add(): Promise<void> {
    const serverName = name.value.trim();
    const line = command.value.trim();
    if (!serverName || !line) return;
    if (
      !confirm(
        "MCP-сервер запускается с вашими правами, а модель сможет вызывать его инструменты. Подключайте только доверенные серверы. Продолжить?",
      )
    )
      return;
    const isUrl = /^https?:\/\//.test(line);
    const [cmd, ...args] = isUrl ? [""] : splitCommand(line);
    await save(serverName, isUrl ? { url: line } : { command: cmd, args });
    name.value = "";
    command.value = "";
  }

  function row(s: McpServer): HTMLElement {
    const [cls, label] = STATE[s.state];
    const target = s.config.url || [s.config.command, ...s.config.args].join(" ");
    return h(
      "div",
      { class: "mcp-server" },
      h(
        "div",
        { class: "row" },
        h("h3", null, s.name),
        h("span", { class: `tag ${cls}` }, label),
        h("span", { class: "muted mono mcp-target" }, target),
      ),
      s.error ? h("p", { class: "err-text" }, s.error) : null,
      s.tools.length
        ? h(
            "div",
            { class: "mcp-tools" },
            ...s.tools.map((t) => h("span", { class: "tag mono" }, t)),
          )
        : null,
      h(
        "div",
        { class: "row" },
        toggle(s.config.enabled, (v) => void save(s.name, { ...s.config, enabled: v })),
        field(
          "Подтверждение",
          select(
            [
              ["auto", "кроме только-чтения"],
              ["always", "всегда"],
              ["never", "никогда"],
            ],
            s.config.confirm,
            (v) => void save(s.name, { ...s.config, confirm: v as McpServerConfig["confirm"] }),
          ),
        ),
        h(
          "button",
          {
            class: "btn danger",
            onclick: async () => {
              if (!confirm(`Отключить и удалить сервер ${s.name}?`)) return;
              try {
                await api.del(`/api/mcp/${encodeURIComponent(s.name)}`);
                await reload();
              } catch (err) {
                toast("Ошибка", String(err), "error");
              }
            },
          },
          "Удалить",
        ),
      ),
    );
  }

  void reload();
  return panel(
    "MCP-серверы",
    h(
      "p",
      { class: "muted", style: "margin:0 0 12px;font-size:13px" },
      "Готовые инструменты для модели: файлы, браузер, календарь и сотни других. ",
      "Каждый инструмент занимает место в промте — у локальной модели его немного.",
    ),
    list,
    h(
      "div",
      { class: "row", style: "margin-top:12px" },
      h("div", { style: "flex:0 1 160px" }, name),
      h("div", { style: "flex:1;min-width:220px" }, command),
      h("button", { class: "btn", onclick: () => void add() }, "Подключить"),
    ),
  );
}
