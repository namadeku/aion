// UI-only preferences kept in the browser (theme, input mode, the look of the avatar panel).
// The backend config has no field for them; storage may be unavailable, so every access is
// guarded and falls back to the default.

export type Theme = "dark" | "light" | "neon";

/** The order the title-bar button cycles through. */
export const THEMES: readonly Theme[] = ["dark", "light", "neon"];
/** How the user talks to the assistant: voice and text, voice only, or text only (mic muted). */
export type InputMode = "hybrid" | "voice" | "text";

const PREFIX = "aion.";

function read(key: string): string | null {
  try {
    return localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(PREFIX + key, value);
  } catch {
    /* private mode / disabled storage: keep the value for this session only */
  }
}

type Listener = () => void;
const listeners = new Set<Listener>();

/** Re-render hook for anything that depends on preferences. */
export function onPrefsChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function changed(): void {
  for (const fn of listeners) fn();
}

// -- theme --------------------------------------------------------------------------------

export function getTheme(): Theme {
  const saved = read("theme");
  return THEMES.find((t) => t === saved) ?? "dark";
}

export function applyTheme(theme: Theme = getTheme()): void {
  document.documentElement.dataset.theme = theme;
}

export function setTheme(theme: Theme): void {
  write("theme", theme);
  applyTheme(theme);
  changed();
}

/** The theme after `theme` in the cycle dark → light → neon → dark. */
export function nextTheme(theme: Theme = getTheme()): Theme {
  return THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
}

export function toggleTheme(): Theme {
  const next = nextTheme();
  setTheme(next);
  return next;
}

// -- input mode ---------------------------------------------------------------------------

/** The last voice-capable mode the user picked ("text" is derived from the mic being muted). */
export function getVoiceMode(): Exclude<InputMode, "text"> {
  return read("voiceMode") === "voice" ? "voice" : "hybrid";
}

export function setVoiceMode(mode: Exclude<InputMode, "text">): void {
  write("voiceMode", mode);
  changed();
}

// -- avatar panel -------------------------------------------------------------------------

/**
 * The window's avatar panel shows the orb when the profile has no character model (kind
 * "hud"); this switches back to the classic HUD ring.
 */
export function getClassicHud(): boolean {
  return read("classicHud") === "1";
}

export function setClassicHud(value: boolean): void {
  write("classicHud", value ? "1" : "0");
  changed();
}
