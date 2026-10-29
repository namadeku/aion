import "@fontsource/exo-2/latin-500.css";
import "@fontsource/exo-2/cyrillic-500.css";
import "@fontsource/exo-2/latin-600.css";
import "@fontsource/exo-2/cyrillic-600.css";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/cyrillic-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/cyrillic-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/cyrillic-600.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/cyrillic-400.css";
// neon theme only: @font-face files download when the theme first uses them
import "@fontsource/tektur/latin-500.css";
import "@fontsource/tektur/cyrillic-500.css";
import "@fontsource/tektur/latin-600.css";
import "@fontsource/tektur/cyrillic-600.css";
import "@fontsource/martian-mono/latin-400.css";
import "@fontsource/martian-mono/cyrillic-400.css";
import "./tokens.css";
import "./style.css";
import "./pages.css";
import "./theme-neon.css";

import { debugMode, desktopMode, token } from "./api";
import { logoMark } from "./brand";
import { sharedWorkLog } from "./components/worklog-shared";
import { h, iconButton, icons, svgIcon, toast } from "./dom";
import { native, type ResizeEdge } from "./native";
import "./pages/index";
import { applyTheme, nextTheme, toggleTheme, type Theme } from "./prefs";
import { currentPath, resolve, sectionPages, sections, type PageRoute } from "./routes";
import { store } from "./store";

applyTheme();

const THEME_LABELS: Record<Theme, string> = {
  dark: "Тёмная тема",
  light: "Светлая тема",
  neon: "Неоновая тема",
};
const THEME_ICONS: Record<Theme, string> = { dark: icons.moon, light: icons.sun, neon: icons.chip };

const app = document.querySelector<HTMLDivElement>("#app")!;

if (desktopMode) {
  void import("./pages/desktop").then(({ desktopApp }) => {
    desktopApp(app);
    store.connect();
  });
} else {
  startApp();
}

function startApp(): void {
  // -- rail -------------------------------------------------------------------------------
  const nav = h("nav", { class: "rail-nav", "aria-label": "Разделы" });
  const profileInitial = h("span", { class: "profile-initial", "aria-hidden": "true" });
  const connection = h("span", { class: "sr-only", role: "status" });
  const profile = h(
    "a",
    { class: "rail-item profile", href: "#/settings/voice" },
    profileInitial,
    h("i", { class: "conn-dot", "aria-hidden": "true" }),
  );
  const rail = h(
    "aside",
    { class: "rail" },
    h("a", { class: "rail-logo", href: "#/", "aria-label": "Aion — главная" }, logoMark(30)),
    nav,
    h("div", { class: "rail-bottom" }, profile, connection),
  );

  // -- title bar --------------------------------------------------------------------------
  const themeBtn = iconButton("Тема", icons.sun, () => {
    toggleTheme();
    renderChrome();
  });
  const windowButtons = h(
    "div",
    { class: "window-buttons" },
    iconButton("Свернуть", icons.minimize, () => native.minimize()),
    iconButton("Развернуть", icons.maximize, () => native.toggleMaximize()),
    iconButton("Закрыть", icons.close, () => native.close(), "icon-btn close"),
  );
  const notice = h("div", { class: "titlebar-notice" });
  const titlebar = h(
    "header",
    { class: "titlebar" },
    h("div", { class: "wordmark", "aria-label": "Aion" }, "AION"),
    notice,
    h("div", { class: "titlebar-controls" }, themeBtn, windowButtons),
  );
  // the frameless window moves by its title bar; a double click maximizes
  const onControl = (e: MouseEvent): boolean =>
    Boolean((e.target as HTMLElement).closest("button, a, input, select, textarea"));
  titlebar.addEventListener("mousedown", (e) => {
    if (!native.available || e.button !== 0 || onControl(e)) return;
    e.preventDefault(); // no text selection while dragging
    native.startDrag();
  });
  titlebar.addEventListener("dblclick", (e) => {
    if (native.available && !onControl(e)) native.toggleMaximize();
  });

  // -- resize edges (frameless window) ----------------------------------------------------
  const EDGES: ResizeEdge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
  const edges = h(
    "div",
    { class: "resize-edges", "aria-hidden": "true" },
    ...EDGES.map((edge) =>
      h("div", {
        class: `edge edge-${edge}`,
        onmousedown: (e: MouseEvent) => {
          if (e.button !== 0) return;
          e.preventDefault();
          native.startResize(edge);
        },
      }),
    ),
  );

  const main = h("main", { class: "main", id: "main" });
  const frame = h("div", { class: "frame" }, titlebar, main);
  const shell = h(
    "div",
    { class: "shell" },
    h("a", { class: "skip-link", href: "#main" }, "К содержимому"),
    rail,
    frame,
    edges,
  );
  app.append(shell);

  // -- routing ----------------------------------------------------------------------------
  let cleanup: (() => void) | void;
  let renderedEdition = store.edition;
  let current: PageRoute | null = null;

  function render(): void {
    const path = currentPath();
    const route = resolve(path);
    renderedEdition = store.edition;
    current = route;
    nav.replaceChildren(
      ...sections
        .filter((s) => sectionPages(s.id).length)
        .map((s) => {
          const target = sectionPages(s.id)[0];
          const active = route.section === s.id;
          return h(
            "a",
            {
              href: `#/${target.path}`,
              class: `rail-item${active ? " active" : ""}`,
              "aria-label": s.title,
              "aria-current": active ? "page" : undefined,
              "data-tip": s.title,
            },
            svgIcon(s.icon),
          );
        }),
    );

    cleanup?.();
    main.replaceChildren();
    main.scrollTop = 0;
    shell.dataset.bleed = String(Boolean(route.fullBleed));
    shell.dataset.section = route.section;
    let container: HTMLElement = main;
    const siblings = sectionPages(route.section);
    if (!route.fullBleed) {
      container = h("div", { class: "page-wrap" });
      if (siblings.length > 1) {
        const section = sections.find((s) => s.id === route.section);
        main.append(
          h(
            "nav",
            { class: "subnav", "aria-label": section?.title ?? "Подразделы" },
            ...siblings.map((p) =>
              h(
                "a",
                {
                  href: `#/${p.path}`,
                  class: p === route ? "active" : "",
                  "aria-current": p === route ? "page" : undefined,
                },
                p.title,
              ),
            ),
          ),
        );
      }
      main.append(container);
    }
    cleanup = route.page(container);
    document.title = route.path ? `${route.title} — ${store.name}` : store.name;
  }

  function renderChrome(): void {
    // the button names and shows the theme it switches to
    const next = nextTheme();
    const label = THEME_LABELS[next];
    themeBtn.replaceChildren(svgIcon(THEME_ICONS[next]));
    themeBtn.setAttribute("aria-label", label);
    themeBtn.title = label;

    windowButtons.hidden = !native.available;
    edges.hidden = !native.available || native.state === "maximized";
    shell.dataset.native = String(native.available);
    const maxBtn = windowButtons.children[1] as HTMLButtonElement;
    const maxLabel = native.state === "maximized" ? "Восстановить" : "Развернуть";
    maxBtn.setAttribute("aria-label", maxLabel);
    maxBtn.title = maxLabel;
  }

  function renderStatus(): void {
    // before the first snapshot the page is still connecting, not disconnected
    const state = store.connected ? "ok" : store.loaded ? "lost" : "connecting";
    connection.textContent =
      state === "ok" ? "соединение есть" : state === "lost" ? "нет соединения…" : "подключение…";
    profile.dataset.conn = state;
    profileInitial.textContent = (store.name || "A").slice(0, 1).toUpperCase();
    const label = `Профиль: ${store.name}. ${connection.textContent}`;
    profile.setAttribute("aria-label", label);
    profile.dataset.tip = `${store.name} · ${connection.textContent}`;

    const items: HTMLElement[] = [];
    if (!token) {
      items.push(
        h(
          "span",
          { class: "notice warn" },
          "Откройте интерфейс из окна Aion или по ссылке из консоли (с токеном).",
        ),
      );
    }
    if (state === "lost")
      items.push(h("span", { class: "notice warn" }, "Нет связи с ядром — переподключаюсь…"));
    if (store.update) {
      items.push(
        h(
          "a",
          { class: "notice accent", href: "#/settings/about" },
          `Доступна версия ${store.update}`,
        ),
      );
    }
    notice.replaceChildren(...items);
    if (current?.path === "") document.title = store.name;
  }

  const startup = h("div", { class: "startup", hidden: true, role: "status" });
  app.append(startup);
  let dismissedError: string | null = null;

  /** First start: models are downloading (voice, language model), or the voice failed. */
  function renderStartup(): void {
    const s = store.startup;
    const failed = s.stage === "failed" && s.error !== dismissedError;
    startup.hidden = s.stage !== "loading" && !failed && !s.downloads.length;
    if (startup.hidden) return;
    startup.className = failed ? "startup failed" : "startup";
    if (failed) {
      const close = (): void => {
        dismissedError = s.error;
        renderStartup();
      };
      startup.replaceChildren(
        iconButton("Скрыть", icons.close, close, "icon-btn startup-close"),
        h("b", null, "Голос не запустился"),
        h("p", null, "Пока можно писать текстом. Причина:"),
        h("code", null, s.error ?? ""),
        h(
          "p",
          null,
          "Проверьте интернет (модели скачиваются с huggingface.co и alphacephei.com) и микрофон, " +
            "затем перезапустите Aion. Подробности — в %LOCALAPPDATA%\\Aion\\logs\\aion.log.",
        ),
      );
      return;
    }
    const downloading = s.downloads.length > 0;
    startup.replaceChildren(
      h("b", null, s.stage === "loading" ? "Подготовка голоса" : "Скачивание моделей"),
      h(
        "p",
        null,
        downloading
          ? "Первый запуск: скачиваю модели, это займёт несколько минут. " +
              "Пока можно писать текстом."
          : "Загружаю модели…",
      ),
      ...s.downloads.map((d) => {
        const mb = (n: number): string => (n / 1e6).toFixed(0);
        const size = d.total
          ? `${mb(d.done)} / ${mb(d.total)} МБ`
          : d.done
            ? `${mb(d.done)} МБ`
            : "";
        const bar = h("div", { class: "startup-bar" }, h("span"));
        const fill = bar.firstElementChild as HTMLElement;
        fill.style.width = d.total ? `${Math.min(100, (100 * d.done) / d.total)}%` : "100%";
        if (!d.total) fill.classList.add("indeterminate");
        return h(
          "div",
          { class: "startup-file" },
          h("div", null, h("span", null, d.label), h("small", null, size)),
          bar,
        );
      }),
    );
  }

  window.addEventListener("hashchange", render);
  store.subscribe(renderStatus);
  store.subscribe(renderStartup);
  native.subscribe(renderChrome);
  // the first snapshot tells the edition: show or hide dev-only pages
  store.subscribe(() => {
    if (store.edition !== renderedEdition) render();
  });
  store.on("notification", (e) => toast(String(e.title), String(e.text ?? ""), String(e.level)));
  store.on("plugin_status_changed", (e) => {
    if (e.status === "error") toast(`Плагин ${e.plugin}`, String(e.error ?? "ошибка"), "error");
  });

  sharedWorkLog(); // listen to runs from the start, whatever page is open
  render();
  renderChrome();
  renderStatus();
  store.connect();
  if (debugMode) (window as unknown as { aion: unknown }).aion = { store };
}
