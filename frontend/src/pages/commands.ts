// «Команды»: user commands and scenarios grouped by folder. Toggles per command and per folder,
// search, import / export, and the editor (command-editor.ts). The shared «Ход работы» sits at
// the bottom so a test run is visible while editing.

import { api } from "../api";
import { sharedWorkLog } from "../components/worklog-shared";
import { h, icons, svgIcon, toast, toggle } from "../dom";
import type { CommandListing, CommandSummary } from "../types";
import { commandUrl, createEditor, type Editor } from "./command-editor";
import { failure } from "./common";

const LANG_LABEL = { ru: "RU", en: "EN", both: "RU+EN" } as const;

const norm = (text: string): string => text.toLowerCase().replaceAll("ё", "е");

function matches(c: CommandSummary, q: string): boolean {
  if (!q) return true;
  return norm([c.id, c.name, c.description, ...c.triggers].join(" ")).includes(q);
}

/** A switch whose input carries the accessible name. */
function labelled(label: string, checked: boolean, onChange: (v: boolean) => void): HTMLElement {
  const el = toggle(checked, onChange);
  el.querySelector("input")?.setAttribute("aria-label", label);
  return el;
}

function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function commandsPage(root: HTMLElement): () => void {
  const view = h("div", { class: "cmd-view" });
  const log = sharedWorkLog();
  const dock = h("div", { class: "cmd-dock" }, log.el);
  root.append(
    h(
      "div",
      { class: "page wide" },
      h("h1", null, "Команды"),
      h(
        "p",
        { class: "lead" },
        "Свои фразы и сценарии без кода: что сказать и что при этом сделать. ",
        "Опасные действия (CMD, удаление, питание) всегда переспрашивают.",
      ),
      view,
      dock,
    ),
  );

  let listing: CommandListing = { commands: [], folders: [] };
  let query = "";
  let editor: Editor | null = null;
  const selected = new Set<string>();
  let importReport: string[] = [];

  // -- list view ---------------------------------------------------------------------------

  const search = h("input", {
    type: "search",
    placeholder: "Поиск по названию и фразам",
    "aria-label": "Поиск команд",
  });
  const fileInput = h("input", {
    type: "file",
    accept: ".yaml,.yml,.zip",
    hidden: true,
    "aria-label": "Файл для импорта",
  });
  const listBox = h("div", { class: "cmd-groups" });
  const reportBox = h("div", { class: "banner", role: "status", hidden: true });
  const exportBtn = h("button", {
    type: "button",
    class: "btn ghost",
    onclick: () => void exportSelected(),
  });
  const listView = h(
    "div",
    null,
    h(
      "div",
      { class: "row toolbar" },
      h("div", { class: "grow" }, search),
      h(
        "button",
        { type: "button", class: "btn", onclick: () => openEditor(null) },
        svgIcon(icons.plus),
        "Создать",
      ),
      h(
        "button",
        { type: "button", class: "btn ghost", onclick: () => fileInput.click() },
        svgIcon(icons.upload),
        "Импорт",
      ),
      exportBtn,
      fileInput,
    ),
    reportBox,
    listBox,
  );

  async function reload(): Promise<void> {
    try {
      listing = await api.get<CommandListing>("/api/commands");
      for (const id of [...selected]) {
        if (!listing.commands.some((c) => c.id === id)) selected.delete(id);
      }
      renderList();
    } catch (err) {
      failure(listBox, err);
    }
  }

  function syncExport(): void {
    exportBtn.replaceChildren(
      svgIcon(icons.download),
      selected.size ? `Экспорт (${selected.size})` : "Экспорт",
    );
    exportBtn.title = selected.size
      ? "Скачать выбранные команды в .zip"
      : "Ничего не выбрано: скачать все команды в .zip";
  }

  function renderList(): void {
    const q = norm(query.trim());
    const byFolder = new Map<string, CommandSummary[]>();
    for (const f of listing.folders) byFolder.set(f.path, []);
    for (const c of listing.commands) {
      if (!byFolder.has(c.folder)) byFolder.set(c.folder, []);
      if (matches(c, q)) byFolder.get(c.folder)!.push(c);
    }
    const root = byFolder.get("") ?? [];
    byFolder.delete("");
    const sections: HTMLElement[] = [];
    if (root.length || (!q && !byFolder.size)) sections.push(group("", root));
    for (const [path, items] of [...byFolder].sort(([a], [b]) => a.localeCompare(b, "ru"))) {
      if (q && !items.length) continue;
      sections.push(group(path, items));
    }
    if (!listing.commands.length) {
      listBox.replaceChildren(
        h(
          "div",
          { class: "empty-state" },
          h("p", { class: "empty-title" }, "Команд пока нет"),
          h(
            "p",
            { class: "muted" },
            "Создайте первую или включите готовый набор на вкладке ",
            h("a", { href: "#/commands/packs" }, "«Паки и режим»"),
            ".",
          ),
        ),
      );
    } else if (q && sections.length === 0) {
      listBox.replaceChildren(
        h("p", { class: "muted" }, `Ничего не найдено по запросу «${query.trim()}».`),
      );
    } else {
      listBox.replaceChildren(...sections);
    }
    syncExport();
  }

  function group(path: string, items: CommandSummary[]): HTMLElement {
    const folder = listing.folders.find((f) => f.path === path);
    const title = path || "Мои команды";
    const headId = `cmd-folder-${path.replace(/\W+/g, "-") || "root"}`;
    return h(
      "section",
      {
        class: `panel cmd-folder${folder && !folder.enabled ? " off" : ""}`,
        "aria-labelledby": headId,
      },
      h(
        "div",
        { class: "cmd-folder-head" },
        svgIcon(icons.folder),
        h("h2", { id: headId }, title),
        path.startsWith("packs/") ? h("span", { class: "tag" }, "пак") : null,
        h("span", { class: "muted count" }, `${items.length}`),
        h("span", { class: "spacer" }),
        folder
          ? labelled(`Папка ${title} включена`, folder.enabled, (v) => void toggleFolder(path, v))
          : null,
      ),
      items.length
        ? h("ul", { class: "cmd-list" }, ...items.map(row))
        : h(
            "p",
            { class: "muted cmd-none" },
            path ? "В папке нет команд." : "Здесь появятся ваши команды.",
          ),
    );
  }

  function row(c: CommandSummary): HTMLElement {
    const folder = listing.folders.find((f) => f.path === c.folder);
    const check = h("input", {
      type: "checkbox",
      checked: selected.has(c.id),
      "aria-label": `Выбрать «${c.name}»`,
      onchange: () => {
        if (check.checked) selected.add(c.id);
        else selected.delete(c.id);
        syncExport();
      },
    });
    const phrases = c.triggers.slice(0, 3).map((t) => `«${t}»`);
    const more = c.triggers.length - phrases.length;
    return h(
      "li",
      { class: `cmd-row${c.enabled ? "" : " off"}${c.error ? " broken" : ""}`, "data-id": c.id },
      check,
      h(
        "div",
        { class: "cmd-main" },
        h(
          "div",
          { class: "cmd-title" },
          h(
            "button",
            { type: "button", class: "link-btn", onclick: () => openEditor(c.id) },
            c.name,
          ),
          c.dangerous
            ? h("span", { class: "tag warn", title: "Есть опасные действия" }, "опасная")
            : null,
          h("span", { class: "tag", title: "Язык фраз" }, LANG_LABEL[c.lang] ?? c.lang),
          c.error ? h("span", { class: "tag err" }, "ошибка") : null,
        ),
        c.error
          ? h("p", { class: "err-text", title: c.error }, c.error)
          : h(
              "p",
              { class: "cmd-triggers" },
              phrases.length
                ? phrases.join(" · ")
                : "нет фраз: запускается из других команд или кнопкой",
              more > 0 ? ` и ещё ${more}` : "",
            ),
        c.description && !c.error ? h("p", { class: "cmd-desc muted" }, c.description) : null,
      ),
      h(
        "div",
        { class: "cmd-actions" },
        (() => {
          const t = labelled(`«${c.name}» включена`, c.enabled, (v) => void toggleCommand(c.id, v));
          const input = t.querySelector("input")!;
          input.disabled = Boolean(c.error) || Boolean(folder && !folder.enabled);
          if (input.disabled) t.title = c.error ? "В файле ошибка" : "Папка выключена";
          return t;
        })(),
        c.error
          ? null
          : (() => {
              const b = h(
                "button",
                {
                  type: "button",
                  class: "icon-btn",
                  "aria-label": `Проверить «${c.name}»`,
                  title: "Проверить без голоса",
                  onclick: () => void testCommand(c.id),
                },
                svgIcon(icons.play),
              );
              return b;
            })(),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn",
            "aria-label": `Изменить «${c.name}»`,
            title: "Изменить",
            onclick: () => openEditor(c.id),
          },
          svgIcon(icons.edit),
        ),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn",
            "aria-label": `Удалить «${c.name}»`,
            title: "Удалить",
            onclick: () => void removeCommand(c),
          },
          svgIcon(icons.trash),
        ),
      ),
    );
  }

  async function toggleCommand(id: string, enabled: boolean): Promise<void> {
    try {
      await api.post(`${commandUrl(id)}/toggle`, { enabled });
    } catch (err) {
      toast("Не получилось", err instanceof Error ? err.message : String(err), "error");
    }
    await reload();
  }

  async function toggleFolder(path: string, enabled: boolean): Promise<void> {
    try {
      await api.post("/api/commands/folders/toggle", { path, enabled });
    } catch (err) {
      toast("Не получилось", err instanceof Error ? err.message : String(err), "error");
    }
    await reload();
  }

  async function testCommand(id: string): Promise<void> {
    try {
      await api.post(`${commandUrl(id)}/test`);
      log.setExpanded(true);
      dock.scrollIntoView({ block: "nearest" });
    } catch (err) {
      toast("Не запущено", err instanceof Error ? err.message : String(err), "error");
    }
  }

  async function removeCommand(c: CommandSummary): Promise<void> {
    if (!confirm(`Удалить команду «${c.name}»? Файл будет удалён.`)) return;
    try {
      await api.del(commandUrl(c.id));
      toast("Удалено", c.name, "success");
    } catch (err) {
      toast("Не удалено", err instanceof Error ? err.message : String(err), "error");
    }
    await reload();
  }

  async function importFile(file: File): Promise<void> {
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const res = await api.upload<{ imported: string[]; errors: string[] }>(
        "/api/commands/import",
        form,
      );
      importReport = res.errors;
      toast(
        res.imported.length ? "Импортировано" : "Ничего не импортировано",
        res.imported.length ? `Команд: ${res.imported.length}` : "",
        res.imported.length ? "success" : "warning",
      );
    } catch (err) {
      importReport = [err instanceof Error ? err.message : String(err)];
      toast("Импорт не удался", importReport[0], "error");
    }
    reportBox.hidden = importReport.length === 0;
    reportBox.replaceChildren(
      h("b", null, "Замечания при импорте"),
      h("ul", null, ...importReport.map((m) => h("li", null, m))),
    );
    await reload();
  }

  async function exportSelected(): Promise<void> {
    const ids = [...selected].map(encodeURIComponent).join(",");
    try {
      const blob = await api.get<Blob>(`/api/commands/export${ids ? `?ids=${ids}` : ""}`);
      download(blob, "aion-commands.zip");
    } catch (err) {
      toast("Экспорт не удался", err instanceof Error ? err.message : String(err), "error");
    }
  }

  // -- switching between the list and the editor -----------------------------------------------

  function showList(): void {
    editor?.dispose();
    editor = null;
    view.replaceChildren(listView);
    void reload();
  }

  function openEditor(id: string | null): void {
    const folder = id === null ? "" : (listing.commands.find((c) => c.id === id)?.folder ?? "");
    editor?.dispose();
    editor = createEditor({
      id,
      folder,
      all: listing.commands,
      onClose: () => showList(),
    });
    view.replaceChildren(editor.el);
    root.closest(".main")?.scrollTo({ top: 0 });
    editor.el.querySelector<HTMLElement>("input, button")?.focus();
  }

  search.addEventListener("input", () => {
    query = search.value;
    renderList();
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (file) void importFile(file);
  });

  showList();
  return () => editor?.dispose();
}
