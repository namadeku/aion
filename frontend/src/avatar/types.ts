import type { Emotion, Mood, State } from "../types";

/** One-off reactions an avatar may play. */
export type Reaction = "wake" | "wave" | "poke" | "pat";

/** Body mode of the desktop mascot, driven by the backend's physics. */
export type BodyMode = "stand" | "walk" | "sit" | "drag" | "fall" | "peek";

export interface DesktopInput {
  mode: BodyMode;
  /** Walking direction: -1 left, 1 right, 0 facing the user; peeking: toward the screen. */
  facing: number;
  /** Horizontal speed of the character in frame heights per second (right is positive). */
  vx: number;
  /** Vertical speed in frame heights per second (down is positive). */
  vy: number;
  /** Cursor relative to the head, roughly -1..1 (x right, y up); null when far away. */
  look: [number, number] | null;
}

/** Where the character is in the frame, in canvas pixels (for the desktop mascot). */
export interface Layout {
  /** Row of the soles when standing. */
  feet: number;
  /** Row of the seat when sitting on an edge. */
  seat: number;
  /** Column of the body centre. */
  center: number;
  /** Row of the top of the head. */
  head: number;
}

/** Something that visualizes the assistant: HUD ring, VRM model or Live2D model. */
export interface Avatar {
  /** Called every frame with smoothed audio levels (0..1). */
  update(input: AvatarInput): void;
  /** One-off reaction, e.g. perking up when the assistant hears its name. */
  react?(event: Reaction): void;
  /** Desktop mascot body mode (desktop mode only). */
  setDesktop?(input: DesktopInput | null): void;
  /** Character position in the frame (desktop mode only). */
  layout?(): Layout | null;
  dispose(): void;
}

export interface AvatarInput {
  state: State;
  emotion: Emotion;
  outputLevel: number;
  inputLevel: number;
  /** Seconds since start, for idle animations. */
  time: number;
  dt: number;
  /** Slow mood: tired characters yawn and doze sooner, bored ones sigh and look around. */
  mood?: Mood;
}

export interface AvatarOptions {
  /** Full-body framing with the feet at the bottom (desktop mascot). */
  fullBody?: boolean;
}
