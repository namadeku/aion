import "@fontsource/exo-2/latin-500.css";
import "@fontsource/exo-2/cyrillic-500.css";
import "@fontsource/exo-2/latin-600.css";
import "@fontsource/exo-2/cyrillic-600.css";
import "@fontsource/exo-2/latin-700.css";
import "@fontsource/exo-2/cyrillic-700.css";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/cyrillic-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/cyrillic-500.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/cyrillic-400.css";
import "./style.css";

import { debugMode, desktopMode, token } from "./api";
import { h, icons, svgIcon, toast } from "./dom";
import { store } from "./store";
import { homePage } from "./pages/home";
import { pluginsPage } from "./pages/plugins";
import { voicePage } from "./pages/voice";
import { audioPage } from "./pages/audio";
import { llmPage } from "./pages/llm";
import { logsPage } from "./pages/logs";
import { aboutPage } from "./pages/about";

/** A page renders into a container and returns a cleanup function. */
export type Page = (root: HTMLElement) => (() => void) | void;

interface Route {
  path: string;
  title: string;
  icon: string;
  page: Page;
  /** Only in the dev edition (the public build has no log viewer). */
  devOnly?: boolean;
}

const routes: Route[] = [
  { path: "", title: "Главная", icon: icons.home, page: homePage },
  { path: "plugins", title: "Плагины", icon: icons.plugins, page: pluginsPage },
  { path: "voice", title: "Голос и персонаж", icon: icons.voice, page: voicePage },
  { path: "audio", title: "Микрофон", icon: icons.mic, page: audioPage },
  { path: "llm", title: "LLM", icon: icons.brain, page: llmPage },
  { path: "logs", title: "Логи", icon: icons.logs, page: logsPage, devOnly: true },
  { path: "about", title: "О программе", icon: icons.info, page: aboutPage },
];

const visibleRoutes = (): Route[] => routes.filter((r) => !r.devOnly || store.edition === "dev");

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
  const nav = h("nav", { class: "nav" });
  const footer = h("div", { class: "rail-footer" });
  const main = h("main", { class: "main" });
  app.append(
    h(
      "div",
      { class: "shell" },
      h(
        "aside",
        { class: "rail" },
        h("div", { class: "brand" }, "Aion", h("small", null, "voice assistant")),
        nav,
        footer,
      ),
      main,
    ),
  );

  let cleanup: (() => void) | void;
  let renderedEdition = store.edition;

  function render(): void {
    const path = location.hash.replace(/^#\/?/, "");
    const shown = visibleRoutes();
    const route = shown.find((r) => r.path === path) ?? shown[0];
    renderedEdition = store.edition;
    nav.replaceChildren(
      ...shown.map((r) =>
        h(
          "a",
          { href: `#/${r.path}`, class: r === route ? "active" : "", title: r.title },
          svgIcon(r.icon),
          h("span", null, r.title),
        ),
      ),
    );
    cleanup?.();
    main.replaceChildren();
    main.scrollTop = 0;
    cleanup = route.page(main);
    document.title = route.path ? `${route.title} — ${store.name}` : store.name;
  }

  function renderFooter(): void {
    const connection = h(
      "div",
      { style: store.connected ? "" : "color:var(--amber)" },
      store.connected ? "● соединение есть" : "○ нет соединения…",
    );
    const update = store.update
      ? h("a", { href: "#/about", class: "update-link" }, `↑ обновление ${store.update}`)
      : null;
    footer.replaceChildren(connection, ...(update ? [update] : []));
  }

  const startup = h("div", { class: "startup", hidden: true });
  app.append(startup);
  let dismissedError: string | null = null;

  /** First start: the voice models are downloading, or the voice failed to start. */
  function renderStartup(): void {
    const s = store.startup;
    const failed = s.stage === "failed" && s.error !== dismissedError;
    startup.hidden = s.stage !== "loading" && !failed;
    if (startup.hidden) return;
    startup.className = failed ? "startup failed" : "startup";
    if (failed) {
      const close = (): void => {
        dismissedError = s.error;
        renderStartup();
      };
      startup.replaceChildren(
        h("button", { class: "startup-close", title: "Скрыть", onClick: close }, "×"),
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
    startup.replaceChildren(
      h("b", null, "Подготовка голоса"),
      h(
        "p",
        null,
        s.downloads.length
          ? "Первый запуск: скачиваю модели, это займёт несколько минут."
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
  store.subscribe(renderFooter);
  store.subscribe(renderStartup);
  // the first snapshot tells the edition: show or hide dev-only pages
  store.subscribe(() => {
    if (store.edition !== renderedEdition) render();
  });
  store.on("notification", (e) => toast(String(e.title), String(e.text ?? ""), String(e.level)));
  store.on("plugin_status_changed", (e) => {
    if (e.status === "error") toast(`Плагин ${e.plugin}`, String(e.error ?? "ошибка"), "error");
  });

  if (!token) {
    main.append(
      h(
        "div",
        { class: "page" },
        h(
          "div",
          { class: "banner" },
          "Откройте интерфейс из окна Aion или по ссылке из консоли (с токеном).",
        ),
      ),
    );
  }

  render();
  renderFooter();
  store.connect();
  if (debugMode) (window as unknown as { aion: unknown }).aion = { store };
}
