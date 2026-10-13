// «Анимации» block of the avatar settings: which .vrma clip plays for each action, with a live
// full-body preview of the model.

import { api } from "../api";
import { ANIMATION_SLOTS, PROCEDURAL, planClips, type AnimationSlot } from "../avatar/actions";
import type { Avatar } from "../avatar/types";
import { h, iconButton, icons, toast } from "../dom";
import type { AvatarConfig } from "../types";
import { panel } from "./common";

/** A small stage with the model that plays the chosen action; survives page re-renders. */
export class AnimationPreview {
  readonly stage: HTMLElement;
  private caption: HTMLElement;
  private view: HTMLElement;
  private avatar: Avatar | null = null;
  private config: AvatarConfig | null = null;
  private key = "";
  private mounting: Promise<Avatar | null> | null = null;
  private frame = 0;
  private disposed = false;

  constructor() {
    this.view = h(
      "div",
      { class: "anim-view" },
      h(
        "p",
        { class: "anim-placeholder" },
        "Нажмите «Показать» у действия — модель сыграет его здесь.",
      ),
    );
    this.caption = h("p", { class: "anim-caption", "aria-live": "polite" });
    this.stage = h(
      "figure",
      { class: "anim-stage", "aria-label": "Предпросмотр анимации" },
      this.view,
      this.caption,
    );
  }

  /** The avatar setup to preview; a different one is loaded on the next «Показать». */
  setConfig(config: AvatarConfig): void {
    this.config = config;
    const key = previewKey(config);
    if (key !== this.key && this.avatar) {
      this.unmount();
      void this.mount(); // keep showing the model, now with the new clips
    }
  }

  async play(slot: AnimationSlot, source: string): Promise<void> {
    this.caption.textContent = `${slot.label}: ${source}`;
    const avatar = await this.mount();
    if (avatar && slot.name !== "idle") avatar.play?.(slot.name);
  }

  private async mount(): Promise<Avatar | null> {
    const config = this.config;
    if (!config || this.disposed) return null;
    const key = previewKey(config);
    if (this.avatar && key === this.key) return this.avatar;
    if (this.mounting && key === this.key) return this.mounting;
    this.unmount();
    this.key = key;
    const canvas = h("canvas", { class: "anim-canvas", "aria-hidden": "true" });
    this.view.replaceChildren(canvas);
    this.stage.setAttribute("aria-busy", "true");
    this.mounting = (async () => {
      const { createAvatar } = await import("../avatar");
      const avatar = await createAvatar(canvas, config, { fullBody: true });
      this.stage.removeAttribute("aria-busy");
      if (this.disposed || key !== this.key) {
        avatar.dispose();
        return null;
      }
      this.avatar = avatar;
      this.loop();
      return avatar;
    })();
    return this.mounting;
  }

  private loop(): void {
    const start = performance.now();
    let last = start;
    const tick = (now: number): void => {
      this.frame = requestAnimationFrame(tick);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      this.avatar?.update({
        state: "idle",
        emotion: "neutral",
        outputLevel: 0,
        inputLevel: 0,
        time: (now - start) / 1000,
        dt,
      });
    };
    this.frame = requestAnimationFrame(tick);
  }

  private unmount(): void {
    cancelAnimationFrame(this.frame);
    this.avatar?.dispose();
    this.avatar = null;
    this.mounting = null;
    this.key = "";
  }

  dispose(): void {
    this.disposed = true;
    this.unmount();
  }
}

function previewKey(config: AvatarConfig): string {
  const clips = (config.clips ?? []).join("|");
  return `${config.path ?? ""}:${JSON.stringify(config.animations ?? {})}:${clips}`;
}

/** The clip files that play for a slot with the current files and mapping (none: procedural). */
function clipsFor(slot: string, clips: string[], mapping: Record<string, string>): string[] {
  const plan = planClips(clips, mapping);
  if (slot === "idle") return plan.idle;
  const file = plan.actions.get(slot);
  return file ? [file] : [];
}

function sourceOf(slot: string, clips: string[], mapping: Record<string, string>): string {
  const files = clipsFor(slot, clips, mapping);
  return files.length ? `клип ${files.join(", ")}` : "встроенная анимация";
}

/**
 * The «Анимации» panel: found clips, a clip (or the procedural motion) per action and a
 * preview. `onMap` saves one slot ("" goes back to the naming convention).
 */
export function animationsPanel(
  avatar: AvatarConfig,
  preview: AnimationPreview,
  onMap: (slot: string, file: string) => void,
): HTMLElement {
  if (avatar.kind !== "vrm" || !avatar.path) {
    return panel(
      "Анимации",
      h(
        "p",
        { class: "muted hint" },
        "Клипы движений работают с 3D-моделью VRM. Выберите тип «3D-модель VRM» и файл модели в блоке выше.",
      ),
    );
  }
  const mapping = avatar.animations ?? {};
  const found = h("div", { class: "anim-files" }, h("p", { class: "muted" }, "Ищу клипы…"));
  const rows = h("div", { class: "anim-slots" });
  const body = h(
    "div",
    { class: "anim-layout" },
    h("div", { class: "anim-main" }, found, rows),
    preview.stage,
  );

  const fill = (clips: string[]): void => {
    preview.setConfig({ ...avatar, clips });
    found.replaceChildren(
      clips.length
        ? h(
            "div",
            { class: "row tags", "aria-label": "Найденные клипы" },
            ...clips.map((c) => h("span", { class: "tag" }, c)),
          )
        : h(
            "p",
            { class: "muted hint" },
            "Рядом с моделью нет файлов .vrma — играют встроенные анимации. Положите клипы в папку модели: ",
            h("code", null, "idle*.vrma"),
            " станет фоновым циклом, ",
            h("code", null, "wave.vrma"),
            " заменит взмах рукой, и так для любого действия из списка.",
          ),
    );
    const row = (slot: AnimationSlot): HTMLElement => {
      const auto = clipsFor(slot.name, clips, { ...mapping, [slot.name]: "" });
      const current = mapping[slot.name] ?? "";
      const select = h(
        "select",
        {
          "aria-label": `Анимация: ${slot.label}`,
          disabled: !clips.length,
          onchange: () => onMap(slot.name, select.value),
        },
        h(
          "option",
          { value: "", selected: current === "" },
          `Авто: ${auto.join(", ") || "встроенная"}`,
        ),
        ...clips.map((c) => h("option", { value: c, selected: current === c }, c)),
        h(
          "option",
          { value: PROCEDURAL, selected: current === PROCEDURAL },
          "Встроенная (процедурная)",
        ),
      );
      if (current && current !== PROCEDURAL && !clips.includes(current)) {
        select.append(h("option", { value: current, selected: true }, `${current} (нет файла)`));
      }
      const show = iconButton(`Показать: ${slot.label}`, icons.play, () => {
        void preview
          .play(slot, sourceOf(slot.name, clips, mapping))
          .catch((err: unknown) =>
            toast("Предпросмотр не открылся", err instanceof Error ? err.message : String(err)),
          );
      });
      show.classList.add("anim-play");
      return h(
        "div",
        { class: "anim-row", "data-slot": slot.name },
        h("span", { class: "anim-label" }, slot.label),
        select,
        show,
      );
    };
    rows.replaceChildren(
      ...ANIMATION_SLOTS.filter((s) => !s.sitting).map(row),
      h("h3", { class: "anim-group" }, "Сидя на краю окна"),
      ...ANIMATION_SLOTS.filter((s) => s.sitting).map(row),
    );
  };

  void api.get<{ clips: string[] }>("/api/avatar/clips").then(
    (r) => fill(r.clips),
    () => fill([]),
  );
  return panel(
    "Анимации",
    h(
      "p",
      { class: "muted hint" },
      "Клипы VRM Animation (.vrma) из папки модели заменяют встроенные движения. Выберите клип для действия или оставьте «Авто» — тогда клип подбирается по имени файла.",
    ),
    body,
  );
}
