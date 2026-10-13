// Input bar: multi-line text field, `</>` code mode, input mode (Гибрид/Голос/Текст),
// push-to-talk microphone and the round send / stop button.

import { api } from "../api";
import { h, icons, svgIcon, toast } from "../dom";
import type { InputMode } from "../prefs";
import { store } from "../store";
import type { CommandListing, CommandSummary } from "../types";
import { inputState } from "./input-state";
import { sharedWorkLog } from "./worklog-shared";

const MODES: [InputMode, string][] = [
  ["hybrid", "Гибрид"],
  ["voice", "Голос"],
  ["text", "Текст"],
];

const MODE_HINT: Record<InputMode, string> = {
  hybrid: "Голос и текст: говорите или пишите",
  voice: "Только голос: окно ввода остаётся для команд",
  text: "Только текст: микрофон выключен",
};

export interface Composer {
  el: HTMLElement;
  focus(): void;
  dispose(): void;
}

export function createComposer(): Composer {
  const field = h("textarea", {
    class: "composer-input",
    rows: 1,
    placeholder: "введите текст…",
    "aria-label": "Сообщение",
    spellcheck: true,
    "aria-autocomplete": "list",
  });
  const codeBtn = h(
    "button",
    {
      type: "button",
      class: "icon-btn code-toggle",
      "aria-label": "Режим команды или кода",
      title: "Режим команды или кода",
      "aria-pressed": "false",
      onclick: () => {
        inputState.setCodeMode(!inputState.codeMode);
        field.focus();
      },
    },
    svgIcon(icons.code),
  );
  const modeSelect = h("select", {
    class: "mode-select",
    "aria-label": "Режим ввода",
    onchange: () => inputState.setMode(modeSelect.value as InputMode),
  });
  for (const [value, label] of MODES) modeSelect.append(h("option", { value }, label));
  const modeWrap = h("label", { class: "mode" }, modeSelect, svgIcon(icons.chevronDown));

  const micBtn = h("button", {
    type: "button",
    class: "icon-btn mic-btn",
    onclick: () => store.send({ type: "push_to_talk" }),
  });
  const sendBtn = h("button", { type: "button", class: "send-btn" });

  // -- command palette (code mode): typing filters the user's commands, Enter runs one ----
  const paletteId = `palette-${Math.random().toString(36).slice(2)}`;
  const palette = h("ul", {
    class: "palette",
    id: paletteId,
    role: "listbox",
    "aria-label": "Команды",
    hidden: true,
  });
  let commands: CommandSummary[] = [];
  let shown: CommandSummary[] = [];
  let active = 0;
  /** Closed after a run (until the user types again) or when the field loses the focus. */
  let dismissed = false;
  let loadedFor = false;

  const norm = (t: string): string => t.toLowerCase().replaceAll("ё", "е");

  async function loadCommands(): Promise<void> {
    try {
      const listing = await api.get<CommandListing>("/api/commands");
      commands = listing.commands.filter((c) => !c.error);
    } catch {
      commands = [];
    }
    renderPalette();
  }

  function renderPalette(): void {
    if (!inputState.codeMode || dismissed || document.activeElement !== field) {
      palette.hidden = true;
      field.removeAttribute("aria-activedescendant");
      field.removeAttribute("aria-controls");
      return;
    }
    const q = norm(field.value.trim());
    shown = commands
      .filter((c) => !q || norm([c.name, c.id, ...c.triggers].join(" ")).includes(q))
      .slice(0, 8);
    active = Math.min(active, Math.max(0, shown.length - 1));
    palette.replaceChildren(
      ...(shown.length
        ? shown.map((c, i) =>
            h(
              "li",
              {
                id: `${paletteId}-${i}`,
                role: "option",
                class: `palette-item${i === active ? " active" : ""}${c.enabled ? "" : " off"}`,
                "aria-selected": String(i === active),
                // mousedown: keep the focus in the field
                onmousedown: (e: MouseEvent) => {
                  e.preventDefault();
                  void runCommand(c);
                },
              },
              h("span", { class: "palette-name" }, c.name),
              h(
                "span",
                { class: "palette-phrase" },
                c.enabled ? (c.triggers[0] ? `«${c.triggers[0]}»` : c.id) : "выключена",
              ),
              c.dangerous ? h("span", { class: "tag warn" }, "опасная") : null,
            ),
          )
        : [
            h(
              "li",
              { class: "palette-empty", role: "presentation" },
              commands.length
                ? "Нет такой команды. Ctrl+Enter отправит текст как есть."
                : "Своих команд пока нет: создайте их в разделе «Команды».",
            ),
          ]),
    );
    palette.hidden = false;
    field.setAttribute("aria-controls", paletteId);
    if (shown.length) field.setAttribute("aria-activedescendant", `${paletteId}-${active}`);
    else field.removeAttribute("aria-activedescendant");
  }

  async function runCommand(c: CommandSummary): Promise<void> {
    field.value = "";
    grow();
    active = 0;
    dismissed = true;
    renderPalette();
    try {
      const url = `/api/commands/${c.id.split("/").map(encodeURIComponent).join("/")}/test`;
      await api.post(url);
      sharedWorkLog().setExpanded(true);
    } catch (err) {
      toast("Не запущено", err instanceof Error ? err.message : String(err), "error");
    }
    sync();
  }

  const busy = (): boolean =>
    store.state === "thinking" || store.state === "speaking" || store.runActive;
  const stopMode = (): boolean => busy() && !field.value.trim();

  const send = (): void => {
    const text = field.value.trim();
    if (!text) return;
    store.send({ type: "text", text });
    field.value = "";
    grow();
    sync();
  };

  sendBtn.addEventListener("click", () => {
    if (stopMode()) store.send({ type: "interrupt" });
    else send();
    field.focus();
  });
  field.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (inputState.codeMode) {
      if (e.key === "Escape") {
        e.preventDefault();
        inputState.setCodeMode(false);
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (dismissed) {
          dismissed = false;
          renderPalette();
          return;
        }
        if (!shown.length) return;
        active = (active + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
        renderPalette();
        palette.querySelector(".active")?.scrollIntoView({ block: "nearest" });
        return;
      }
      // Enter runs the highlighted command; Ctrl+Enter sends the text as it is
      if (e.key === "Enter" && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        if (shown[active]) void runCommand(shown[active]);
        return;
      }
    }
    // Enter sends, Shift+Enter (or Enter in code mode with Ctrl) makes a new line
    if (e.key !== "Enter") return;
    if (inputState.codeMode ? e.ctrlKey : !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  field.addEventListener("input", () => {
    grow();
    syncSend();
    active = 0;
    dismissed = false;
    renderPalette();
  });
  field.addEventListener("focus", () => renderPalette());
  field.addEventListener("blur", () => renderPalette());

  function grow(): void {
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 180)}px`;
  }

  function syncSend(): void {
    const stop = stopMode();
    sendBtn.replaceChildren(svgIcon(stop ? icons.stop : icons.send));
    sendBtn.classList.toggle("stop", stop);
    const label = stop ? "Стоп" : "Отправить";
    sendBtn.setAttribute("aria-label", label);
    sendBtn.title = stop ? "Стоп: прервать ответ или команду" : "Отправить (Enter)";
    sendBtn.setAttribute("aria-disabled", String(!stop && !field.value.trim()));
  }

  function sync(): void {
    const mode = inputState.mode;
    modeSelect.value = mode;
    modeSelect.disabled = !store.voiceEnabled;
    modeWrap.title = store.voiceEnabled
      ? MODE_HINT[mode]
      : "Голос не запущен: доступен только текст";

    const code = inputState.codeMode;
    codeBtn.setAttribute("aria-pressed", String(code));
    field.classList.toggle("code", code);
    if (code && !loadedFor) {
      loadedFor = true;
      void loadCommands();
    }
    if (!code) loadedFor = false;
    renderPalette();
    field.placeholder = code
      ? "название команды… (↑↓ выбрать, Enter — запустить, Esc — выйти)"
      : mode === "voice"
        ? `скажите «${store.name}, …» или введите текст…`
        : "введите текст…";

    const canTalk = store.voiceEnabled && !store.muted;
    micBtn.hidden = !store.voiceEnabled;
    micBtn.disabled = !canTalk;
    micBtn.replaceChildren(svgIcon(canTalk ? icons.mic : icons.micOff));
    const micLabel = canTalk ? "Нажми и говори" : "Микрофон выключен (режим «Текст»)";
    micBtn.setAttribute("aria-label", micLabel);
    micBtn.title = micLabel;
    micBtn.classList.toggle("live", store.state === "listening");
    syncSend();
  }

  const el = h(
    "div",
    { class: "composer" },
    palette,
    field,
    h(
      "div",
      { class: "composer-bar" },
      codeBtn,
      h("span", { class: "spacer" }),
      modeWrap,
      h("span", { class: "composer-sep", "aria-hidden": "true" }),
      micBtn,
      sendBtn,
    ),
  );
  // a click on the empty part of the box focuses the field
  el.addEventListener("mousedown", (e) => {
    if (e.target === el) {
      e.preventDefault();
      field.focus();
    }
  });

  const offStore = store.subscribe(sync);
  const offInput = inputState.subscribe(sync);
  sync();

  return {
    el,
    focus: () => field.focus(),
    dispose: () => {
      offStore();
      offInput();
    },
  };
}
