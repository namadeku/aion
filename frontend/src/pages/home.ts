import { avatarKeyOf, createAvatar } from "../avatar";
import type { Avatar } from "../avatar/types";
import { logoMark } from "../brand";
import { createComposer } from "../components/composer";
import { sharedWorkLog } from "../components/worklog-shared";
import { h, iconButton, icons, svgIcon } from "../dom";
import { native } from "../native";
import { getClassicHud, onPrefsChange } from "../prefs";
import { store } from "../store";
import type { AvatarConfig, HistoryEntry, Mood } from "../types";

/** Replies that greet or say goodbye make the avatar wave. */
const WAVE_WORDS =
  /(^|[^а-яё])(привет|здравствуй|доброе утро|добрый (день|вечер)|доброй ночи|пока|до встречи|до свидания|спокойной ночи)/i;

/** Mood meters in the avatar panel: [field, label]. */
const MOOD_METERS: [keyof Mood, string][] = [
  ["energy", "энергия"],
  ["affection", "симпатия"],
  ["boredom", "скука"],
];

const STATE_LABEL = {
  idle: "ожидание",
  listening: "слушаю",
  thinking: "думаю",
  speaking: "говорю",
} as const;

/** What the window's avatar panel shows: the orb stands in for the HUD ring by default. */
export function panelAvatar(config: AvatarConfig): AvatarConfig {
  return config.kind === "hud" && !getClassicHud() ? { kind: "orb", path: null } : config;
}

const time = (ts: number): string =>
  new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

function message(m: HistoryEntry): HTMLElement {
  const meta = h(
    "span",
    { class: "meta" },
    time(m.ts),
    m.role === "user" && m.source === "voice" ? h("span", null, "голосом") : null,
  );
  if (m.role === "user") {
    return h(
      "div",
      { class: "msg user" },
      h("div", { class: "msg-body" }, h("div", { class: "bubble" }, m.text), meta),
      h("span", { class: "msg-avatar", "aria-hidden": "true" }, svgIcon(icons.user)),
    );
  }
  return h(
    "div",
    { class: "msg assistant" },
    h("span", { class: "msg-avatar", "aria-hidden": "true" }, logoMark(18)),
    h("div", { class: "msg-body" }, h("div", { class: "bubble" }, m.text), meta),
  );
}

export function homePage(root: HTMLElement): () => void {
  // -- chat column ------------------------------------------------------------------------
  const thread = h("div", { class: "thread-inner" });
  const scroller = h(
    "div",
    { class: "thread", role: "log", "aria-live": "polite", "aria-label": "Диалог" },
    thread,
  );
  const worklog = sharedWorkLog();
  const composer = createComposer();

  // -- avatar panel -----------------------------------------------------------------------
  const stage = h("div", { class: "orb-stage" });
  const stateName = h("span", { class: "orb-name" });
  const stateLabel = h("span", { class: "orb-state-label" });
  const meters = MOOD_METERS.map(([key, label]) => {
    const fill = h("i");
    const row = h(
      "div",
      { class: "mood-row", "data-key": key },
      h("span", null, label),
      h("b", null, fill),
    );
    return { key, fill, row, label };
  });
  const mood = h(
    "div",
    { class: "mood", role: "group", "aria-label": "Настроение" },
    ...meters.map((m) => m.row),
  );
  const desktopBtn = iconButton(
    "Выпустить персонажа на рабочий стол",
    icons.desktop,
    () => store.send({ type: "desktop_mode", value: true }),
    "icon-btn orb-action",
  );
  const away = h(
    "div",
    { class: "orb-away", hidden: true },
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
  const panel = h(
    "aside",
    { class: "orb-panel", "aria-label": "Аватар ассистента" },
    stage,
    away,
    h(
      "div",
      { class: "orb-foot" },
      h(
        "div",
        { class: "orb-state", role: "status" },
        h("i", { class: "orb-dot", "aria-hidden": "true" }),
        stateName,
        stateLabel,
      ),
      mood,
      desktopBtn,
    ),
  );

  root.append(
    h(
      "div",
      { class: "home" },
      h(
        "section",
        { class: "chat-col", "aria-label": "Чат" },
        scroller,
        h("div", { class: "dock" }, worklog.el, composer.el),
      ),
      panel,
    ),
  );

  // -- avatar -----------------------------------------------------------------------------
  let avatar: Avatar | null = null;
  let canvas: HTMLCanvasElement | null = null;
  let avatarKey = "";
  let disposed = false;

  async function mountAvatar(): Promise<void> {
    const config = panelAvatar(store.avatar);
    const key = avatarKeyOf(config);
    if (key === avatarKey) return;
    avatarKey = key;
    avatar?.dispose();
    avatar = null;
    canvas?.remove();
    // a fresh canvas per avatar: a canvas cannot switch between 2D and WebGL contexts
    canvas = h("canvas", { class: "view", "aria-hidden": "true" });
    stage.append(canvas);
    panel.dataset.look = config.kind;
    const created = await createAvatar(canvas, config);
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
    // nothing to draw: the character is on the desktop, the window is minimized or hidden,
    // or the panel is collapsed on a narrow window
    if (store.desktop || native.hidden || !stage.offsetWidth) return;
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
  // follow new messages while the user is at the bottom (also when the work log opens)
  let stick = true;
  scroller.addEventListener("scroll", () => {
    stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60;
  });
  const keepBottom = new ResizeObserver(() => {
    if (stick) scroller.scrollTop = scroller.scrollHeight;
  });
  keepBottom.observe(scroller);
  keepBottom.observe(thread);
  let rendered = -1;
  let lastText = "";
  function renderMessages(history: HistoryEntry[]): void {
    const tail = history.at(-1);
    if (history.length === rendered && tail?.text === lastText) return;
    thread.replaceChildren(
      ...(history.length
        ? history.map(message)
        : [
            h(
              "div",
              { class: "thread-empty" },
              h("p", { class: "thread-empty-title" }, `Скажите «${store.name}, который час?»`),
              h(
                "p",
                null,
                "или напишите сообщение внизу. Ответ прозвучит голосом и появится здесь.",
              ),
            ),
          ]),
    );
    if (stick || rendered <= 0) scroller.scrollTop = scroller.scrollHeight;
    rendered = history.length;
    lastText = tail?.text ?? "";
  }

  function sync(): void {
    panel.dataset.state = store.state;
    stateName.textContent = store.name;
    stateLabel.textContent = STATE_LABEL[store.state];
    for (const m of meters) {
      const value = Math.round(store.mood[m.key] * 100);
      m.fill.style.width = `${value}%`;
      m.row.title = `${m.label}: ${value}%`;
    }
    desktopBtn.hidden = !store.desktopAvailable || store.desktop;
    away.hidden = !store.desktop;
    renderMessages(store.history);
    void mountAvatar();
  }

  const offs = [
    store.subscribe(sync),
    onPrefsChange(() => void mountAvatar()),
    store.on("wake_detected", () => avatar?.react?.("wake")),
    store.on("assistant_reply", (e) => {
      if (typeof e.text === "string" && WAVE_WORDS.test(e.text)) avatar?.react?.("wave");
    }),
  ];
  sync();
  composer.focus();

  return () => {
    disposed = true;
    keepBottom.disconnect();
    offs.forEach((off) => off());
    composer.dispose();
    cancelAnimationFrame(frame);
    avatar?.dispose();
  };
}
