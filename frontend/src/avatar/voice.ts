// Voice-driven body motion shared by the orb and the HUD ring (the desktop mascot).
//
// Speaking: the body bobs and breathes with the amplitude of the voice and pulses on syllables.
// Listening: an attentive lean-in, growing with the microphone level.
// Thinking: a slow wander; idle: faint drifting so the character never freezes.
// With prefers-reduced-motion only the glow reacts; nothing moves or changes size.

import { follow } from "./smooth";
import type { AvatarInput } from "./types";

export interface VoicePose {
  /** Smoothed loudness 0..1 of what matters now (the voice when speaking, the mic when listening). */
  level: number;
  /** Short pulse 0..1 on sharp rises of the voice (syllables). */
  beat: number;
  /** Vertical offset as a fraction of the frame height (positive is up). */
  lift: number;
  /** Horizontal offset as a fraction of the frame height. */
  sway: number;
  /** Scale around 1. */
  scale: number;
  /** Roll in radians (positive is clockwise on screen). */
  roll: number;
  /** Extra brightness 0..1. */
  glow: number;
}

const ease = (current: number, target: number, rate: number, dt: number): number =>
  current + (target - current) * follow(rate, dt);

export class VoiceMotion {
  private level = 0;
  private lastRaw = 0;
  private beat = 0;
  private speaking = 0;
  private listening = 0;
  private thinking = 0;

  /** Advance by one frame. `reduced` mirrors prefers-reduced-motion. */
  update(input: AvatarInput, reduced: boolean): VoicePose {
    const { state, outputLevel, inputLevel, time, dt } = input;
    const raw = state === "speaking" ? outputLevel : state === "listening" ? inputLevel : 0;
    // fast attack, slower release: the body follows the loudness without flicker
    this.level = ease(this.level, raw, raw > this.level ? 18 : 5, dt);
    const rise = raw - this.lastRaw;
    this.lastRaw = raw;
    if (state === "speaking" && rise > 0.06) this.beat = Math.min(1, this.beat + rise * 4);
    this.beat *= Math.exp(-dt * 9);

    this.speaking = ease(this.speaking, state === "speaking" ? 1 : 0, 8, dt);
    this.listening = ease(this.listening, state === "listening" ? 1 : 0, 5, dt);
    this.thinking = ease(this.thinking, state === "thinking" ? 1 : 0, 3, dt);

    const level = this.level;
    const glow = Math.min(
      1,
      this.speaking * (0.25 + level * 0.75) +
        this.listening * (0.35 + level * 0.65) +
        this.thinking * 0.3 +
        this.beat * 0.3,
    );
    if (reduced) return { level, beat: 0, lift: 0, sway: 0, scale: 1, roll: 0, glow };

    const t = time;
    // idle drift from incommensurate sines: it never repeats noticeably
    const driftLift = Math.sin(t * 1.1) * 0.004 + Math.sin(t * 0.47 + 1.3) * 0.003;
    const driftRoll = Math.sin(t * 0.37) * 0.02 + Math.sin(t * 0.23 + 2.1) * 0.015;
    const thinkingWander = Math.sin(t * 0.9) * this.thinking;
    return {
      level,
      beat: this.beat,
      lift:
        driftLift +
        this.speaking * (level * 0.02 + this.beat * 0.012) +
        this.listening * (0.006 + level * 0.014) +
        this.thinking * Math.sin(t * 1.6) * 0.006,
      sway: Math.sin(t * 0.5) * 0.004 + thinkingWander * 0.012,
      scale:
        1 +
        this.speaking * (level * 0.08 + this.beat * 0.03) +
        this.listening * (0.025 + level * 0.06) -
        this.thinking * 0.03,
      roll:
        driftRoll +
        thinkingWander * 0.07 -
        this.listening * 0.04 +
        this.speaking * Math.sin(t * 2.3) * level * 0.03,
      glow,
    };
  }
}
