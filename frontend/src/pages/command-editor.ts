// Command editor: name, trigger phrases, language, confirmation mode and a list of typed actions
// (add / remove / reorder with buttons, so it works from the keyboard too). Saves with PUT,
// shows the server's validation messages next to the field they belong to, tests without voice.

import { ApiError, api } from "../api";
import { sharedWorkLog } from "../components/worklog-shared";
import { h, icons, svgIcon, toast, toggle } from "../dom";
import type { CommandAction, CommandDetail, CommandLang, CommandSummary } from "../types";
import {
  ACTIONS,
  ANTI_CHEAT_WARNING,
  actionDef,
  isDangerous,
  renderFields,
} from "./command-actions";

const LANGS: [CommandLang, string][] = [
  ["ru", "Русский"],
  ["en", "English"],
  ["both", "Русский и English"],
];

const CONFIRMS: [string, string][] = [
  ["auto", "Спрашивать при опасных"],
  ["always", "Всегда спрашивать"],
  ["never", "Не спрашивать"],
];

export const commandUrl = (id: string): string =>
  `/api/commands/${id.split("/").map(encodeURIComponent).join("/")}`;

/** A file name for a new command from its title: lower case, spaces and junk to dashes. */
export function slugify(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_.-]+/gu, "-")
    .replace(/^[-.]+|[-.]+$/g, "")
    .slice(0, 60);
  return slug || "command";
}

export interface EditorOptions {
  /** Edit this command; null creates a new one. */
  id: string | null;
  /** Folder of a new command ("" is the root). */
  folder: string;
  /** All commands (to pick from in «Запустить команду» and to keep new ids unique). */
  all: CommandSummary[];
  /** Back to the list; `changed` means the list must be reloaded. */
  onClose: (changed: boolean) => void;
}

export interface Editor {
  el: HTMLElement;
  dispose(): void;
}

interface Draft {
  id: string;
  isNew: boolean;
  idTouched: boolean;
  name: string;
  description: string;
  lang: CommandLang;
  confirm: string;
  enabled: boolean;
  triggers: string[];
  actions: CommandAction[];
}

const field = (label: string, el: HTMLElement, id: string, hint = "", wide = false): HTMLElement =>
  h(
    "div",
    { class: `field${wide ? " wide" : ""}` },
    h("label", { for: id, class: "field-label" }, label),
    el,
    hint ? h("small", null, hint) : null,
  );

export function createEditor(opts: EditorOptions): Editor {
  const el = h("div", { class: "cmd-editor" }, h("p", { class: "muted" }, "Загрузка…"));
  let draft: Draft | null = null;
  let dirty = false;
  let changed = false;
  let recording = false;
  let disposed = false;
  let errors: string[] = [];
  let uid = 0;

  const others = (): [string, string][] =>
    opts.all
      .filter((c) => c.id !== draft?.id && !c.error)
      .map((c): [string, string] => [c.id, `${c.name} (${c.id})`]);

  // -- loading ---------------------------------------------------------------------------

  async function load(): Promise<void> {
    if (opts.id === null) {
      draft = {
        id: "",
        isNew: true,
        idTouched: false,
        name: "",
        description: "",
        lang: "ru",
        confirm: "auto",
        enabled: true,
        triggers: [""],
        actions: [{ type: "say", text: "" }],
      };
      build();
      return;
    }
    try {
      const d = await api.get<CommandDetail>(commandUrl(opts.id));
      if (disposed) return;
      if (d.error || d.raw !== undefined) {
        el.replaceChildren(broken(d));
        return;
      }
      draft = {
        id: d.id,
        isNew: false,
        idTouched: true,
        name: d.name ?? "",
        description: d.description ?? "",
        lang: d.lang ?? "ru",
        confirm: d.confirm ?? "auto",
        enabled: d.enabled ?? true,
        triggers: d.triggers?.length ? [...d.triggers] : [""],
        actions: structuredClone(d.actions ?? []),
      };
      build();
    } catch (err) {
      el.replaceChildren(
        backBar(),
        h("div", { class: "banner" }, err instanceof Error ? err.message : String(err)),
      );
    }
  }

  /** A file that does not parse: show the reason and the text, allow deleting. */
  function broken(d: CommandDetail): HTMLElement {
    return h(
      "div",
      null,
      backBar(),
      h(
        "section",
        { class: "panel" },
        h("h2", null, d.id),
        h("div", { class: "banner", role: "alert" }, `Файл не читается: ${d.error ?? "ошибка"}`),
        h(
          "p",
          { class: "muted" },
          "Исправьте файл в папке команд или удалите его. Текст файла для сверки:",
        ),
        h("pre", { class: "worklog-output raw-file", tabindex: 0 }, d.raw ?? ""),
        h(
          "div",
          { class: "row", style: "margin-top:12px" },
          h(
            "button",
            { type: "button", class: "btn danger", onclick: () => void remove(d.id) },
            "Удалить файл",
          ),
        ),
      ),
    );
  }

  function backBar(): HTMLElement {
    return h(
      "div",
      { class: "row editor-back" },
      h(
        "button",
        { type: "button", class: "btn ghost", onclick: () => leave() },
        svgIcon(icons.chevron),
        "К списку",
      ),
    );
  }

  function leave(): void {
    if (dirty && !confirm("Несохранённые изменения будут потеряны. Выйти?")) return;
    opts.onClose(changed);
  }

  // -- building ----------------------------------------------------------------------------

  const status = h("span", { class: "muted editor-status", role: "status" });
  const errorBox = h("div", { class: "banner error-box", role: "alert", hidden: true });
  const saveBtn = h(
    "button",
    { type: "button", class: "btn", onclick: () => void save() },
    "Сохранить",
  );
  const testBtn = h(
    "button",
    {
      type: "button",
      class: "btn ghost",
      title: "Выполнить сейчас без голоса: ход виден внизу",
      onclick: () => void test(),
    },
    svgIcon(icons.play),
    "Проверить",
  );
  const deleteBtn = h(
    "button",
    { type: "button", class: "btn danger", onclick: () => void remove(draft!.id) },
    "Удалить",
  );
  const titleEl = h("h2", { class: "editor-title" });
  const dangerTag = h("span", { class: "tag warn", hidden: true }, "опасная");
  const idInput = h("input", {
    id: "cmd-id",
    type: "text",
    autocomplete: "off",
    spellcheck: false,
  });
  const triggersBox = h("div", { class: "trigger-list" });
  const actionsBox = h("ol", { class: "action-list" });
  const recordBtn = h("button", {
    type: "button",
    class: "btn ghost",
    onclick: () => void toggleRecord(),
  });
  const addType = h("select", { id: "cmd-add-type", "aria-label": "Тип нового действия" });
  for (const a of ACTIONS) addType.append(h("option", { value: a.type }, a.label));

  function markDirty(): void {
    dirty = true;
    syncHeader();
  }

  function syncHeader(): void {
    if (!draft) return;
    titleEl.textContent = draft.name.trim() || (draft.isNew ? "Новая команда" : draft.id);
    dangerTag.hidden = !draft.actions.some(isDangerous);
    deleteBtn.hidden = draft.isNew;
    status.textContent = dirty ? "Есть несохранённые изменения" : "";
    if (draft.isNew && !draft.idTouched) {
      draft.id = uniqueId(`${opts.folder ? opts.folder + "/" : ""}${slugify(draft.name)}`);
      idInput.value = draft.id;
    }
  }

  function uniqueId(base: string): string {
    const taken = new Set(opts.all.map((c) => c.id));
    let id = base;
    for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
    return id;
  }

  function build(): void {
    const d = draft!;
    idInput.value = d.id || "";
    idInput.addEventListener("input", () => {
      d.id = idInput.value.trim();
      d.idTouched = true;
      markDirty();
    });
    const name = h("input", {
      id: "cmd-name",
      type: "text",
      value: d.name,
      placeholder: "Протокол гостей",
      autocomplete: "off",
    });
    name.addEventListener("input", () => {
      d.name = name.value;
      markDirty();
    });
    const desc = h("textarea", { id: "cmd-desc", rows: 2 });
    desc.value = d.description;
    desc.addEventListener("input", () => {
      d.description = desc.value;
      markDirty();
    });
    const lang = h("select", { id: "cmd-lang" });
    for (const [v, label] of LANGS)
      lang.append(h("option", { value: v, selected: v === d.lang }, label));
    lang.addEventListener("change", () => {
      d.lang = lang.value as CommandLang;
      markDirty();
    });
    const confirmSel = h("select", { id: "cmd-confirm" });
    for (const [v, label] of CONFIRMS) {
      confirmSel.append(h("option", { value: v, selected: v === d.confirm }, label));
    }
    const confirmNote = h("small", { class: "warn-text" });
    const syncConfirm = (): void => {
      confirmNote.textContent =
        d.confirm === "never" && d.actions.some(isDangerous)
          ? "В команде есть опасные действия, а вопроса не будет."
          : "";
    };
    confirmSel.addEventListener("change", () => {
      d.confirm = confirmSel.value;
      syncConfirm();
      markDirty();
    });
    addType.addEventListener("change", () => undefined);

    const basics = h(
      "section",
      { class: "panel", "aria-labelledby": "cmd-basics" },
      h("h2", { id: "cmd-basics" }, "Основное"),
      h(
        "div",
        { class: "grid" },
        field("Название", name, "cmd-name"),
        d.isNew ? field("Файл (id)", idInput, "cmd-id", "Папка/имя, например work/standup") : null,
        field("Язык фраз", lang, "cmd-lang"),
        field("Подтверждение", h("div", null, confirmSel, confirmNote), "cmd-confirm"),
        field("Описание", desc, "cmd-desc", "", true),
        h(
          "div",
          { class: "field" },
          h("span", null, "Включена"),
          toggle(d.enabled, (v) => {
            d.enabled = v;
            markDirty();
          }),
        ),
      ),
    );

    const triggersPanel = h(
      "section",
      { class: "panel", "aria-labelledby": "cmd-triggers" },
      h("h2", { id: "cmd-triggers" }, "Фразы"),
      h(
        "p",
        { class: "muted hint" },
        "Что нужно сказать. ",
        h("code", null, "[слово]"),
        " — необязательное, ",
        h("code", null, "(а|б)"),
        " — любое из, ",
        h("code", null, "{тема}"),
        " — подставляется в действия.",
      ),
      triggersBox,
      h(
        "button",
        { type: "button", class: "btn ghost", onclick: () => addTrigger() },
        svgIcon(icons.plus),
        "Добавить фразу",
      ),
    );

    const actionsPanel = h(
      "section",
      { class: "panel", "aria-labelledby": "cmd-actions" },
      h("h2", { id: "cmd-actions" }, "Действия"),
      h(
        "p",
        { class: "muted hint" },
        "Выполняются сверху вниз. Больше одного действия — сценарий.",
      ),
      actionsBox,
      h(
        "div",
        { class: "row action-add" },
        addType,
        h(
          "button",
          { type: "button", class: "btn ghost", onclick: () => addAction(addType.value) },
          svgIcon(icons.plus),
          "Добавить действие",
        ),
        h("span", { class: "spacer" }),
        recordBtn,
      ),
      h(
        "p",
        { class: "muted hint record-hint" },
        "«Записать действия» запоминает нажатия клавиш и щелчки мыши по всему экрану. ",
        "Щелчок по кнопке остановки тоже может попасть в запись, удалите его.",
      ),
    );

    el.replaceChildren(
      h(
        "div",
        { class: "editor-head" },
        h(
          "button",
          { type: "button", class: "btn ghost", onclick: () => leave() },
          svgIcon(icons.chevron),
          "К списку",
        ),
        titleEl,
        dangerTag,
        h("span", { class: "spacer" }),
        status,
        testBtn,
        saveBtn,
        deleteBtn,
      ),
      errorBox,
      basics,
      triggersPanel,
      actionsPanel,
    );
    syncConfirm();
    renderTriggers();
    renderActions();
    syncRecord();
    syncHeader();
    dirty = false;
    syncHeader();
  }

  // -- triggers ----------------------------------------------------------------------------

  function renderTriggers(focus = -1): void {
    const d = draft!;
    triggersBox.replaceChildren(
      ...d.triggers.map((t, i) => {
        const input = h("input", {
          type: "text",
          value: t,
          "aria-label": `Фраза ${i + 1}`,
          placeholder: "протокол гостей",
          autocomplete: "off",
        });
        input.addEventListener("input", () => {
          d.triggers[i] = input.value;
          markDirty();
        });
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            addTrigger(i + 1);
          }
        });
        const own = errors.filter((m) => m.startsWith(`фраза ${i + 1}`));
        return h(
          "div",
          { class: "trigger-row" },
          h(
            "div",
            { class: "row nowrap" },
            h("div", { class: "grow" }, input),
            h(
              "button",
              {
                type: "button",
                class: "icon-btn",
                "aria-label": `Удалить фразу ${i + 1}`,
                title: "Удалить фразу",
                disabled: d.triggers.length < 2 && !t,
                onclick: () => {
                  d.triggers.splice(i, 1);
                  if (!d.triggers.length) d.triggers.push("");
                  markDirty();
                  renderTriggers(Math.min(i, d.triggers.length - 1));
                },
              },
              svgIcon(icons.trash),
            ),
          ),
          ...own.map((m) => h("p", { class: "err-text" }, m)),
        );
      }),
    );
    if (focus >= 0) triggersBox.querySelectorAll("input")[focus]?.focus();
  }

  function addTrigger(at = draft!.triggers.length): void {
    draft!.triggers.splice(at, 0, "");
    markDirty();
    renderTriggers(at);
  }

  // -- actions -----------------------------------------------------------------------------

  /** Redraw the list; keep the keyboard focus on the same button after a move. */
  function renderActions(focus?: { index: number; part: string }): void {
    const d = draft!;
    actionsBox.replaceChildren(...d.actions.map((a, i) => actionCard(a, i)));
    if (!d.actions.length) {
      actionsBox.append(h("li", { class: "action-empty muted" }, "Пока ни одного действия."));
    }
    if (focus) {
      const target = actionsBox.querySelector<HTMLElement>(
        `[data-index="${focus.index}"] [data-part="${focus.part}"]`,
      );
      (target && !(target as HTMLButtonElement).disabled
        ? target
        : actionsBox.querySelector<HTMLElement>(`[data-index="${focus.index}"] [data-part="type"]`)
      )?.focus();
    }
  }

  function actionCard(a: CommandAction, i: number): HTMLElement {
    const d = draft!;
    const def = actionDef(a.type);
    const key = `act-${++uid}`;
    const typeSel = h("select", {
      "aria-label": `Тип действия ${i + 1}`,
      "data-part": "type",
    });
    for (const t of ACTIONS)
      typeSel.append(h("option", { value: t.type, selected: t.type === a.type }, t.label));
    if (!def) typeSel.append(h("option", { value: a.type, selected: true }, a.type));
    typeSel.addEventListener("change", () => {
      const next = actionDef(typeSel.value)?.create();
      if (!next) return;
      d.actions[i] = next;
      markDirty();
      renderActions({ index: i, part: "type" });
    });

    const fields = h("div", { class: "action-body" });
    const draw = (): void => {
      fields.replaceChildren(
        renderFields(
          a,
          {
            commands: others(),
            onChange: () => {
              markDirty();
              danger.hidden = !isDangerous(a);
            },
            refresh: draw,
          },
          key,
        ),
      );
    };
    const danger = h("span", { class: "tag warn", hidden: !isDangerous(a) }, "опасное");
    draw();

    const move = (by: number): void => {
      const j = i + by;
      if (j < 0 || j >= d.actions.length) return;
      [d.actions[i], d.actions[j]] = [d.actions[j], d.actions[i]];
      markDirty();
      renderActions({ index: j, part: by < 0 ? "up" : "down" });
    };
    const own = errors.filter((m) => m.startsWith(`действие ${i + 1}`));
    return h(
      "li",
      { class: "action-card", "data-index": i, "data-type": a.type },
      h(
        "div",
        { class: "action-head" },
        h("span", { class: "action-num", "aria-hidden": "true" }, i + 1),
        typeSel,
        danger,
        h("span", { class: "spacer" }),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn",
            "data-part": "up",
            "aria-label": `Поднять действие ${i + 1}`,
            title: "Выше",
            disabled: i === 0,
            onclick: () => move(-1),
          },
          svgIcon(icons.up),
        ),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn",
            "data-part": "down",
            "aria-label": `Опустить действие ${i + 1}`,
            title: "Ниже",
            disabled: i === d.actions.length - 1,
            onclick: () => move(1),
          },
          svgIcon(icons.down),
        ),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn",
            "data-part": "remove",
            "aria-label": `Удалить действие ${i + 1}`,
            title: "Удалить",
            onclick: () => {
              d.actions.splice(i, 1);
              markDirty();
              renderActions({ index: Math.min(i, d.actions.length - 1), part: "remove" });
            },
          },
          svgIcon(icons.trash),
        ),
      ),
      def ? h("p", { class: "muted hint" }, def.hint) : null,
      fields,
      def?.emulatesInput ? h("p", { class: "warn-text anti-cheat" }, ANTI_CHEAT_WARNING) : null,
      ...own.map((m) => h("p", { class: "err-text" }, m)),
    );
  }

  function addAction(type: string): void {
    const next = actionDef(type)?.create();
    if (!next) return;
    const d = draft!;
    d.actions.push(next);
    markDirty();
    renderActions({ index: d.actions.length - 1, part: "type" });
  }

  // -- recording ---------------------------------------------------------------------------

  function syncRecord(): void {
    recordBtn.classList.toggle("recording", recording);
    recordBtn.replaceChildren(
      svgIcon(recording ? icons.stop : icons.record),
      recording ? "Остановить запись" : "Записать действия",
    );
    recordBtn.setAttribute("aria-pressed", String(recording));
  }

  async function toggleRecord(): Promise<void> {
    try {
      if (!recording) {
        await api.post("/api/commands/record/start");
        recording = true;
        syncRecord();
        toast("Запись идёт", "Выполните нужные действия и вернитесь сюда", "info");
        return;
      }
      const { actions } = await api.post<{ actions: CommandAction[] }>("/api/commands/record/stop");
      recording = false;
      syncRecord();
      const d = draft!;
      d.actions.push(...actions);
      markDirty();
      renderActions({ index: Math.max(0, d.actions.length - 1), part: "type" });
      toast(
        actions.length ? "Записано" : "Ничего не записано",
        actions.length ? `Добавлено действий: ${actions.length}` : "",
        actions.length ? "success" : "warning",
      );
    } catch (err) {
      recording = false;
      syncRecord();
      toast("Запись не удалась", err instanceof Error ? err.message : String(err), "error");
    }
  }

  // -- save, test, delete --------------------------------------------------------------------

  function showErrors(list: string[]): void {
    errors = list;
    // messages with a place (a phrase, an action) are drawn next to it, the rest on top
    const general = list.filter((m) => !/^(действие|фраза) \d+/.test(m));
    errorBox.hidden = list.length === 0;
    errorBox.replaceChildren(
      h("b", null, "Команда не сохранена: исправьте ошибки"),
      ...general.map((m) => h("div", null, m)),
    );
    renderTriggers();
    renderActions();
    if (list.length) errorBox.scrollIntoView({ block: "nearest" });
  }

  async function save(): Promise<boolean> {
    const d = draft!;
    if (d.isNew && !d.id) {
      showErrors(["Укажите название или файл (id)"]);
      return false;
    }
    const body = {
      name: d.name.trim(),
      triggers: d.triggers.map((t) => t.trim()).filter(Boolean),
      lang: d.lang,
      enabled: d.enabled,
      confirm: d.confirm,
      description: d.description.trim(),
      actions: d.actions,
    };
    saveBtn.disabled = true;
    try {
      await api.put<CommandSummary>(commandUrl(d.id), body);
      showErrors([]);
      d.isNew = false;
      dirty = false;
      changed = true;
      syncHeader();
      // the file name is fixed after the first save
      el.querySelector("#cmd-id")?.closest(".field")?.remove();
      toast("Сохранено", d.name.trim(), "success");
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) showErrors(err.details);
      else showErrors([err instanceof Error ? err.message : String(err)]);
      return false;
    } finally {
      saveBtn.disabled = false;
    }
  }

  async function test(): Promise<void> {
    const d = draft!;
    if ((dirty || d.isNew) && !(await save())) return;
    try {
      await api.post<{ run_id: string }>(`${commandUrl(d.id)}/test`);
      sharedWorkLog().setExpanded(true);
    } catch (err) {
      toast("Не запущено", err instanceof Error ? err.message : String(err), "error");
    }
  }

  async function remove(id: string): Promise<void> {
    if (!confirm(`Удалить команду ${id}?`)) return;
    try {
      await api.del(commandUrl(id));
      dirty = false;
      opts.onClose(true);
    } catch (err) {
      toast("Не удалено", err instanceof Error ? err.message : String(err), "error");
    }
  }

  void load();

  return {
    el,
    dispose: () => {
      disposed = true;
      // a forgotten recording keeps hooking the keyboard
      if (recording) void api.post("/api/commands/record/stop").catch(() => undefined);
    },
  };
}
