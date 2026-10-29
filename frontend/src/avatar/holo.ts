// «Нейра»: an original 2D holographic AI character drawn procedurally on a canvas.
// A bust (head, neck, shoulders) projected from an emitter ring: translucent fills, glowing
// outlines, scanlines, a faint RGB split, short signal glitches and rising data particles.
//
// Everything is drawn in "character units" (head width is about 1) onto an offscreen layer,
// which is then composed onto the visible canvas with the hologram effects.
// Colours come from the theme tokens (--holo-*) and follow theme switches.
// As the desktop mascot (`fullBody`) the bust stands on its projector base at the bottom of a
// tall frame, reports that line through `layout()` and moves with the voice (see voice.ts).

import type { Emotion, State } from "../types";
import type { Avatar, AvatarInput, AvatarOptions, DesktopInput, Layout, Reaction } from "./types";
import { Spring, approach, clampDt, follow, voiceOpenness, wander } from "./smooth";
import { VoiceMotion, type VoicePose } from "./voice";

type RGB = [number, number, number];

interface Palette {
  primary: RGB;
  secondary: RGB;
  hair: RGB;
  glow: RGB;
}

const FALLBACK: Record<keyof Palette, RGB> = {
  primary: [56, 214, 255],
  secondary: [245, 230, 10],
  hair: [200, 107, 255],
  glow: [56, 214, 255],
};
const RED: RGB = [255, 64, 76];
const TAU = Math.PI * 2;

/** Face parameters per emotion (all eased towards the target). */
interface Face {
  /** Brow height: positive raises. */
  brow: number;
  /** Inner brow end: positive raises (sad), negative lowers (angry). */
  tilt: number;
  /** Eye openness multiplier. */
  open: number;
  /** Eyes become happy closed arcs (0..1). */
  happy: number;
  /** Mouth curve: positive smiles. */
  smile: number;
  /** Small round mouth (surprise), 0..1. */
  round: number;
  /** Tint towards red, 0..1. */
  angry: number;
}

const FACES: Record<Emotion, Face> = {
  neutral: { brow: 0, tilt: 0, open: 1, happy: 0, smile: 0.15, round: 0, angry: 0 },
  joy: { brow: 0.35, tilt: 0.1, open: 1, happy: 1, smile: 1, round: 0, angry: 0 },
  thinking: { brow: 0.3, tilt: 0.45, open: 0.95, happy: 0, smile: 0, round: 0, angry: 0 },
  surprise: { brow: 1, tilt: 0.2, open: 1.3, happy: 0, smile: 0, round: 1, angry: 0 },
  sad: { brow: -0.15, tilt: 1, open: 0.82, happy: 0, smile: -0.8, round: 0, angry: 0 },
  angry: { brow: -0.45, tilt: -1, open: 0.85, happy: 0, smile: -0.55, round: 0, angry: 1 },
};

// Frame geometry (units; origin = head centre, y down).
const BASE_Y = 2.6;
const BODY_BOTTOM = 2.05;
const PIVOT_Y = 0.9;
const EYE_Y = 0.08;
const MOUTH_Y = 0.43;
const PARTICLES = 34;

const smooth01 = (x: number): number => {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
};

function parseColor(raw: string, fallback: RGB, probe: CanvasRenderingContext2D): RGB {
  const value = raw.trim();
  if (!value) return fallback;
  // let the browser normalise any CSS colour syntax to #rrggbb / rgba()
  probe.fillStyle = "#000000";
  probe.fillStyle = value;
  const norm = String(probe.fillStyle);
  if (/^#[0-9a-f]{6}$/i.test(norm)) {
    return [
      parseInt(norm.slice(1, 3), 16),
      parseInt(norm.slice(3, 5), 16),
      parseInt(norm.slice(5, 7), 16),
    ];
  }
  const m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(norm);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : fallback;
}

function mix(a: RGB, b: RGB, k: number): RGB {
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
}

function rgba(c: RGB, a: number): string {
  const al = a < 0 ? 0 : a > 1 ? 1 : a;
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${al.toFixed(3)})`;
}

function polygon(points: [number, number][], close = true): Path2D {
  const p = new Path2D();
  points.forEach(([x, y], i) => (i ? p.lineTo(x, y) : p.moveTo(x, y)));
  if (close) p.closePath();
  return p;
}

function hexagon(cx: number, cy: number, r: number, rot = 0): Path2D {
  const p = new Path2D();
  for (let i = 0; i < 6; i++) {
    const a = rot + (i / 6) * TAU;
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i) p.lineTo(x, y);
    else p.moveTo(x, y);
  }
  p.closePath();
  return p;
}

/** Static shapes that never change: built once. */
interface Shapes {
  face: Path2D;
  neck: Path2D;
  body: Path2D;
  bodyEdge: Path2D;
  wingL: Path2D;
  wingR: Path2D;
  trimL: Path2D;
  trimR: Path2D;
  seamL: Path2D;
  seamR: Path2D;
  traces: Path2D;
  traceNodes: Path2D;
  headband: Path2D;
  mic: Path2D;
  chip: Path2D;
  chipInner: Path2D;
  earHex: Path2D;
  earInner: Path2D;
}

function buildShapes(): Shapes {
  const face = new Path2D();
  face.moveTo(0, -0.62);
  face.bezierCurveTo(0.3, -0.62, 0.47, -0.4, 0.46, -0.1);
  face.bezierCurveTo(0.45, 0.15, 0.36, 0.4, 0.15, 0.56);
  face.quadraticCurveTo(0.05, 0.63, 0, 0.63);
  face.quadraticCurveTo(-0.05, 0.63, -0.15, 0.56);
  face.bezierCurveTo(-0.36, 0.4, -0.45, 0.15, -0.46, -0.1);
  face.bezierCurveTo(-0.47, -0.4, -0.3, -0.62, 0, -0.62);
  face.closePath();

  const neck = polygon([
    [-0.16, 0.4],
    [-0.17, 1.0],
    [0, 1.4],
    [0.17, 1.0],
    [0.16, 0.4],
  ]);

  const bodyPts: [number, number][] = [
    [-0.17, 0.88],
    [-0.62, 1.02],
    [-1.0, 1.3],
    [-1.05, BODY_BOTTOM + 0.05],
    [1.05, BODY_BOTTOM + 0.05],
    [1.0, 1.3],
    [0.62, 1.02],
    [0.17, 0.88],
  ];
  const body = polygon(bodyPts);
  const bodyEdge = polygon(
    [
      [-1.05, BODY_BOTTOM],
      [-1.0, 1.3],
      [-0.62, 1.02],
      [-0.17, 0.88],
    ],
    false,
  );
  bodyEdge.addPath(
    polygon(
      [
        [1.05, BODY_BOTTOM],
        [1.0, 1.3],
        [0.62, 1.02],
        [0.17, 0.88],
      ],
      false,
    ),
  );

  const wing = (s: number): Path2D =>
    polygon([
      [s * 0.14, 0.7],
      [s * 0.37, 0.56],
      [s * 0.53, 0.98],
      [s * 0.43, 1.26],
      [s * 0.13, 1.12],
    ]);
  const trim = (s: number): Path2D =>
    polygon(
      [
        [s * 0.17, 0.78],
        [s * 0.34, 0.68],
        [s * 0.46, 1.0],
        [s * 0.38, 1.2],
        [s * 0.17, 1.1],
      ],
      false,
    );
  const seam = (s: number): Path2D =>
    polygon(
      [
        [s * 0.66, 1.04],
        [s * 0.88, 1.4],
        [s * 0.88, BODY_BOTTOM],
      ],
      false,
    );

  // circuit traces on the chest around the chip
  const traces = new Path2D();
  const nodes = new Path2D();
  for (const s of [-1, 1]) {
    traces.moveTo(s * 0.09, 1.56);
    traces.lineTo(s * 0.34, 1.56);
    traces.lineTo(s * 0.44, 1.46);
    traces.lineTo(s * 0.62, 1.46);
    traces.moveTo(s * 0.2, 1.56);
    traces.lineTo(s * 0.26, 1.7);
    traces.lineTo(s * 0.5, 1.7);
    traces.lineTo(s * 0.56, 1.8);
    traces.moveTo(s * 0.07, 1.62);
    traces.lineTo(s * 0.12, 1.82);
    nodes.addPath(hexagon(s * 0.62, 1.46, 0.016));
    nodes.addPath(hexagon(s * 0.5, 1.7, 0.014));
    nodes.addPath(hexagon(s * 0.56, 1.8, 0.012));
    nodes.addPath(hexagon(s * 0.12, 1.82, 0.012));
  }

  const headband = polygon(
    [
      [-0.5, 0.02],
      [-0.53, -0.3],
      [-0.4, -0.62],
      [-0.14, -0.77],
      [0.2, -0.77],
      [0.44, -0.62],
      [0.56, -0.34],
      [0.58, -0.12],
    ],
    false,
  );
  const mic = new Path2D();
  mic.moveTo(-0.5, 0.14);
  mic.bezierCurveTo(-0.52, 0.34, -0.44, 0.5, -0.27, 0.5);

  return {
    face,
    neck,
    body,
    bodyEdge,
    wingL: wing(-1),
    wingR: wing(1),
    trimL: trim(-1),
    trimR: trim(1),
    seamL: seam(-1),
    seamR: seam(1),
    traces,
    traceNodes: nodes,
    headband,
    mic,
    chip: hexagon(0, 1.5, 0.085, Math.PI / 6),
    chipInner: hexagon(0, 1.5, 0.048, Math.PI / 6),
    earHex: hexagon(-0.52, 0.06, 0.09, Math.PI / 6),
    earInner: hexagon(-0.52, 0.06, 0.052, Math.PI / 6),
  };
}

interface Particle {
  x: number;
  y: number;
  speed: number;
  size: number;
  phase: number;
}

export class HoloAvatar implements Avatar {
  private ctx: CanvasRenderingContext2D;
  private layer: HTMLCanvasElement;
  private lctx: CanvasRenderingContext2D;
  private tint: HTMLCanvasElement;
  private tctx: CanvasRenderingContext2D;
  private scan: CanvasPattern | null = null;
  private resize: ResizeObserver;
  private themeWatch: MutationObserver;
  private reduced = matchMedia("(prefers-reduced-motion: reduce)");
  private fullBody: boolean;
  private voice = new VoiceMotion();
  private shapes = buildShapes();
  private palette: Palette;
  private dpr = 1;
  private scanPeriod = 3;

  // animation state
  private face: Face = { ...FACES.neutral };
  private mouth = 0;
  private gaze = { x: 0, y: 0 };
  private gazeGoal = { x: 0, y: 0 };
  private nextGlance = 1.5;
  private blinkAt = 2.5;
  private blinkT = -1;
  private blinkAgain = false;
  private ringLevel = 0;
  private spin = 0;
  private lean = 0;
  private thinking = 0;
  private listening = 0;
  private sway = 0;
  private swayVel = 0;
  private beatWas = false;
  private emotion: Emotion = "neutral";
  private glitchT = 0;
  private glitchGap = 0;
  private nextGlitch = 6;
  private slices: { y: number; h: number; dx: number }[] = [];
  private flickerT = 0;
  private wideT = 0;
  private happyT = 0;
  private waveT = 0;
  private nod = new Spring(0, 16);
  private flinch = new Spring(0, 18);
  private yaw = new Spring(0, 6);
  private pitch = new Spring(0, 7);
  private tilt = new Spring(0, 8);
  private desktop: DesktopInput | null = null;
  private particles: Particle[] = [];
  private pose: VoicePose = { level: 0, beat: 0, lift: 0, sway: 0, scale: 1, roll: 0, glow: 0 };

  constructor(
    private canvas: HTMLCanvasElement,
    options: AvatarOptions = {},
  ) {
    this.fullBody = options.fullBody ?? false;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D недоступен");
    this.ctx = ctx;
    this.layer = document.createElement("canvas");
    this.tint = document.createElement("canvas");
    const lctx = this.layer.getContext("2d");
    const tctx = this.tint.getContext("2d");
    if (!lctx || !tctx) throw new Error("Canvas 2D недоступен");
    this.lctx = lctx;
    this.tctx = tctx;
    this.palette = this.readPalette();
    for (let i = 0; i < PARTICLES; i++) this.particles.push(this.spawn(true));
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(canvas);
    this.themeWatch = new MutationObserver(() => (this.palette = this.readPalette()));
    this.themeWatch.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    this.fit();
  }

  // -- setup ------------------------------------------------------------------------------

  private readPalette(): Palette {
    const style = getComputedStyle(document.documentElement);
    const probe = this.lctx;
    const read = (name: string, key: keyof Palette): RGB =>
      parseColor(style.getPropertyValue(name), FALLBACK[key], probe);
    return {
      primary: read("--holo-primary", "primary"),
      secondary: read("--holo-secondary", "secondary"),
      hair: read("--holo-hair", "hair"),
      glow: read("--holo-glow", "glow"),
    };
  }

  private fit(): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { width, height } = this.canvas.getBoundingClientRect();
    const pw = Math.max(1, Math.round(width * dpr));
    const ph = Math.max(1, Math.round(height * dpr));
    this.dpr = dpr;
    this.canvas.width = pw;
    this.canvas.height = ph;
    this.layer.width = pw;
    this.layer.height = ph;
    this.tint.width = Math.max(1, pw >> 1);
    this.tint.height = Math.max(1, ph >> 1);
    // scanlines: one dark row per period (applied with destination-out)
    this.scanPeriod = Math.max(2, Math.round(3 * dpr));
    const tile = document.createElement("canvas");
    tile.width = 1;
    tile.height = this.scanPeriod;
    const t = tile.getContext("2d");
    if (t) {
      t.fillStyle = "rgba(0,0,0,0.32)";
      t.fillRect(0, 0, 1, Math.max(1, Math.round(dpr)));
      this.scan = this.ctx.createPattern(tile, "repeat");
    }
  }

  private spawn(anywhere: boolean): Particle {
    return {
      x: (Math.random() - 0.5) * 2.3,
      y: anywhere ? -0.9 + Math.random() * 3.5 : BASE_Y - Math.random() * 0.2,
      speed: 0.14 + Math.random() * 0.3,
      size: 0.01 + Math.random() * 0.014,
      phase: Math.random() * TAU,
    };
  }

  /** Frame placement: character-unit scale and the screen position of the head centre. */
  private frame(w: number, h: number): { S: number; ox: number; oy: number } {
    if (this.fullBody) {
      const S = Math.min(w / 2.6, (h * 0.78) / 3.75);
      return { S, ox: w / 2, oy: h * 0.985 - (BASE_Y + 0.22) * S };
    }
    const compact = h < 220;
    const top = -0.95;
    const bottom = compact ? 1.4 : BASE_Y + 0.25;
    const S = Math.min(w / 2.4, h / (bottom - top + 0.1));
    return { S, ox: w / 2, oy: h / 2 - ((top + bottom) / 2) * S };
  }

  // -- events -----------------------------------------------------------------------------

  private glitch(duration: number): void {
    if (this.reduced.matches) return;
    this.glitchT = Math.max(this.glitchT, duration);
  }

  react(event: Reaction): void {
    if (event === "wake") {
      this.glitch(0.35);
      if (!this.reduced.matches) this.flickerT = 0.6;
      this.wideT = 1.2;
      this.gazeGoal = { x: 0, y: 0 };
    } else if (event === "poke") {
      this.flinch.kick(-26);
      this.glitch(0.28);
      this.wideT = 0.5;
    } else if (event === "pat") {
      this.happyT = 2.2;
      this.nod.kick(10);
    } else {
      this.waveT = 2.2;
      this.tilt.kick(2.5);
    }
  }

  setDesktop(input: DesktopInput | null): void {
    this.desktop = input;
  }

  // -- state step -------------------------------------------------------------------------

  private step(input: AvatarInput, dt: number): void {
    const { state, emotion, outputLevel, inputLevel, time } = input;
    const reduced = this.reduced.matches;
    const k = (rate: number) => follow(rate, dt);
    const pose = this.voice.update({ ...input, dt }, reduced);
    this.pose = pose;

    // emotions
    if (emotion !== this.emotion) {
      this.emotion = emotion;
      if (emotion === "surprise") this.glitch(0.3);
    }
    this.happyT = Math.max(0, this.happyT - dt);
    this.waveT = Math.max(0, this.waveT - dt);
    this.wideT = Math.max(0, this.wideT - dt);
    const target = { ...FACES[emotion] };
    if (state === "thinking" && emotion === "neutral") Object.assign(target, FACES.thinking);
    if (this.happyT > 0) {
      target.happy = Math.max(target.happy, 1);
      target.smile = 1;
      target.brow = Math.max(target.brow, 0.3);
    }
    if (this.waveT > 0) target.smile = Math.max(target.smile, 0.9);
    const desktopMode = this.desktop?.mode;
    if (desktopMode === "drag" || desktopMode === "fall") {
      target.open = Math.max(target.open, 1.25);
      target.brow = Math.max(target.brow, 0.8);
      target.round = Math.max(target.round, 0.6);
    }
    for (const key of Object.keys(target) as (keyof Face)[]) {
      this.face[key] = approach(this.face[key], target[key], 9, dt);
    }

    // state blends
    this.listening = approach(this.listening, state === "listening" ? 1 : 0, 5, dt);
    this.thinking = approach(this.thinking, state === "thinking" ? 1 : 0, 4, dt);
    const level = state === "speaking" ? outputLevel : state === "listening" ? inputLevel : 0;
    this.ringLevel += (level - this.ringLevel) * k(level > this.ringLevel ? 16 : 6);
    this.spin += dt * (this.thinking > 0.5 ? 3.2 : 0.35);
    this.lean = approach(this.lean, state === "listening" ? 1 : 0, 3, dt);

    // mouth follows the voice
    const open = state === "speaking" ? voiceOpenness(outputLevel) : 0;
    this.mouth += (open - this.mouth) * k(open > this.mouth ? 30 : 14);

    // blinking: natural intervals, sometimes a double blink
    this.blinkAt -= dt;
    if (this.blinkT >= 0) {
      this.blinkT += dt;
      if (this.blinkT > 0.17) {
        this.blinkT = -1;
        if (this.blinkAgain) {
          this.blinkAgain = false;
          this.blinkAt = 0.12;
        }
      }
    } else if (this.blinkAt <= 0) {
      this.blinkT = 0;
      this.blinkAgain = Math.random() < 0.18;
      this.blinkAt = 2.2 + Math.random() * 4.5;
    }

    // gaze: cursor on the desktop, glances in idle, up and aside when thinking
    this.nextGlance -= dt;
    const cursor = this.desktop?.look;
    if (state === "thinking") this.gazeGoal = { x: 0.7, y: 0.75 };
    else if (cursor) this.gazeGoal = { x: cursor[0], y: cursor[1] };
    else if (state !== "idle") this.gazeGoal = { x: 0, y: -0.05 };
    else if (this.nextGlance <= 0) {
      const away = Math.random() < 0.45;
      this.gazeGoal = away
        ? { x: (Math.random() - 0.5) * 1.5, y: (Math.random() - 0.45) * 0.8 }
        : { x: 0, y: 0 };
      this.nextGlance = 1.4 + Math.random() * 3.6;
    }
    const gazeRate = reduced ? 2 : 7;
    this.gaze.x += (this.gazeGoal.x - this.gaze.x) * k(gazeRate);
    this.gaze.y += (this.gazeGoal.y - this.gaze.y) * k(gazeRate);

    // head: turn towards the cursor, lean while listening, nod on stressed syllables
    const turnGoal = cursor ? cursor[0] * 0.55 : state === "thinking" ? 0.35 : 0;
    this.yaw.update(turnGoal + (reduced ? 0 : wander(time * 0.12, 3) * 0.12), dt);
    const lookUp = this.thinking * 0.5;
    this.pitch.update(lookUp - this.lean * 0.35 + (cursor ? -cursor[1] * 0.2 : 0), dt);
    this.tilt.update(0, dt);
    this.nod.update(0, dt);
    this.flinch.update(0, dt);
    if (pose.beat > 0.3 && !this.beatWas && !reduced) this.nod.kick(7 + pose.level * 8);
    this.beatWas = pose.beat > 0.3;

    // hair: a damped spring pushed by head motion
    const push = (reduced ? 0 : wander(time * 0.35, 5)) * 1.1 + this.yaw.velocity * 0.6;
    this.swayVel += (push - this.sway * 14 - this.swayVel * 3.2) * dt * 4;
    this.sway += this.swayVel * dt * 4;
    this.sway = Math.max(-1.5, Math.min(1.5, this.sway));

    // glitches: occasional signal drop
    if (!reduced) {
      this.nextGlitch -= dt;
      if (this.nextGlitch <= 0) {
        this.glitch(0.1 + Math.random() * 0.16);
        this.nextGlitch = 6 + Math.random() * 9;
      }
    }
    this.flickerT = Math.max(0, this.flickerT - dt);
    if (this.glitchT > 0) {
      this.glitchT = Math.max(0, this.glitchT - dt);
      this.glitchGap -= dt;
      if (this.glitchGap <= 0 || this.slices.length === 0) {
        this.glitchGap = 0.045;
        const n = 3 + Math.floor(Math.random() * 4);
        this.slices = Array.from({ length: n }, () => ({
          y: Math.random(),
          h: 0.015 + Math.random() * 0.07,
          dx: (Math.random() - 0.5) * 0.28,
        }));
      }
    } else if (this.slices.length) this.slices = [];

    // particles rise
    if (!reduced) {
      for (let i = 0; i < this.particles.length; i++) {
        const p = this.particles[i];
        if (!p) continue;
        p.y -= p.speed * dt * (1 + this.ringLevel);
        if (p.y < -1.0) this.particles[i] = this.spawn(false);
      }
    }
  }

  // -- drawing helpers --------------------------------------------------------------------

  /** Soft glowing stroke: a wide faint pass under a thin bright one. */
  private glowStroke(
    c: CanvasRenderingContext2D,
    path: Path2D,
    color: RGB,
    width: number,
    alpha = 1,
    px = 0.006,
  ): void {
    c.lineJoin = "round";
    c.lineCap = "round";
    c.strokeStyle = rgba(color, 0.14 * alpha);
    c.lineWidth = Math.max(width * 3.4, px * 3);
    c.stroke(path);
    c.strokeStyle = rgba(color, 0.95 * alpha);
    c.lineWidth = Math.max(width, px);
    c.stroke(path);
  }

  update(input: AvatarInput): void {
    if (document.hidden) return;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (!w || !h) return;
    const dt = clampDt(input.dt, 0.1);
    this.step(input, dt);
    this.render(w, h, input);
  }

  private render(w: number, h: number, input: AvatarInput): void {
    const { dpr } = this;
    const L = this.lctx;
    const { S, ox, oy } = this.frame(w, h);
    const reduced = this.reduced.matches;
    const t = input.time;
    const pose = this.pose;

    // palette with the "angry" tint
    const a = this.face.angry;
    const pal: Palette = {
      primary: mix(this.palette.primary, RED, a * 0.8),
      secondary: mix(this.palette.secondary, RED, a * 0.4),
      hair: mix(this.palette.hair, RED, a * 0.5),
      glow: mix(this.palette.glow, RED, a * 0.85),
    };

    L.setTransform(1, 0, 0, 1, 0, 0);
    L.clearRect(0, 0, this.layer.width, this.layer.height);
    L.setTransform(dpr * S, 0, 0, dpr * S, dpr * ox, dpr * oy);
    L.lineJoin = "round";
    L.lineCap = "round";
    const px = 1 / S;

    // bust placement (desktop: voice-driven motion around the bottom of the bust)
    const breath = reduced ? 0 : Math.sin(t * 1.25) * 0.012;
    const headBob = reduced ? 0 : Math.sin(t * 1.25 - 0.6) * 0.012;
    L.save();
    this.drawProjector(L, pal, t, px);
    L.save();
    if (this.fullBody && !reduced) {
      const pivotY = BODY_BOTTOM;
      L.translate((pose.sway * h) / S, (-pose.lift * h) / S);
      L.translate(0, pivotY);
      L.rotate(pose.roll * 0.5);
      L.scale(1 + (pose.scale - 1) * 0.5, 1 + (pose.scale - 1) * 0.5);
      L.translate(0, -pivotY);
    }
    const vx = this.desktop?.vx ?? 0;
    const roll =
      (reduced ? 0 : wander(t * 0.2, 7) * 0.012) +
      this.thinking * 0.04 -
      this.listening * 0.025 +
      this.tilt.value * 0.06 +
      this.flinch.value * 0.2 +
      Math.max(-0.12, Math.min(0.12, vx * 0.5)) +
      (this.desktop?.mode === "drag" ? 0.1 : 0);
    const headDy =
      headBob +
      this.lean * 0.035 +
      this.nod.value * 0.03 +
      this.flinch.value * 0.1 -
      (this.waveT > 0 ? 0.01 : 0);
    const headDx = this.yaw.value * 0.05 + (this.desktop ? (this.desktop.facing || 0) * 0.02 : 0);

    // body
    L.save();
    L.translate(0, breath);
    this.drawBody(L, pal, px, t);
    L.restore();
    // head parts that sit behind the collar
    const head = (fn: () => void): void => {
      L.save();
      L.translate(headDx, headDy + breath * 0.6);
      L.translate(0, PIVOT_Y);
      L.rotate(roll);
      L.translate(0, -PIVOT_Y);
      fn();
      L.restore();
    };
    head(() => {
      this.drawBackHair(L, pal, px);
      this.drawNeck(L, pal, px);
    });
    L.save();
    L.translate(0, breath);
    this.drawCollar(L, pal, px, t);
    L.restore();
    head(() => {
      this.drawHead(L, pal, px, t, input.state);
    });
    // orbiting dots while thinking
    if (this.thinking > 0.05) this.drawOrbit(L, pal, px, headDx, headDy);
    L.restore();
    this.drawParticles(L, pal, t);
    L.restore();

    this.compose(w, h, S, ox, oy, t, reduced);
  }

  // -- scene parts ------------------------------------------------------------------------

  private drawProjector(c: CanvasRenderingContext2D, pal: Palette, t: number, px: number): void {
    const lv = this.ringLevel;
    // halo behind the head
    const halo = c.createRadialGradient(0, 0.25, 0.1, 0, 0.25, 1.5);
    halo.addColorStop(0, rgba(pal.glow, 0.2 + lv * 0.12));
    halo.addColorStop(1, rgba(pal.glow, 0));
    c.fillStyle = halo;
    c.fillRect(-2, -1.5, 4, 4.6);

    // light cone from the emitter up to the bust
    const cone = c.createLinearGradient(0, BASE_Y, 0, BODY_BOTTOM - 0.3);
    cone.addColorStop(0, rgba(pal.glow, 0.34 + lv * 0.15));
    cone.addColorStop(1, rgba(pal.glow, 0));
    c.fillStyle = cone;
    c.beginPath();
    c.moveTo(-0.62, BASE_Y);
    c.lineTo(-1.0, BODY_BOTTOM - 0.3);
    c.lineTo(1.0, BODY_BOTTOM - 0.3);
    c.lineTo(0.62, BASE_Y);
    c.closePath();
    c.fill();

    // base disc
    const ry = 0.15;
    c.fillStyle = rgba(pal.primary, 0.12);
    c.beginPath();
    c.ellipse(0, BASE_Y + 0.09, 1.0, ry, 0, 0, TAU);
    c.fill();
    c.strokeStyle = rgba(pal.primary, 0.5);
    c.lineWidth = Math.max(0.008, px);
    c.stroke();
    c.beginPath();
    c.ellipse(0, BASE_Y, 1.0, ry, 0, 0, TAU);
    c.fillStyle = rgba(pal.primary, 0.08);
    c.fill();
    const rim = new Path2D();
    rim.ellipse(0, BASE_Y, 1.0, ry, 0, 0, TAU);
    this.glowStroke(c, rim, pal.primary, 0.012, 0.9, px);

    // tick ring on the disc
    c.save();
    c.lineCap = "butt";
    c.strokeStyle = rgba(pal.primary, 0.55);
    c.lineWidth = 0.025;
    c.setLineDash([0.006, 0.05]);
    c.lineDashOffset = -t * 0.05 * (this.thinking > 0.5 ? 4 : 1);
    c.beginPath();
    c.ellipse(0, BASE_Y, 0.88, ry * 0.88, 0, 0, TAU);
    c.stroke();
    c.restore();

    // emitter ring with moving segments
    const spin = this.spin * 0.8;
    c.strokeStyle = rgba(pal.glow, 0.85);
    c.lineWidth = Math.max(0.016, px);
    for (const [r0, len] of [
      [0, 1.4],
      [2.5, 0.9],
      [4.1, 1.2],
    ] as const) {
      c.beginPath();
      c.ellipse(0, BASE_Y, 0.62, ry * 0.62, 0, r0 + spin, r0 + len + spin);
      c.stroke();
    }
    c.strokeStyle = rgba(pal.glow, 0.28 + lv * 0.4);
    c.lineWidth = Math.max(0.01, px);
    c.beginPath();
    c.ellipse(0, BASE_Y, 0.62, ry * 0.62, 0, 0, TAU);
    c.stroke();
    const core = c.createRadialGradient(0, BASE_Y, 0, 0, BASE_Y, 0.5);
    core.addColorStop(0, rgba(pal.glow, 0.5 + lv * 0.3));
    core.addColorStop(1, rgba(pal.glow, 0));
    c.save();
    c.translate(0, BASE_Y);
    c.scale(1, 0.25);
    c.translate(0, -BASE_Y);
    c.fillStyle = core;
    c.beginPath();
    c.arc(0, BASE_Y, 0.5, 0, TAU);
    c.fill();
    c.restore();
  }

  private drawBody(c: CanvasRenderingContext2D, pal: Palette, px: number, t: number): void {
    const s = this.shapes;
    const fill = c.createLinearGradient(0, 0.9, 0, BODY_BOTTOM);
    fill.addColorStop(0, rgba(pal.primary, 0.3));
    fill.addColorStop(0.55, rgba(pal.primary, 0.16));
    fill.addColorStop(1, rgba(pal.primary, 0));
    c.fillStyle = fill;
    c.fill(s.body);
    const edge = c.createLinearGradient(0, 0.9, 0, BODY_BOTTOM);
    edge.addColorStop(0, rgba(pal.primary, 1));
    edge.addColorStop(0.7, rgba(pal.primary, 0.6));
    edge.addColorStop(1, rgba(pal.primary, 0));
    c.lineWidth = Math.max(0.016, px);
    c.strokeStyle = edge;
    c.stroke(s.bodyEdge);
    // shoulder seams in the secondary colour
    const seam = c.createLinearGradient(0, 1.0, 0, BODY_BOTTOM);
    seam.addColorStop(0, rgba(pal.secondary, 0.95));
    seam.addColorStop(1, rgba(pal.secondary, 0));
    c.strokeStyle = seam;
    c.lineWidth = Math.max(0.011, px);
    c.stroke(s.seamL);
    c.stroke(s.seamR);

    // circuit traces and the chip emblem
    const pulseRate = this.thinking > 0.5 ? 3.2 : 0.9;
    const energy = 0.35 + this.thinking * 0.4 + this.ringLevel * 0.4;
    c.strokeStyle = rgba(pal.primary, 0.4);
    c.lineWidth = Math.max(0.01, px);
    c.stroke(s.traces);
    c.save();
    c.setLineDash([0.12, 0.5]);
    c.lineDashOffset = -t * pulseRate * 0.5;
    c.strokeStyle = rgba(pal.secondary, Math.min(1, 0.55 + energy * 0.6));
    c.lineWidth = Math.max(0.016, px * 1.4);
    c.stroke(s.traces);
    c.restore();
    c.fillStyle = rgba(pal.secondary, 0.5 + 0.4 * Math.sin(t * pulseRate * 2) ** 2);
    c.fill(s.traceNodes);
    c.fillStyle = rgba(pal.secondary, 0.14 + 0.1 * Math.sin(t * pulseRate * 2 + 1));
    c.fill(s.chip);
    this.glowStroke(c, s.chip, pal.secondary, 0.014, 0.95, px);
    c.lineWidth = Math.max(0.01, px);
    c.strokeStyle = rgba(pal.primary, 0.9);
    c.stroke(s.chipInner);
    c.fillStyle = rgba(pal.primary, 0.55 + 0.4 * Math.sin(t * pulseRate * 2) ** 2);
    c.beginPath();
    c.arc(0, 1.5, 0.014, 0, TAU);
    c.fill();
  }

  private drawNeck(c: CanvasRenderingContext2D, pal: Palette, px: number): void {
    const s = this.shapes;
    c.save();
    const outside = new Path2D();
    outside.rect(-3, -3, 6, 8);
    outside.addPath(s.face);
    c.clip(outside, "evenodd");
    const fill = c.createLinearGradient(0, 0.4, 0, 1.4);
    fill.addColorStop(0, rgba(pal.primary, 0.1));
    fill.addColorStop(1, rgba(pal.primary, 0.24));
    c.fillStyle = fill;
    c.fill(s.neck);
    // shadow under the chin
    const sh = c.createLinearGradient(0, 0.5, 0, 0.8);
    sh.addColorStop(0, "rgba(0,8,24,0.34)");
    sh.addColorStop(1, "rgba(0,8,24,0)");
    c.fillStyle = sh;
    c.fillRect(-0.17, 0.5, 0.34, 0.3);
    c.strokeStyle = rgba(pal.primary, 0.85);
    c.lineWidth = Math.max(0.012, px);
    c.stroke(s.neck);
    c.restore();
  }

  private drawCollar(c: CanvasRenderingContext2D, pal: Palette, px: number, t: number): void {
    const s = this.shapes;
    for (const wing of [s.wingL, s.wingR]) {
      const fill = c.createLinearGradient(0, 0.55, 0, 1.3);
      fill.addColorStop(0, rgba(pal.primary, 0.32));
      fill.addColorStop(1, rgba(pal.primary, 0.14));
      c.fillStyle = fill;
      c.fill(wing);
      this.glowStroke(c, wing, pal.primary, 0.016, 0.95, px);
    }
    const pulse = 0.55 + 0.35 * Math.sin(t * (this.thinking > 0.5 ? 7 : 1.8));
    for (const trim of [s.trimL, s.trimR]) {
      this.glowStroke(c, trim, pal.secondary, 0.01, pulse, px);
    }
  }

  private drawBackHair(c: CanvasRenderingContext2D, pal: Palette, px: number): void {
    const sw = this.sway;
    const sx = (x: number, y: number): number => x + sw * 0.06 * smooth01((y + 0.1) / 1.2) ** 1.5;
    const p = new Path2D();
    p.moveTo(-0.47, -0.1);
    p.bezierCurveTo(-0.58, -0.45, -0.36, -0.76, 0, -0.76);
    p.bezierCurveTo(0.4, -0.76, 0.64, -0.5, 0.66, -0.1);
    p.bezierCurveTo(sx(0.7, 0.3), 0.3, sx(0.7, 0.8), 0.8, sx(0.56, 1.18), 1.18);
    p.lineTo(sx(0.34, 0.8), 0.8);
    p.lineTo(0.3, 0.4);
    p.lineTo(-0.2, 0.2);
    p.lineTo(-0.42, 0.0);
    p.closePath();
    c.save();
    const outside = new Path2D();
    outside.rect(-3, -3, 6, 8);
    outside.addPath(this.shapes.face);
    c.clip(outside, "evenodd");
    const g = c.createLinearGradient(0, -0.76, 0, 1.18);
    g.addColorStop(0, rgba(pal.hair, 0.42));
    g.addColorStop(1, rgba(pal.primary, 0.3));
    c.fillStyle = g;
    c.fill(p);
    const e = c.createLinearGradient(0, -0.76, 0, 1.18);
    e.addColorStop(0, rgba(pal.hair, 0.55));
    e.addColorStop(1, rgba(pal.primary, 0.5));
    c.strokeStyle = e;
    c.lineWidth = Math.max(0.01, px);
    c.stroke(p);
    c.restore();
  }

  private drawHead(
    c: CanvasRenderingContext2D,
    pal: Palette,
    px: number,
    t: number,
    state: State,
  ): void {
    const s = this.shapes;
    // face
    const fill = c.createLinearGradient(0, -0.62, 0, 0.63);
    fill.addColorStop(0, rgba(pal.primary, 0.26));
    fill.addColorStop(0.6, rgba(pal.primary, 0.18));
    fill.addColorStop(1, rgba(pal.primary, 0.24));
    c.fillStyle = fill;
    c.fill(s.face);
    // soft cheek light
    const cheek = c.createRadialGradient(0.05, 0.18, 0.02, 0.05, 0.18, 0.5);
    cheek.addColorStop(0, rgba(pal.glow, 0.1));
    cheek.addColorStop(1, rgba(pal.glow, 0));
    c.fillStyle = cheek;
    c.fill(s.face);
    this.glowStroke(c, s.face, pal.primary, 0.014, 1, px);

    // features shift with the head turn and pitch (a cheap 2.5D parallax)
    const fx = this.yaw.value * 0.06;
    const fy = this.pitch.value * 0.035 + this.nod.value * 0.012;
    c.save();
    c.translate(fx, fy);
    this.drawFeatures(c, pal, px, t, state);
    c.restore();

    this.drawFrontHair(c, pal, px);
    this.drawHeadset(c, pal, px, t);
  }

  private drawFeatures(
    c: CanvasRenderingContext2D,
    pal: Palette,
    px: number,
    t: number,
    state: State,
  ): void {
    const f = this.face;
    // blush
    const blush = 0.07 + f.happy * 0.2 + this.listening * 0.03;
    for (const sx of [-1, 1]) {
      const g = c.createRadialGradient(sx * 0.29, 0.27, 0.01, sx * 0.29, 0.27, 0.12);
      g.addColorStop(0, rgba(pal.hair, blush));
      g.addColorStop(1, rgba(pal.hair, 0));
      c.fillStyle = g;
      c.fillRect(sx * 0.29 - 0.12, 0.15, 0.24, 0.24);
    }

    // eyes
    const blinkLid =
      this.blinkT < 0
        ? 0
        : this.blinkT < 0.07
          ? this.blinkT / 0.07
          : Math.max(0, 1 - (this.blinkT - 0.07) / 0.1);
    const widen = this.listening * 0.12 + (this.wideT > 0 ? Math.min(1, this.wideT * 3) * 0.3 : 0);
    const lid = Math.max(0, (f.open + widen) * (1 - blinkLid * 0.97));
    for (const s of [-1, 1]) this.drawEye(c, pal, px, s, lid, state);

    // brows (in the hair colour): the outer one hides under the fringe on the right
    c.lineCap = "round";
    const browBase = EYE_Y - 0.185 - f.brow * 0.07 - this.listening * 0.012;
    for (const s of [-1, 1]) {
      const inner = browBase - f.tilt * 0.065 + (s > 0 ? -this.thinking * 0.025 : 0);
      const outer = browBase + f.tilt * 0.02 + (f.brow < -0.2 ? 0.02 : 0);
      const b = new Path2D();
      b.moveTo(s * 0.07, inner + 0.01);
      b.quadraticCurveTo(s * 0.22, Math.min(inner, outer) - 0.025, s * 0.38, outer + 0.015);
      this.glowStroke(c, b, pal.hair, 0.014, 1, px);
    }

    // nose
    c.strokeStyle = rgba(pal.primary, 0.55);
    c.lineWidth = Math.max(0.008, px);
    c.beginPath();
    c.moveTo(0.012, 0.22);
    c.lineTo(-0.012, 0.275);
    c.lineTo(0.02, 0.28);
    c.stroke();

    this.drawMouth(c, pal, px);

    // tiny HUD marks on the cheeks
    c.strokeStyle = rgba(pal.glow, 0.45);
    c.lineWidth = Math.max(0.006, px * 0.8);
    for (const s of [-1, 1]) {
      const x = s * 0.36;
      const y = 0.31;
      c.beginPath();
      c.moveTo(x - 0.02, y);
      c.lineTo(x + 0.02, y);
      c.moveTo(x, y - 0.02);
      c.lineTo(x, y + 0.02);
      c.stroke();
    }
    void t;
  }

  private drawEye(
    c: CanvasRenderingContext2D,
    pal: Palette,
    px: number,
    s: number,
    lid: number,
    state: State,
  ): void {
    const f = this.face;
    const cy = EYE_Y;
    const xi = s * 0.05;
    const xo = s * 0.39;
    const yi = cy + 0.02;
    const yo = cy - 0.028;
    const o = Math.max(0, lid);
    const rise = 0.185 * o;
    const low = 0.056 * Math.min(1, o * 1.3);
    const eye = new Path2D();
    const upper = new Path2D();
    upper.moveTo(xi, yi);
    upper.bezierCurveTo(s * 0.1, yi - rise * 1.05, s * 0.26, yo - rise * 1.3, xo, yo);
    eye.addPath(upper);
    eye.bezierCurveTo(s * 0.3, yo + low * 1.7, s * 0.14, yi + low * 1.6, xi, yi);
    eye.closePath();

    const open = 1 - f.happy;
    if (open > 0.02 && o > 0.03) {
      c.save();
      c.globalAlpha = open;
      // sclera
      c.fillStyle = rgba(pal.primary, 0.3);
      c.fill(eye);
      c.save();
      c.clip(eye);
      this.drawIris(c, pal, px, s, o, state);
      c.restore();
      // lids: heavy upper line with a lash flick, light lower line
      const lash = new Path2D();
      lash.moveTo(xo, yo);
      lash.lineTo(xo + s * 0.055, yo - 0.05 * Math.min(1, o) - 0.005);
      lash.moveTo(xo - s * 0.04, yo - rise * 0.78);
      lash.lineTo(xo - s * 0.005, yo - rise * 0.78 - 0.035 * Math.min(1, o));
      this.glowStroke(c, upper, pal.primary, 0.022, 1, px);
      c.strokeStyle = rgba(pal.primary, 0.95);
      c.lineWidth = Math.max(0.012, px);
      c.stroke(lash);
      c.strokeStyle = rgba(pal.primary, 0.45);
      c.lineWidth = Math.max(0.008, px);
      c.stroke(eye);
      c.restore();
    }
    if (f.happy > 0.02) {
      // happy closed arc
      const arc = new Path2D();
      arc.moveTo(xi, yi);
      arc.bezierCurveTo(s * 0.12, cy - 0.12, s * 0.28, cy - 0.12, xo, yo + 0.01);
      c.save();
      c.globalAlpha = f.happy;
      this.glowStroke(c, arc, pal.primary, 0.026, 1, px);
      c.restore();
    }
  }

  private drawIris(
    c: CanvasRenderingContext2D,
    pal: Palette,
    px: number,
    s: number,
    lid: number,
    state: State,
  ): void {
    const lv = this.ringLevel;
    const gx = this.gaze.x;
    const gy = this.gaze.y;
    const ix = s * 0.222 + gx * 0.05 + (s > 0 ? 0 : 0);
    const iy = EYE_Y - 0.005 - gy * 0.035 + (Math.min(lid, 1) - 1) * 0.02;
    const r = 0.118 * (1 + this.listening * 0.04 + lv * 0.05);
    const boost = this.listening * 0.15 + lv * 0.35 + (state === "thinking" ? 0.1 : 0);

    c.save();
    c.translate(ix, iy);
    const g = c.createRadialGradient(0, 0, 0, 0, 0, r);
    g.addColorStop(0, rgba(pal.glow, 0.18 + boost * 0.3));
    g.addColorStop(0.7, rgba(pal.glow, 0.42 + boost * 0.3));
    g.addColorStop(1, rgba(pal.glow, 0.9));
    c.fillStyle = g;
    c.beginPath();
    c.arc(0, 0, r, 0, TAU);
    c.fill();

    c.lineCap = "butt";
    const spin = this.spin;
    // outer rim
    c.strokeStyle = rgba(pal.glow, 0.95);
    c.lineWidth = Math.max(0.009, px);
    c.beginPath();
    c.arc(0, 0, r - 0.004, 0, TAU);
    c.stroke();
    // segmented ring (rotates; fast while thinking)
    c.strokeStyle = rgba(pal.secondary, 0.65 + boost * 0.35);
    c.lineWidth = Math.max(0.009, px);
    for (let i = 0; i < 3; i++) {
      const a0 = spin * 1.2 * s + (i / 3) * TAU;
      c.beginPath();
      c.arc(0, 0, r * 0.76, a0, a0 + 1.35);
      c.stroke();
    }
    // tick ring (dashed circle = tiny radial ticks), counter-rotating
    c.save();
    c.rotate(-spin * 0.9);
    c.strokeStyle = rgba(pal.glow, 0.75 + boost * 0.25);
    c.lineWidth = r * 0.17;
    const tickR = r * 0.56;
    c.setLineDash([0.0035, (TAU * tickR) / 26 - 0.0035]);
    c.beginPath();
    c.arc(0, 0, tickR, 0, TAU);
    c.stroke();
    c.restore();
    // inner ring, reacts to the level
    c.strokeStyle = rgba(pal.glow, 0.7 + boost * 0.3);
    c.lineWidth = Math.max(0.008, px);
    c.beginPath();
    c.arc(0, 0, r * (0.38 + lv * 0.06), 0, TAU);
    c.stroke();
    // pupil and catch-lights
    c.fillStyle = "rgba(2,10,26,0.88)";
    c.beginPath();
    c.arc(0, 0, r * 0.22, 0, TAU);
    c.fill();
    c.fillStyle = "rgba(255,255,255,0.95)";
    c.beginPath();
    c.arc(-r * 0.32, -r * 0.34, r * 0.15, 0, TAU);
    c.fill();
    c.fillStyle = "rgba(255,255,255,0.6)";
    c.beginPath();
    c.arc(r * 0.28, r * 0.3, r * 0.07, 0, TAU);
    c.fill();
    c.restore();
  }

  private drawMouth(c: CanvasRenderingContext2D, pal: Palette, px: number): void {
    const f = this.face;
    const open = Math.max(this.mouth, f.round * 0.5);
    const half = 0.09 * (1 - f.round * 0.4) * (1 + f.smile * 0.1 + this.mouth * 0.1);
    const curve = f.smile;
    const cornerY = MOUTH_Y - curve * 0.02 * (1 - f.round);
    const midY = MOUTH_Y + curve * 0.014;
    if (f.round > 0.4) {
      // surprised: a small round mouth
      const o = new Path2D();
      o.ellipse(0, MOUTH_Y + 0.015, 0.04, 0.03 + open * 0.05, 0, 0, TAU);
      c.save();
      c.globalAlpha = Math.min(1, (f.round - 0.4) * 2.5);
      c.fillStyle = rgba(pal.hair, 0.35);
      c.fill(o);
      this.glowStroke(c, o, pal.primary, 0.012, 1, px);
      c.restore();
      if (f.round > 0.9) return;
    }
    const upper = new Path2D();
    upper.moveTo(-half, cornerY);
    upper.quadraticCurveTo(0, 2 * midY - cornerY - open * 0.004, half, cornerY);
    if (open > 0.04) {
      const depth = 0.012 + open * 0.085;
      const mouth = new Path2D();
      mouth.addPath(upper);
      mouth.quadraticCurveTo(0, 2 * (midY + depth) - cornerY, -half, cornerY);
      c.fillStyle = "rgba(2,10,26,0.5)";
      c.fill(mouth);
      c.fillStyle = rgba(pal.hair, 0.35);
      c.fill(mouth);
      // tongue glow
      c.fillStyle = rgba(pal.glow, 0.25);
      c.beginPath();
      c.ellipse(0, midY + depth * 0.7, half * 0.5, depth * 0.22, 0, 0, TAU);
      c.fill();
      this.glowStroke(c, mouth, pal.primary, 0.01, 0.95, px);
    } else {
      this.glowStroke(c, upper, pal.primary, 0.014, 1, px);
    }
  }

  private drawFrontHair(c: CanvasRenderingContext2D, pal: Palette, px: number): void {
    const sw = this.sway;
    const sx = (x: number, y: number): number => x + sw * 0.06 * smooth01((y + 0.1) / 1.2) ** 1.5;
    const fill = c.createLinearGradient(0, -0.76, 0, 1.18);
    fill.addColorStop(0, rgba(pal.hair, 0.58));
    fill.addColorStop(1, rgba(pal.primary, 0.5));
    const edge = c.createLinearGradient(0, -0.76, 0, 1.18);
    edge.addColorStop(0, rgba(pal.hair, 1));
    edge.addColorStop(1, rgba(pal.primary, 0.95));

    // side-swept fringe and the long curtain down the right cheek
    const fringe = new Path2D();
    fringe.moveTo(-0.22, -0.74);
    fringe.bezierCurveTo(0.16, -0.8, 0.54, -0.64, 0.62, -0.14);
    fringe.bezierCurveTo(sx(0.68, 0.3), 0.3, sx(0.7, 0.8), 0.8, sx(0.54, 1.18), 1.18);
    fringe.bezierCurveTo(sx(0.5, 0.95), 0.92, sx(0.44, 0.62), 0.62, sx(0.42, 0.4), 0.4);
    fringe.bezierCurveTo(0.455, 0.2, 0.45, 0.02, 0.425, -0.1);
    fringe.bezierCurveTo(0.4, -0.2, 0.26, -0.26, 0.1, -0.35);
    fringe.bezierCurveTo(-0.02, -0.42, -0.1, -0.5, -0.24, -0.66);
    fringe.closePath();
    c.fillStyle = fill;
    c.fill(fringe);

    // short cap on the undercut side
    const cap = new Path2D();
    cap.moveTo(-0.22, -0.74);
    cap.bezierCurveTo(-0.46, -0.72, -0.56, -0.44, -0.49, -0.1);
    cap.lineTo(-0.44, -0.16);
    cap.bezierCurveTo(-0.42, -0.3, -0.36, -0.42, -0.26, -0.5);
    cap.bezierCurveTo(-0.2, -0.54, -0.12, -0.52, -0.1, -0.5);
    cap.lineTo(-0.24, -0.66);
    cap.closePath();
    c.fillStyle = fill;
    c.fill(cap);

    c.lineWidth = Math.max(0.012, px);
    c.strokeStyle = edge;
    c.stroke(fringe);
    c.stroke(cap);

    // strands with light motion
    c.lineCap = "round";
    c.lineWidth = Math.max(0.008, px);
    c.strokeStyle = rgba(pal.hair, 0.5);
    const strands: [number, number, number, number, number, number, number, number][] = [
      [-0.16, -0.7, 0.1, -0.72, 0.4, -0.56, 0.52, -0.2],
      [-0.12, -0.62, 0.14, -0.6, 0.34, -0.42, 0.46, -0.12],
      [-0.08, -0.55, 0.12, -0.5, 0.28, -0.36, 0.38, -0.2],
    ];
    for (const [x0, y0, x1, y1, x2, y2, x3, y3] of strands) {
      c.beginPath();
      c.moveTo(x0, y0);
      c.bezierCurveTo(x1, y1, x2, y2, x3, y3);
      c.stroke();
    }
    c.strokeStyle = rgba(pal.primary, 0.45);
    const tail: [number, number, number, number, number, number, number, number][] = [
      [0.52, -0.1, 0.6, 0.3, 0.62, 0.7, 0.52, 1.1],
      [0.48, 0.1, 0.55, 0.4, 0.55, 0.75, 0.47, 1.0],
      [0.44, 0.3, 0.5, 0.5, 0.5, 0.7, 0.44, 0.84],
    ];
    for (const [x0, y0, x1, y1, x2, y2, x3, y3] of tail) {
      c.beginPath();
      c.moveTo(x0, y0);
      c.bezierCurveTo(sx(x1, y1), y1, sx(x2, y2), y2, sx(x3, y3), y3);
      c.stroke();
    }
    // loose strand
    c.strokeStyle = rgba(pal.hair, 0.8);
    c.lineWidth = Math.max(0.007, px);
    c.beginPath();
    c.moveTo(0.1, -0.36);
    c.bezierCurveTo(0.14, -0.3, 0.2 + sw * 0.03, -0.2, 0.2 + sw * 0.07, -0.08);
    c.stroke();

    // undercut: shaved side with short fine lines
    c.strokeStyle = rgba(pal.hair, 0.65);
    c.lineWidth = Math.max(0.006, px * 0.8);
    c.beginPath();
    for (let i = 0; i < 11; i++) {
      const y = -0.2 + i * 0.045;
      c.moveTo(-0.455 + Math.abs(y + 0.05) * 0.04, y);
      c.lineTo(-0.395 + Math.abs(y + 0.05) * 0.06, y - 0.03);
    }
    c.stroke();
    c.strokeStyle = rgba(pal.secondary, 0.7);
    c.lineWidth = Math.max(0.007, px);
    c.beginPath();
    c.moveTo(-0.45, -0.19);
    c.lineTo(-0.4, -0.3);
    c.lineTo(-0.3, -0.42);
    c.stroke();
  }

  private drawHeadset(c: CanvasRenderingContext2D, pal: Palette, px: number, t: number): void {
    const s = this.shapes;
    this.glowStroke(c, s.headband, pal.primary, 0.014, 1, px);
    // accent segment on the crown
    c.strokeStyle = rgba(pal.secondary, 0.95);
    c.lineWidth = Math.max(0.02, px * 1.5);
    c.beginPath();
    c.moveTo(-0.14, -0.77);
    c.lineTo(0.2, -0.77);
    c.stroke();
    // boom mic
    this.glowStroke(c, s.mic, pal.primary, 0.009, 0.8, px);
    c.fillStyle = rgba(pal.secondary, 0.95);
    c.beginPath();
    c.arc(-0.27, 0.5, 0.017, 0, TAU);
    c.fill();

    // ear module: glows while listening, LED blinks slowly
    if (this.listening > 0.02) {
      const g = c.createRadialGradient(-0.52, 0.06, 0.02, -0.52, 0.06, 0.28);
      g.addColorStop(0, rgba(pal.glow, 0.5 * this.listening + this.ringLevel * 0.3));
      g.addColorStop(1, rgba(pal.glow, 0));
      c.fillStyle = g;
      c.fillRect(-0.85, -0.25, 0.7, 0.62);
    }
    c.fillStyle = rgba(pal.primary, 0.28 + this.listening * 0.3);
    c.fill(s.earHex);
    this.glowStroke(c, s.earHex, pal.primary, 0.014, 1, px);
    c.strokeStyle = rgba(pal.secondary, 0.8);
    c.lineWidth = Math.max(0.008, px);
    c.stroke(s.earInner);
    const led = this.reduced.matches ? 0.8 : (Math.sin(t * 2.1) > 0.35 ? 1 : 0.18) + this.listening;
    c.fillStyle = rgba(pal.secondary, Math.min(1, led));
    c.beginPath();
    c.arc(-0.52, 0.06, 0.019, 0, TAU);
    c.fill();
  }

  private drawOrbit(
    c: CanvasRenderingContext2D,
    pal: Palette,
    px: number,
    dx: number,
    dy: number,
  ): void {
    const n = 4;
    for (let i = 0; i < n; i++) {
      const a = this.spin * 0.9 + (i / n) * TAU;
      const depth = Math.sin(a);
      const x = dx + Math.cos(a) * 0.82;
      const y = dy - 0.1 + depth * 0.2;
      const size = (0.016 + 0.008 * depth) * 1.2;
      c.fillStyle = rgba(i % 2 ? pal.secondary : pal.glow, (0.45 + 0.45 * depth) * this.thinking);
      c.beginPath();
      c.arc(x, y, Math.max(size, px), 0, TAU);
      c.fill();
    }
  }

  private drawParticles(c: CanvasRenderingContext2D, pal: Palette, t: number): void {
    if (this.reduced.matches) return;
    for (const p of this.particles) {
      const fadeIn = smooth01((BASE_Y - p.y) / 0.4);
      const fadeOut = smooth01((p.y + 1.0) / 0.6);
      const alpha = fadeIn * fadeOut * (0.35 + 0.35 * Math.sin(t * 3 + p.phase));
      if (alpha <= 0.01) continue;
      c.fillStyle = rgba(p.size > 0.017 ? pal.secondary : pal.glow, alpha);
      c.fillRect(p.x + Math.sin(t * 0.8 + p.phase) * 0.03, p.y, p.size, p.size);
    }
  }

  // -- composition ------------------------------------------------------------------------

  private compose(
    w: number,
    h: number,
    S: number,
    ox: number,
    oy: number,
    t: number,
    reduced: boolean,
  ): void {
    void w;
    void h;
    void ox;
    const { ctx, dpr } = this;
    const W = this.canvas.width;
    const H = this.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, W, H);

    const glitching = this.glitchT > 0;
    const shift = (1.2 + (glitching ? 4 : 0)) * dpr;
    const flick =
      this.flickerT > 0
        ? 0.35 + Math.random() * 0.65
        : reduced
          ? 1
          : 0.95 + wander(t * 3, 9) * 0.05;

    // chromatic fringe: the layer tinted red and blue, offset behind the real picture
    const T = this.tctx;
    const tw = this.tint.width;
    const th = this.tint.height;
    for (const [color, dir] of [
      ["rgb(255,40,90)", -1],
      ["rgb(40,110,255)", 1],
    ] as const) {
      T.globalCompositeOperation = "copy";
      T.drawImage(this.layer, 0, 0, tw, th);
      T.globalCompositeOperation = "source-in";
      T.fillStyle = color;
      T.fillRect(0, 0, tw, th);
      ctx.globalAlpha = 0.42 * flick;
      ctx.drawImage(this.tint, dir * shift, 0, W, H);
    }

    ctx.globalAlpha = flick;
    ctx.drawImage(this.layer, 0, 0);
    if (glitching) {
      const k = Math.min(1, this.glitchT * 8);
      for (const sl of this.slices) {
        const y = Math.floor(sl.y * H);
        const hh = Math.max(2, Math.floor(sl.h * H));
        const dx = sl.dx * S * dpr * k;
        ctx.clearRect(0, y, W, hh);
        ctx.drawImage(this.layer, 0, y, W, hh, dx, y, W, hh);
        ctx.globalAlpha = 0.5 * flick;
        ctx.drawImage(this.tint, 0, y / 2, tw, hh / 2, dx * 1.6 + 3 * dpr, y, W, hh);
        ctx.globalAlpha = flick;
      }
    }
    ctx.globalAlpha = 1;

    // sweeping highlight band (only paints over the drawing)
    if (!reduced) {
      const period = 6.5;
      const f = (t % period) / period;
      const by = oy * dpr + (-1.2 + f * 4.4) * S * dpr;
      const bh = 0.18 * S * dpr;
      const g = ctx.createLinearGradient(0, by - bh, 0, by + bh);
      g.addColorStop(0, "rgba(255,255,255,0)");
      g.addColorStop(0.5, "rgba(255,255,255,0.2)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.globalCompositeOperation = "source-atop";
      ctx.fillStyle = g;
      ctx.fillRect(0, by - bh, W, bh * 2);
    }

    // scanlines: punch faint holes into the picture
    if (this.scan) {
      ctx.globalCompositeOperation = "destination-out";
      const period = this.scanPeriod;
      const off = reduced ? 0 : (t * 14 * dpr) % period;
      ctx.setTransform(1, 0, 0, 1, 0, off);
      ctx.fillStyle = this.scan;
      ctx.fillRect(0, -period, W, H + period * 2);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    }
    ctx.globalCompositeOperation = "source-over";
  }

  // -- desktop ----------------------------------------------------------------------------

  /** Projector base on the frame (drawing-buffer pixels): the character stands on that line. */
  layout(): Layout | null {
    if (!this.fullBody) return null;
    const { width, height } = this.canvas;
    const cssW = this.canvas.clientWidth || width;
    const cssH = this.canvas.clientHeight || height;
    const { S, ox, oy } = this.frame(cssW, cssH);
    const k = height / cssH;
    return {
      feet: (oy + (BASE_Y + 0.2) * S) * k,
      seat: (oy + BODY_BOTTOM * S) * k,
      center: ox * k,
      head: (oy - 0.8 * S) * k,
    };
  }

  dispose(): void {
    this.resize.disconnect();
    this.themeWatch.disconnect();
  }
}
