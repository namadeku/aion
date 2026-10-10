import { createAvatar } from "../avatar";
import type { Avatar } from "../avatar/types";
import { h, icons, svgIcon } from "../dom";
import { store } from "../store";
import type { HistoryEntry, Mood } from "../types";

/** Replies that greet or say goodbye make the avatar wave. */
const WAVE_WORDS =
  /(^|[^а-яё])(привет|здравствуй|доброе утро|добрый (день|вечер)|доброй ночи|пока|до встречи|до свидания|спокойной ночи)/i;

/** Mood meters under the name: [field, label, colour]. */
const MOOD_METERS: [keyof Mood, string, string][] = [
  ["energy", "энергия", "var(--green)"],
  ["affection", "симпатия", "var(--accent)"],
  ["boredom", "скука", "var(--amber)"],
];

const STATE_LABEL = {
  idle: "ожидание",
  listening: "слушаю",
  thinking: "думаю",
  speaking: "говорю",
} as const;

export function homePage(root: HTMLElement): () => void {
  // a fresh canvas per avatar: a canvas cannot switch between 2D and WebGL contexts
  const stage = h("section", { class: "stage" });
  const name = h("div", { class: "assistant-name" });
  const chip = h("div", { class: "state-chip" }, h("i"), h("b"));
  const meters = MOOD_METERS.map(([key, label, color]) => {
    const fill = h("i", { style: `--meter:${color}` });
    const row = h("div", { class: "mood-row" }, h("span", null, label), h("b", null, fill));
    return { key, fill, row };
  });
  const mood = h(
    "div",
    { class: "mood", role: "group", "aria-label": "Настроение" },
    ...meters.map((m) => m.row),
  );
  const messages = h("div", { class: "messages", "aria-live": "polite" });
  const input = h("input", {
    type: "text",
    placeholder: "Напишите или скажите имя ассистента…",
    "aria-label": "Сообщение",
  });
  const muteBtn = h("button", { class: "round", type: "button", title: "Выключить микрофон" });
  const talkBtn = h(
    "button",
    { class: "round", type: "button", title: "Нажми и говори" },
    svgIcon(icons.mic),
  );
  const desktopBtn = h(
    "button",
    { class: "round", type: "button", title: "Выпустить персонажа на рабочий стол" },
    svgIcon(icons.desktop),
  );
  desktopBtn.addEventListener("click", () => store.send({ type: "desktop_mode", value: true }));
  const stopBtn = h(
    "button",
    { class: "round", type: "button", title: "Прервать" },
    svgIcon(icons.stop),
  );

  const send = (): void => {
    const text = input.value.trim();
    if (!text) return;
    store.send({ type: "text", text });
    input.value = "";
  };
  input.addEventListener("keydown", (e) => e.key === "Enter" && send());
  talkBtn.addEventListener("click", () => store.send({ type: "push_to_talk" }));
  stopBtn.addEventListener("click", () => store.send({ type: "interrupt" }));
  muteBtn.addEventListener("click", () => store.send({ type: "mute", value: !store.muted }));

  const away = h(
    "div",
    { class: "stage-away", hidden: true },
    h("p", null, "Персонаж гуляет по рабочему столу"),
    h(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: () => store.send({ type: "desktop_mode", value: false }),
      },
      "Вернуть в окно",
    ),
  );
  stage.append(
    away,
    h("div", { class: "stage-top" }, name, chip, mood),
    h(
      "div",
      { class: "stage-bottom" },
      h(
        "div",
        { class: "composer" },
        input,
        h(
          "button",
          { class: "round", type: "button", title: "Отправить", onclick: send },
          svgIcon(icons.send),
        ),
      ),
      talkBtn,
      stopBtn,
      muteBtn,
      desktopBtn,
    ),
  );
  root.append(
    h(
      "div",
      { class: "home" },
      stage,
      h("aside", { class: "chat" }, h("header", null, "Диалог"), messages),
    ),
  );

  // -- avatar -----------------------------------------------------------------------------
  let avatar: Avatar | null = null;
  let canvas: HTMLCanvasElement | null = null;
  let avatarKey = "";
  let disposed = false;

  async function mountAvatar(): Promise<void> {
    const key = `${store.avatar.kind}:${store.avatar.path ?? ""}`;
    if (key === avatarKey) return;
    avatarKey = key;
    avatar?.dispose();
    canvas?.remove();
    canvas = h("canvas", { class: "view", "aria-hidden": "true" });
    stage.prepend(canvas);
    const created = await createAvatar(canvas, store.avatar);
    if (disposed || key !== avatarKey) created.dispose();
    else avatar = created;
  }

  let last = performance.now();
  const start = last;
  let frame = 0;
  const loop = (now: number): void => {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    frame = requestAnimationFrame(loop);
    // the character is out on the desktop: this hidden window does not need to draw it too
    if (store.desktop || document.hidden) return;
    avatar?.update({
      state: store.state,
      emotion: store.emotion,
      outputLevel: store.outputLevel,
      inputLevel: store.inputLevel,
      time: (now - start) / 1000,
      dt,
      mood: store.mood,
    });
  };
  frame = requestAnimationFrame(loop);

  // -- reactive parts ---------------------------------------------------------------------
  let rendered = 0;
  let lastText = "";
  function renderMessages(history: HistoryEntry[]): void {
    const tail = history.at(-1);
    if (history.length === rendered && tail?.text === lastText) return;
    const atBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40;
    messages.replaceChildren(
      ...(history.length
        ? history.map((m) =>
            h(
              "div",
              { class: `msg ${m.role}` },
              m.text,
              h(
                "span",
                { class: "meta" },
                new Date(m.ts * 1000).toLocaleTimeString("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                }),
                m.role === "user" && m.source === "voice" ? " · голос" : "",
              ),
            ),
          )
        : [
            h(
              "div",
              { class: "empty" },
              `Скажите «${store.name}, который час?» или напишите сообщение.`,
            ),
          ]),
    );
    if (atBottom || rendered === 0) messages.scrollTop = messages.scrollHeight;
    rendered = history.length;
    lastText = tail?.text ?? "";
  }

  function sync(): void {
    name.textContent = store.name;
    stage.dataset.state = store.state;
    chip.dataset.state = store.state;
    chip.querySelector("b")!.textContent = STATE_LABEL[store.state];
    for (const m of meters) {
      const value = store.mood[m.key];
      m.fill.style.width = `${Math.round(value * 100)}%`;
      m.row.title = `${m.row.firstChild?.textContent}: ${Math.round(value * 100)}%`;
    }
    muteBtn.replaceChildren(svgIcon(store.muted ? icons.micOff : icons.mic));
    muteBtn.className = `round${store.muted ? " off" : ""}`;
    muteBtn.title = store.muted ? "Включить микрофон" : "Выключить микрофон";
    muteBtn.hidden = !store.voiceEnabled;
    talkBtn.hidden = !store.voiceEnabled || store.muted;
    talkBtn.className = `round${store.state === "listening" ? " on" : ""}`;
    desktopBtn.hidden = !store.desktopAvailable || store.desktop;
    away.hidden = !store.desktop;
    renderMessages(store.history);
    void mountAvatar();
  }

  const unsubscribe = store.subscribe(sync);
  const unsubscribeWake = store.on("wake_detected", () => avatar?.react?.("wake"));
  const unsubscribeReply = store.on("assistant_reply", (e) => {
    if (typeof e.text === "string" && WAVE_WORDS.test(e.text)) avatar?.react?.("wave");
  });
  sync();
  input.focus();

  return () => {
    disposed = true;
    unsubscribe();
    unsubscribeWake();
    unsubscribeReply();
    cancelAnimationFrame(frame);
    avatar?.dispose();
  };
}
