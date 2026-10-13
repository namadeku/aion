// Typed forms for the action types of a user command (docs/commands-api.md, section 3).
// Each type declares its fields; `renderFields` turns an action object into inputs that edit
// that object in place and call `onChange` (the editor marks the draft as unsaved).

import { h } from "../dom";
import type { CommandAction } from "../types";

type Option = [string, string];

interface FieldDef {
  key: string;
  label: string;
  control: "text" | "mono" | "number" | "select";
  options?: Option[];
  placeholder?: string;
  hint?: string;
  min?: number;
  max?: number;
  step?: number;
  /** An empty value removes the key from the action. */
  optional?: boolean;
  wide?: boolean;
  /** Show the field only for some values of the other fields. */
  when?: (a: CommandAction) => boolean;
}

export interface ActionDef {
  type: string;
  label: string;
  /** Short hint under the type. */
  hint: string;
  fields: FieldDef[];
  create: () => CommandAction;
  dangerous?: (a: CommandAction) => boolean;
  /** Emulated input: games with an anti-cheat may ignore it. */
  emulatesInput?: boolean;
}

export const ANTI_CHEAT_WARNING =
  "Игры с античитом (Valorant, CS2 и другие) могут игнорировать или блокировать эмулированные клавиши и мышь.";

const POWER: Option[] = [
  ["lock", "Заблокировать компьютер"],
  ["sleep", "Спящий режим"],
  ["restart", "Перезагрузить"],
  ["shutdown", "Выключить"],
];

export const ACTIONS: ActionDef[] = [
  {
    type: "open",
    label: "Открыть",
    hint: "Программа, папка, файл или адрес сайта",
    fields: [
      {
        key: "target",
        label: "Что открыть",
        control: "text",
        placeholder: "https://music.yandex.ru или Блокнот",
        wide: true,
      },
    ],
    create: () => ({ type: "open", target: "" }),
  },
  {
    type: "search",
    label: "Поиск в интернете",
    hint: "Открывает результаты поиска в браузере",
    fields: [
      { key: "query", label: "Запрос", control: "text", placeholder: "{topic} youtube" },
      {
        key: "engine",
        label: "Поисковик",
        control: "text",
        optional: true,
        placeholder: "по умолчанию",
      },
    ],
    create: () => ({ type: "search", query: "" }),
  },
  {
    type: "keys",
    label: "Клавиши",
    hint: "Сочетание клавиш или набор текста",
    emulatesInput: true,
    fields: [
      {
        key: "combo",
        label: "Сочетание",
        control: "text",
        placeholder: "ctrl+shift+esc",
        hint: "Модификаторы ctrl shift alt win, до 5 клавиш; esc, enter, f1…f20, playpause…",
        wide: true,
        when: (a) => a.text === undefined,
      },
      {
        key: "text",
        label: "Текст для набора",
        control: "text",
        wide: true,
        when: (a) => a.text !== undefined,
      },
    ],
    create: () => ({ type: "keys", combo: "" }),
  },
  {
    type: "mouse",
    label: "Мышь",
    hint: "Перемещение, щелчок, прокрутка",
    emulatesInput: true,
    fields: [
      {
        key: "action",
        label: "Действие",
        control: "select",
        options: [
          ["click", "Щелчок"],
          ["right", "Правый щелчок"],
          ["double", "Двойной щелчок"],
          ["move", "Переместить"],
          ["scroll", "Прокрутка"],
        ],
      },
      { key: "x", label: "X", control: "number", optional: true, step: 1, placeholder: "текущая" },
      { key: "y", label: "Y", control: "number", optional: true, step: 1, placeholder: "текущая" },
      {
        key: "amount",
        label: "Щелчков колеса",
        control: "number",
        optional: true,
        step: 1,
        min: -100,
        max: 100,
        hint: "Минус — вниз",
        when: (a) => a.action === "scroll",
      },
    ],
    create: () => ({ type: "mouse", action: "click" }),
  },
  {
    type: "wait",
    label: "Пауза",
    hint: "Подождать перед следующим шагом",
    fields: [{ key: "seconds", label: "Секунд", control: "number", min: 0.1, max: 600, step: 0.1 }],
    create: () => ({ type: "wait", seconds: 1 }),
  },
  {
    type: "volume",
    label: "Громкость",
    hint: "Системная или одной программы",
    fields: [
      {
        key: "set",
        label: "Уровень, %",
        control: "number",
        min: 0,
        max: 100,
        step: 1,
        when: (a) => "set" in a,
      },
      {
        key: "change",
        label: "Изменить на, %",
        control: "number",
        min: -100,
        max: 100,
        step: 1,
        when: (a) => "change" in a,
      },
      {
        key: "app",
        label: "Программа",
        control: "text",
        optional: true,
        placeholder: "вся система; или spotify, discord.exe",
        hint: "Имя процесса из микшера Windows",
      },
    ],
    create: () => ({ type: "volume", set: 50 }),
  },
  {
    type: "window",
    label: "Окно",
    hint: "Свернуть, развернуть, закрыть или переключиться",
    dangerous: (a) => a.action === "close",
    fields: [
      {
        key: "action",
        label: "Действие",
        control: "select",
        options: [
          ["minimize", "Свернуть"],
          ["maximize", "Развернуть"],
          ["focus", "Переключиться"],
          ["close", "Закрыть"],
        ],
      },
      { key: "name", label: "Окно или программа", control: "text", placeholder: "Chrome" },
    ],
    create: () => ({ type: "window", action: "minimize", name: "" }),
  },
  {
    type: "minimize_all",
    label: "Свернуть все окна",
    hint: "Показать рабочий стол",
    fields: [],
    create: () => ({ type: "minimize_all" }),
  },
  {
    type: "shell",
    label: "CMD / PowerShell",
    hint: "Запускает команду без окна; вывод виден в «Ходе работы»",
    dangerous: () => true,
    fields: [
      {
        key: "shell",
        label: "Оболочка",
        control: "select",
        options: [
          ["cmd", "CMD"],
          ["powershell", "PowerShell"],
        ],
      },
      {
        key: "timeout",
        label: "Время, с",
        control: "number",
        optional: true,
        min: 1,
        max: 120,
        step: 1,
        placeholder: "по умолчанию",
      },
      { key: "command", label: "Команда", control: "mono", wide: true },
    ],
    create: () => ({ type: "shell", shell: "cmd", command: "" }),
  },
  {
    type: "power",
    label: "Питание",
    hint: "Блокировка, сон, перезагрузка, выключение",
    dangerous: (a) => a.action !== "lock",
    fields: [{ key: "action", label: "Действие", control: "select", options: POWER }],
    create: () => ({ type: "power", action: "lock" }),
  },
  {
    type: "delete",
    label: "Удалить в корзину",
    hint: "Только в корзину; корни дисков и системные папки не удаляются",
    dangerous: () => true,
    fields: [
      {
        key: "path",
        label: "Файл или папка",
        control: "text",
        placeholder: "~/Downloads/старое.zip",
        wide: true,
      },
    ],
    create: () => ({ type: "delete", path: "" }),
  },
  {
    type: "file",
    label: "Создать файл",
    hint: "Текстовый файл; если он уже есть, Aion спросит",
    fields: [
      {
        key: "path",
        label: "Путь",
        control: "text",
        placeholder: "~/Desktop/заметка.txt",
        wide: true,
      },
      { key: "content", label: "Содержимое", control: "mono", optional: true, wide: true },
    ],
    create: () => ({ type: "file", path: "", content: "" }),
  },
  {
    type: "dictate",
    label: "Диктовка",
    hint: "Спросит фразу и напечатает её",
    fields: [
      {
        key: "prompt",
        label: "Вопрос",
        control: "text",
        optional: true,
        placeholder: "Что напечатать?",
        wide: true,
      },
    ],
    create: () => ({ type: "dictate" }),
  },
  {
    type: "play",
    label: "Звук",
    hint: "Файл .wav или .mp3 через плеер Aion",
    fields: [
      {
        key: "path",
        label: "Файл",
        control: "text",
        placeholder: "C:\\Sounds\\chime.wav",
        wide: true,
      },
    ],
    create: () => ({ type: "play", path: "" }),
  },
  {
    type: "say",
    label: "Сказать",
    hint: "Ассистент произнесёт фразу",
    fields: [{ key: "text", label: "Фраза", control: "text", wide: true }],
    create: () => ({ type: "say", text: "" }),
  },
  {
    type: "run",
    label: "Запустить команду",
    hint: "Выполнить другую команду целиком (вложенность до 5)",
    fields: [{ key: "command", label: "Команда", control: "select", options: [] }],
    create: () => ({ type: "run", command: "" }),
  },
];

const BY_TYPE = new Map(ACTIONS.map((a) => [a.type, a]));

export function actionDef(type: string): ActionDef | undefined {
  return BY_TYPE.get(type);
}

export function isDangerous(a: CommandAction): boolean {
  return actionDef(a.type)?.dangerous?.(a) ?? false;
}

export interface FormContext {
  /** Other commands for the «Запустить команду» select: [id, name]. */
  commands: Option[];
  onChange: () => void;
  /** Redraw the fields (a value changed which fields are shown). */
  refresh: () => void;
}

function control(a: CommandAction, f: FieldDef, ctx: FormContext, id: string): HTMLElement {
  const value = a[f.key];
  const set = (v: unknown): void => {
    if (v === "" || v === undefined || v === null) {
      if (f.optional) delete a[f.key];
      else a[f.key] = f.control === "number" ? undefined : "";
    } else a[f.key] = v;
    ctx.onChange();
  };
  switch (f.control) {
    case "select": {
      const options: Option[] = f.options?.length ? [...f.options] : [...ctx.commands];
      const current = String(value ?? "");
      if (a.type === "run" && !options.some(([v]) => v === current)) {
        options.unshift([current, current || "— выберите —"]);
      }
      const el = h("select", { id });
      for (const [v, label] of options)
        el.append(h("option", { value: v, selected: v === current }, label));
      el.addEventListener("change", () => {
        set(el.value);
        ctx.refresh();
      });
      return el;
    }
    case "number": {
      const el = h("input", {
        id,
        type: "number",
        value: value === undefined ? "" : String(value),
        min: f.min,
        max: f.max,
        step: f.step ?? 1,
        placeholder: f.placeholder,
      });
      el.addEventListener("input", () => set(el.value === "" ? undefined : Number(el.value)));
      return el;
    }
    case "mono": {
      const el = h("textarea", {
        id,
        class: "mono-input",
        rows: 3,
        spellcheck: false,
        placeholder: f.placeholder,
      });
      el.value = String(value ?? "");
      el.addEventListener("input", () => set(el.value));
      return el;
    }
    default: {
      const el = h("input", {
        id,
        type: "text",
        value: String(value ?? ""),
        placeholder: f.placeholder,
        autocomplete: "off",
      });
      el.addEventListener("input", () => set(el.value));
      return el;
    }
  }
}

function wrap(
  label: string,
  el: HTMLElement,
  id: string,
  hint?: string,
  wide?: boolean,
): HTMLElement {
  return h(
    "div",
    { class: `field${wide ? " wide" : ""}` },
    h("label", { for: id, class: "field-label" }, label),
    el,
    hint ? h("small", null, hint) : null,
  );
}

/** Extra «mode» switches that are not fields of the stored action. */
function modeSwitch(a: CommandAction, ctx: FormContext, uid: string): HTMLElement | null {
  const id = `${uid}-mode`;
  if (a.type === "keys") {
    const el = h(
      "select",
      { id },
      h("option", { value: "combo", selected: a.text === undefined }, "Сочетание клавиш"),
      h("option", { value: "text", selected: a.text !== undefined }, "Набрать текст"),
    );
    el.addEventListener("change", () => {
      if (el.value === "text") {
        a.text = "";
        delete a.combo;
      } else {
        a.combo = "";
        delete a.text;
      }
      ctx.onChange();
      ctx.refresh();
    });
    return wrap("Что сделать", el, id);
  }
  if (a.type === "volume") {
    const mode =
      a.set !== undefined
        ? "set"
        : a.change !== undefined
          ? "change"
          : a.mute === false
            ? "unmute"
            : "mute";
    const el = h("select", { id });
    for (const [v, label] of [
      ["set", "Установить уровень"],
      ["change", "Прибавить или убавить"],
      ["mute", "Выключить звук"],
      ["unmute", "Включить звук"],
    ] as Option[]) {
      el.append(h("option", { value: v, selected: v === mode }, label));
    }
    el.addEventListener("change", () => {
      delete a.set;
      delete a.change;
      delete a.mute;
      if (el.value === "set") a.set = 50;
      else if (el.value === "change") a.change = -10;
      else a.mute = el.value === "mute";
      ctx.onChange();
      ctx.refresh();
    });
    return wrap("Что сделать", el, id);
  }
  return null;
}

/** The inputs of one action. Edits mutate `a` in place. */
export function renderFields(a: CommandAction, ctx: FormContext, uid: string): HTMLElement {
  const def = actionDef(a.type);
  if (!def) {
    return h("p", { class: "err-text" }, `Неизвестный тип действия «${a.type}»`);
  }
  const fields = def.fields.filter((f) => !f.when || f.when(a));
  return h(
    "div",
    { class: "grid action-fields" },
    modeSwitch(a, ctx, uid),
    ...fields.map((f) => {
      const id = `${uid}-${f.key}`;
      return wrap(f.label, control(a, f, ctx, id), id, f.hint, f.wide);
    }),
  );
}
