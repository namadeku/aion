// Desktop mascot renderer. Runs in an off-screen window: draws the character full-body plus a
// speech bubble and streams RGBA frames to the backend, which shows them in a per-pixel
// transparent native window over the Windows desktop (WebView2 cannot be transparent itself).

import { createAvatar } from "../avatar";
import type { Avatar, BodyMode, DesktopInput, Layout, Reaction } from "../avatar/types";
import { store } from "../store";
import type { BusEvent } from "../types";

const FPS = 30;
const WAVE_WORDS =
  /(^|[^а-яё])(привет|здравствуй|доброе утро|добрый (день|вечер)|доброй ночи|пока|до встречи|до свидания|спокойной ночи)/i;
const REACTIONS = new Set<Reaction>(["wake", "wave", "poke", "pat"]);

interface Bubble {
  text: string;
  /** performance.now() after which the bubble fades out. */
  until: number;
  alpha: number;
}

export function desktopApp(root: HTMLElement): void {
  document.documentElement.classList.add("desktop-mode");
  let width = 360;
  let height = 600;
  // a fresh canvas per avatar: a canvas cannot switch between 2D (HUD) and WebGL contexts
  let canvas = document.createElement("canvas");
  const out = document.createElement("canvas");
  const ctx = out.getContext("2d", { willReadFrequently: true })!;

  let avatar: Avatar | null = null;
  let avatarKey = "";
  let layout: Layout | null = null;
  let sentLayout = "";
  let desktop: DesktopInput = { mode: "stand", facing: 0, vx: 0, vy: 0, look: null };
  const bubble: Bubble = { text: "", until: 0, alpha: 0 };
  let replyOpen = false;

  function resize(w: number, h: number): void {
    width = w;
    height = h;
    sizeCanvas();
    out.width = w;
    out.height = h;
    sentLayout = "";
  }
  function sizeCanvas(): void {
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width = `${width / dpr}px`;
    canvas.style.height = `${height / dpr}px`;
  }
  resize(width, height);

  async function mountAvatar(): Promise<void> {
    if (!store.loaded) return; // wait for the snapshot: it says which avatar to show
    const key = `${store.avatar.kind}:${store.avatar.path ?? ""}`;
    if (key === avatarKey) return;
    avatarKey = key;
    avatar?.dispose();
    avatar = null;
    canvas = document.createElement("canvas");
    canvas.className = "desktop-view";
    sizeCanvas();
    root.replaceChildren(canvas);
    const created = await createAvatar(canvas, store.avatar, { fullBody: true });
    if (key !== avatarKey) created.dispose();
    else {
      avatar = created;
      avatar.setDesktop?.(desktop);
    }
  }

  // -- backend events -------------------------------------------------------------------
  store.on("mascot_config", (e) => {
    resize(Number(e.width) || width, Number(e.height) || height);
  });
  store.on("mascot_state", (e) => {
    desktop = {
      mode: (e.mode as BodyMode) ?? "stand",
      facing: Number(e.facing) || 0,
      vx: Number(e.vx) || 0,
      vy: Number(e.vy) || 0,
      look: Array.isArray(e.look) ? (e.look as [number, number]) : null,
    };
    avatar?.setDesktop?.(desktop);
  });
  store.on("mascot_event", (e) => {
    const name = e.name as Reaction;
    if (REACTIONS.has(name)) avatar?.react?.(name);
  });
  store.on("wake_detected", () => avatar?.react?.("wake"));
  store.on("assistant_reply", (e: BusEvent) => {
    const text = typeof e.text === "string" ? e.text : "";
    if (text) {
      bubble.text = replyOpen ? `${bubble.text} ${text}` : text;
      replyOpen = true;
      if (WAVE_WORDS.test(text)) avatar?.react?.("wave");
    }
    if (e.final) replyOpen = false;
    bubble.until = performance.now() + 2500 + bubble.text.length * 55;
  });
  store.on("speech_recognized", () => {
    bubble.until = 0;
    replyOpen = false;
  });
  let wasConnected = false;
  store.subscribe(() => {
    if (store.connected && !wasConnected) store.send({ type: "mascot_hello" });
    wasConnected = store.connected;
    void mountAvatar();
  });

  // -- frame loop -----------------------------------------------------------------------
  const start = performance.now();
  let last = start;
  let lastLayoutAt = 0;
  const loop = (now: number): void => {
    requestAnimationFrame(loop);
    if (now - last < 1000 / FPS - 2) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (!avatar) return;
    avatar.update({
      state: store.state,
      emotion: store.emotion,
      outputLevel: store.outputLevel,
      inputLevel: store.inputLevel,
      time: (now - start) / 1000,
      dt,
      mood: store.mood,
    });
    // compose right after rendering: the WebGL buffer is only valid within this task
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(canvas, 0, 0, width, height);
    if (now - lastLayoutAt > 400) {
      lastLayoutAt = now;
      layout = avatar.layout?.() ?? null;
      sendLayout();
    }
    drawBubble(now, dt);
    sendFrame();
  };
  requestAnimationFrame(loop);

  function sendLayout(): void {
    if (!layout) return;
    const scaleX = width / (canvas.width || width);
    const scaleY = height / (canvas.height || height);
    const message = {
      type: "mascot_layout",
      feet: Math.round(layout.feet * scaleY),
      seat: Math.round(layout.seat * scaleY),
      center: Math.round(layout.center * scaleX),
      head: Math.round(layout.head * scaleY),
    };
    const key = JSON.stringify(message);
    if (key !== sentLayout) {
      sentLayout = key;
      store.send(message);
    }
  }

  function sendFrame(): void {
    const pixels = ctx.getImageData(0, 0, width, height).data;
    const buffer = new ArrayBuffer(8 + pixels.length);
    const header = new DataView(buffer);
    header.setUint32(0, width, true);
    header.setUint32(4, height, true);
    new Uint8Array(buffer, 8).set(pixels);
    store.sendBinary(buffer, pixels.length * 2);
  }

  function drawBubble(now: number, dt: number): void {
    const thinking = store.state === "thinking";
    const visible = thinking || (bubble.text !== "" && now < bubble.until);
    bubble.alpha += ((visible ? 1 : 0) - bubble.alpha) * Math.min(1, dt * 8);
    if (bubble.alpha < 0.02) {
      if (!visible) bubble.text = "";
      return;
    }
    const font = Math.max(12, Math.round(height * 0.028));
    ctx.font = `500 ${font}px Inter, "Segoe UI", sans-serif`;
    const maxWidth = width * 0.9 - font * 1.4;
    const lines = thinking
      ? [".".repeat(1 + (Math.floor(now / 350) % 3))]
      : wrap(bubble.text, maxWidth).slice(-4);
    const textWidth = Math.max(...lines.map((l) => ctx.measureText(l).width), font * 1.5);
    const padX = font * 0.7;
    const padY = font * 0.5;
    const lineHeight = font * 1.3;
    const w = textWidth + padX * 2;
    const h = lines.length * lineHeight + padY * 2;
    const headTop = layout?.head ?? height * 0.26;
    const centerX = layout?.center ?? width / 2;
    const bottom = Math.max(h + 4, headTop - font * 1.3);
    const x = Math.min(width - w - 2, Math.max(2, centerX - w / 2));
    const y = bottom - h;

    ctx.save();
    ctx.globalAlpha = bubble.alpha;
    ctx.fillStyle = "rgba(6, 13, 22, 0.9)";
    ctx.strokeStyle = "rgba(84, 214, 255, 0.85)";
    ctx.lineWidth = Math.max(1.5, font * 0.09);
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, font * 0.6);
    const tail = Math.min(Math.max(centerX, x + font), x + w - font);
    ctx.moveTo(tail - font * 0.4, bottom);
    ctx.lineTo(tail, bottom + font * 0.5);
    ctx.lineTo(tail + font * 0.4, bottom);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#e8f6ff";
    ctx.textBaseline = "top";
    lines.forEach((line, i) => ctx.fillText(line, x + padX, y + padY + i * lineHeight));
    ctx.restore();
  }

  function wrap(text: string, maxWidth: number): string[] {
    const lines: string[] = [];
    let line = "";
    for (const word of text.split(/\s+/)) {
      const candidate = line ? `${line} ${word}` : word;
      if (ctx.measureText(candidate).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else line = candidate;
    }
    if (line) lines.push(line);
    return lines;
  }
}
