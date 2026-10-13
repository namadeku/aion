// History: with an empty search box, this session's conversation; with a query, a search over
// the UI journal and the diary of past conversations (GET /api/history/search), newest first,
// grouped by day with the matches highlighted.

import { api } from "../api";
import { h } from "../dom";
import { store } from "../store";
import type { HistoryEntry, HistoryHit } from "../types";
import { failure, pageShell } from "./common";

const day = (ts: number): string =>
  new Date(ts * 1000).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    weekday: "long",
  });
const clock = (ts: number): string =>
  new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

const escapeRegExp = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` with the words of `query` wrapped in <mark>. */
function highlight(text: string, query: string): (Node | string)[] {
  const words = [
    ...new Set(
      query
        .toLowerCase()
        .split(/\s+/)
        .filter((w) => w.length > 1),
    ),
  ];
  if (!words.length) return [text];
  const re = new RegExp(`(${words.map(escapeRegExp).join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 === 1 ? h("mark", null, part) : part));
}

interface Item {
  kind: "message" | "episode";
  role: string;
  text: string;
  ts: number;
  voice: boolean;
}

export function historyPage(root: HTMLElement): () => void {
  const body = pageShell(
    root,
    "История",
    "Сообщения этой сессии и записи дневника из прошлых разговоров. Найти можно по любому слову.",
  );
  const search = h("input", {
    type: "search",
    placeholder: "Поиск по всем разговорам",
    "aria-label": "Поиск по истории",
  });
  const status = h("p", { class: "muted history-status", role: "status" });
  const list = h("div", { class: "history" });
  let session: HistoryEntry[] = [];
  let hits: HistoryHit[] = [];
  let query = "";
  let seq = 0;

  function items(): Item[] {
    if (query) {
      return hits.map((e) => ({
        kind: e.kind,
        role: e.role,
        text: e.text,
        ts: e.ts,
        voice: e.source === "voice",
      }));
    }
    return session.map((e) => ({
      kind: "message",
      role: e.role,
      text: e.text,
      ts: e.ts,
      voice: e.source === "voice",
    }));
  }

  function who(i: Item): string {
    if (i.role === "user") return "Вы";
    if (i.role === "diary") return "Дневник";
    return store.name;
  }

  function render(): void {
    const shown = items();
    if (!shown.length) {
      list.replaceChildren(
        h(
          "div",
          { class: "empty-state history-empty" },
          h("p", { class: "empty-title" }, query ? "Ничего не найдено" : "Пока пусто"),
          h(
            "p",
            { class: "muted" },
            query
              ? `По запросу «${query}» нет ни сообщений, ни записей дневника. Попробуйте другое слово.`
              : "Поговорите с ассистентом на главной — диалог сохранится здесь.",
          ),
        ),
      );
      return;
    }
    const groups = new Map<string, Item[]>();
    // the session list is oldest-first, the search answer newest-first
    const ordered = query ? shown : [...shown].reverse();
    for (const e of ordered) {
      const key = day(e.ts);
      groups.set(key, [...(groups.get(key) ?? []), e]);
    }
    list.replaceChildren(
      ...[...groups].map(([title, group]) =>
        h(
          "section",
          { class: "history-day" },
          h("h2", null, title),
          h(
            "ol",
            null,
            ...group.map((e) =>
              h(
                "li",
                { class: `history-item ${e.role}` },
                h("time", null, clock(e.ts)),
                h(
                  "span",
                  { class: "history-who" },
                  who(e),
                  h(
                    "span",
                    { class: `tag kind-${e.kind}` },
                    e.kind === "episode" ? "эпизод" : "сообщение",
                  ),
                ),
                h("p", null, ...highlight(e.text, query)),
              ),
            ),
          ),
        ),
      ),
    );
  }

  async function loadSession(): Promise<void> {
    try {
      session = await api.get<HistoryEntry[]>("/api/history");
      if (!query) render();
    } catch (err) {
      failure(list, err);
    }
  }

  async function runSearch(): Promise<void> {
    const q = search.value.trim();
    query = q;
    const mine = ++seq;
    if (!q) {
      status.textContent = "";
      render();
      return;
    }
    status.textContent = "Ищу…";
    try {
      const found = await api.get<HistoryHit[]>(
        `/api/history/search?q=${encodeURIComponent(q)}&limit=50`,
      );
      if (mine !== seq) return; // a newer query has been started
      hits = found;
      status.textContent = found.length ? `Найдено: ${found.length}` : "";
      render();
    } catch (err) {
      if (mine !== seq) return;
      status.textContent = "";
      failure(list, err);
    }
  }

  let typing = 0;
  search.addEventListener("input", () => {
    window.clearTimeout(typing);
    typing = window.setTimeout(() => void runSearch(), 300);
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && search.value) {
      e.preventDefault();
      search.value = "";
      void runSearch();
    }
  });
  body.append(
    h("div", { class: "row toolbar" }, h("div", { class: "grow" }, search)),
    status,
    list,
  );
  void loadSession();

  let pending = 0;
  const refresh = (): void => {
    window.clearTimeout(pending);
    pending = window.setTimeout(() => void loadSession(), 400);
  };
  const offs = [store.on("speech_recognized", refresh), store.on("assistant_reply", refresh)];
  return () => {
    window.clearTimeout(typing);
    window.clearTimeout(pending);
    offs.forEach((off) => off());
  };
}
