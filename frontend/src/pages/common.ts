import { api } from "../api";
import { h, toast } from "../dom";
import type { Config } from "../types";

export async function loadConfig(): Promise<Config> {
  return api.get<Config>("/api/config");
}

/** Deep-merge patch into the live config; shows a toast and returns the new config. */
export async function patchConfig(
  patch: Record<string, unknown>,
  quiet = false,
): Promise<Config | null> {
  try {
    const cfg = await api.patch<Config>("/api/config", patch);
    if (!quiet) toast("Сохранено", "", "success");
    return cfg;
  } catch (err) {
    toast("Не сохранено", err instanceof Error ? err.message : String(err), "error");
    return null;
  }
}

export function pageShell(root: HTMLElement, title: string, lead: string): HTMLElement {
  const body = h("div");
  root.append(
    h("div", { class: "page" }, h("h1", null, title), h("p", { class: "lead" }, lead), body),
  );
  return body;
}

export function panel(title: string, ...children: (Node | null)[]): HTMLElement {
  return h("section", { class: "panel" }, h("h2", null, title), ...children);
}

export function failure(body: HTMLElement, err: unknown): void {
  body.replaceChildren(
    h(
      "div",
      { class: "banner" },
      `Не удалось загрузить: ${err instanceof Error ? err.message : err}`,
    ),
  );
}
