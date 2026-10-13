// Names of the avatar's animation slots and how .vrma clips are matched to them.
// Kept free of three.js: the settings page imports it without loading the 3D libraries.

/** Mapping value that forces the built-in procedural motion even if a clip exists. */
export const PROCEDURAL = "procedural";

export interface AnimationSlot {
  name: string;
  label: string;
  /** Only played while the desktop character sits on a window edge. */
  sitting?: boolean;
}

/** Every slot a clip can replace: the idle loop and the one-off actions (see motion.ts). */
export const ANIMATION_SLOTS: AnimationSlot[] = [
  { name: "idle", label: "Покой (фоновый цикл)" },
  { name: "wave", label: "Машет рукой" },
  { name: "nod", label: "Кивает" },
  { name: "hop", label: "Подпрыгивает" },
  { name: "clap", label: "Хлопает" },
  { name: "stretch", label: "Потягивается" },
  { name: "yawn", label: "Зевает" },
  { name: "sigh", label: "Вздыхает" },
  { name: "hairTouch", label: "Поправляет волосы" },
  { name: "lookAround", label: "Оглядывается" },
  { name: "curious", label: "Прислушивается" },
  { name: "startle", label: "Вздрагивает" },
  { name: "rock", label: "Покачивается" },
  { name: "hum", label: "Напевает" },
  { name: "poke", label: "Реакция на тычок" },
  { name: "pat", label: "Когда гладят" },
  { name: "kick", label: "Болтает ногами", sitting: true },
  { name: "leanBack", label: "Откидывается назад", sitting: true },
  { name: "lookDown", label: "Смотрит вниз", sitting: true },
];

export interface ClipPlan {
  /** Idle loops, taking turns. */
  idle: string[];
  /** Action name → clip file. */
  actions: Map<string, string>;
}

const stem = (file: string): string => file.replace(/\.vrma$/i, "");

/**
 * Which clip plays for what: by default `idle*.vrma` loop and `<action>.vrma` replaces the
 * action; the mapping from the settings overrides single slots (a file name, or
 * "procedural" for the built-in motion). Files that are not there are ignored.
 */
export function planClips(
  files: readonly string[],
  mapping: Record<string, string> = {},
): ClipPlan {
  const present = new Set(files);
  const idle = files.filter((f) => stem(f).toLowerCase().startsWith("idle"));
  const actions = new Map<string, string>();
  for (const file of files) if (!idle.includes(file)) actions.set(stem(file), file);
  let idlePlan = idle;
  for (const [slot, file] of Object.entries(mapping)) {
    if (slot === "idle") {
      if (file === PROCEDURAL) idlePlan = [];
      else if (present.has(file)) idlePlan = [file];
    } else if (file === PROCEDURAL) {
      actions.delete(slot);
    } else if (present.has(file)) {
      actions.set(slot, file);
    }
  }
  return { idle: idlePlan, actions };
}
