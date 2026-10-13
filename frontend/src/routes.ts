// Page registry. The rail shows one icon per section; a section with several pages gets a
// sub-navigation (tabs) above them. To add a page: registerPage({...}) in pages/index.ts.

import { icons } from "./dom";
import { store } from "./store";

/** A page renders into a container and returns a cleanup function. */
export type Page = (root: HTMLElement) => (() => void) | void;

export type SectionId = "home" | "commands" | "console" | "plugins" | "settings" | "history";

export interface Section {
  id: SectionId;
  title: string;
  icon: string;
}

export interface PageRoute {
  /** Hash path without "#/", e.g. "settings/llm". "" is the home page. */
  path: string;
  section: SectionId;
  /** Title in the sub-navigation and the document title. */
  title: string;
  page: Page;
  /** Only in the dev edition (the public build has no log viewer). */
  devOnly?: boolean;
  /** Old paths that still lead here (bookmarks, tests, links in docs). */
  aliases?: string[];
  /** The page draws its own full-bleed layout (no padded .page column). */
  fullBleed?: boolean;
}

export const sections: Section[] = [
  { id: "home", title: "Главная", icon: icons.home },
  { id: "commands", title: "Команды", icon: icons.bolt },
  { id: "console", title: "Консоль", icon: icons.console },
  { id: "plugins", title: "Плагины", icon: icons.plugins },
  { id: "settings", title: "Настройки", icon: icons.settings },
  { id: "history", title: "История", icon: icons.history },
];

const pages: PageRoute[] = [];

export function registerPage(route: PageRoute): void {
  const existing = pages.findIndex((p) => p.path === route.path);
  if (existing >= 0) pages.splice(existing, 1, route);
  else pages.push(route);
}

export function visiblePages(): PageRoute[] {
  return pages.filter((p) => !p.devOnly || store.edition === "dev");
}

export function sectionPages(section: SectionId): PageRoute[] {
  return visiblePages().filter((p) => p.section === section);
}

/** The page for a hash path (aliases included); falls back to home. */
export function resolve(path: string): PageRoute {
  const shown = visiblePages();
  return (
    shown.find((p) => p.path === path) ??
    shown.find((p) => p.aliases?.includes(path)) ??
    // "#/settings" opens the first settings page
    shown.find((p) => p.section === path) ??
    shown.find((p) => p.path === "") ??
    shown[0]
  );
}

export function currentPath(): string {
  return location.hash.replace(/^#\/?/, "");
}
