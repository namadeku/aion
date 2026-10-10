// Holographic J.A.R.V.I.S.-style ring drawn on a 2D canvas. Reacts to voice and state.

import type { State } from "../types";
import type { Avatar, AvatarInput } from "./types";

const COLORS: Record<State, [number, number, number]> = {
  idle: [84, 214, 255],
  listening: [93, 255, 176],
  thinking: [255, 181, 71],
  speaking: [84, 214, 255],
};

const BARS = 96;

export class HudAvatar implements Avatar {
  private ctx: CanvasRenderingContext2D;
  private resize: ResizeObserver;
  private color: [number, number, number] = [...COLORS.idle];
  private energy = 0;
  private bars = new Float32Array(BARS);
  private spin = 0;

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D недоступен");
    this.ctx = ctx;
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(canvas);
    this.fit();
  }

  private fit(): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { width, height } = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.round(width * dpr));
    this.canvas.height = Math.max(1, Math.round(height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  update({ state, outputLevel, inputLevel, time, dt }: AvatarInput): void {
    const { ctx } = this;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (!w || !h) return;

    // ease colour and energy towards the current state
    const target = COLORS[state];
    for (let i = 0; i < 3; i++) this.color[i] += (target[i] - this.color[i]) * Math.min(1, dt * 4);
    const level = state === "speaking" ? outputLevel : state === "listening" ? inputLevel : 0;
    this.energy += (level - this.energy) * Math.min(1, dt * (level > this.energy ? 18 : 6));
    this.spin += dt * (state === "thinking" ? 2.4 : 0.25);

    const [r, g, b] = this.color.map(Math.round);
    const rgba = (a: number) => `rgba(${r},${g},${b},${a})`;
    const cx = w / 2;
    const cy = h * 0.47;
    const R = Math.min(w, h) * 0.3;

    ctx.clearRect(0, 0, w, h);

    // glow
    const glow = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 1.6);
    glow.addColorStop(0, rgba(0.16 + this.energy * 0.25));
    glow.addColorStop(1, rgba(0));
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    ctx.lineCap = "round";

    // outer tick ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.spin * 0.3);
    for (let i = 0; i < 120; i++) {
      const a = (i / 120) * Math.PI * 2;
      const long = i % 10 === 0;
      ctx.strokeStyle = rgba(long ? 0.55 : 0.18);
      ctx.lineWidth = long ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * R * 1.32, Math.sin(a) * R * 1.32);
      ctx.lineTo(Math.cos(a) * R * (long ? 1.4 : 1.36), Math.sin(a) * R * (long ? 1.4 : 1.36));
      ctx.stroke();
    }
    ctx.restore();

    // segmented arcs (counter-rotating)
    const arcs: [number, number, number, number][] = [
      [1.18, 0.0, 1.4, 3],
      [1.18, 2.1, 0.9, 3],
      [1.18, 3.6, 1.8, 3],
      [1.08, 0.7, 2.6, 1.5],
      [1.08, 4.2, 1.2, 1.5],
    ];
    arcs.forEach(([radius, start, len, width], i) => {
      const dir = i % 2 ? -1 : 1;
      ctx.strokeStyle = rgba(0.35 + this.energy * 0.5);
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.arc(cx, cy, R * radius, start + this.spin * dir, start + len + this.spin * dir);
      ctx.stroke();
    });

    // thinking: sweeping scanner arc
    if (state === "thinking") {
      ctx.strokeStyle = rgba(0.9);
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 0.96, this.spin * 1.6, this.spin * 1.6 + 0.9);
      ctx.stroke();
    }

    // radial voice bars
    for (let i = 0; i < BARS; i++) {
      const wave = Math.sin(time * 3 + i * 0.45) * 0.5 + Math.sin(time * 5.3 + i * 1.7) * 0.5 + 1;
      const target = this.energy * (0.35 + 0.65 * wave * 0.5) + 0.03;
      this.bars[i] += (target - this.bars[i]) * Math.min(1, dt * 14);
      const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
      const inner = R * 0.74;
      const outer = inner + R * 0.32 * this.bars[i] + 2;
      ctx.strokeStyle = rgba(0.25 + this.bars[i] * 0.75);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
      ctx.lineTo(cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
      ctx.stroke();
    }

    // core
    const breathe = state === "idle" ? Math.sin(time * 1.4) * 0.03 : 0;
    const core = R * (0.42 + this.energy * 0.12 + breathe);
    const coreFill = ctx.createRadialGradient(cx, cy, 0, cx, cy, core);
    coreFill.addColorStop(0, rgba(0.55 + this.energy * 0.4));
    coreFill.addColorStop(0.55, rgba(0.18));
    coreFill.addColorStop(1, rgba(0.02));
    ctx.fillStyle = coreFill;
    ctx.beginPath();
    ctx.arc(cx, cy, core, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = rgba(0.85);
    ctx.lineWidth = 2;
    ctx.stroke();

    // inner hex
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-this.spin * 0.6);
    ctx.strokeStyle = rgba(0.4);
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i <= 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      const x = Math.cos(a) * core * 0.55;
      const y = Math.sin(a) * core * 0.55;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  dispose(): void {
    this.resize.disconnect();
  }
}
