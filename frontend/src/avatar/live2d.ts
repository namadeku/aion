// Live2D avatar (pixi.js v6 + pixi-live2d-display, Cubism 4 models: *.model3.json).
//
// The proprietary Cubism Core runtime is not bundled (Live2D license): put
// live2dcubismcore.min.js next to the model file, it is loaded from there.

import { avatarUrl } from "../api";
import type { Emotion } from "../types";
import type { Avatar, AvatarInput, AvatarOptions, DesktopInput, Layout, Reaction } from "./types";

type Pixi = typeof import("pixi.js");
type Live2DModelType = import("pixi-live2d-display/cubism4").Live2DModel;

const CORE_FILE = "live2dcubismcore.min.js";

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () =>
      reject(new Error(`Не найден ${CORE_FILE} рядом с моделью (Cubism SDK for Web)`));
    document.head.append(el);
  });
}

export class Live2DAvatar implements Avatar {
  private resize: ResizeObserver;
  private mouth = 0;
  private emotion: Emotion = "neutral";
  private look = { x: 0, y: 0 };
  private lookGoal = { x: 0, y: 0 };
  private nextGlance = 2;
  private nod = 0;
  private bounce = 0;
  private desktop: DesktopInput | null = null;

  private constructor(
    private canvas: HTMLCanvasElement,
    private app: InstanceType<Pixi["Application"]>,
    private model: Live2DModelType,
    private fullBody: boolean,
  ) {
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(canvas);
    this.fit();
  }

  static async create(
    canvas: HTMLCanvasElement,
    path: string,
    options: AvatarOptions = {},
  ): Promise<Live2DAvatar> {
    const w = window as unknown as { Live2DCubismCore?: unknown; PIXI?: Pixi };
    if (!w.Live2DCubismCore) await loadScript(avatarUrl(CORE_FILE));
    const PIXI = await import("pixi.js");
    w.PIXI = PIXI; // pixi-live2d-display looks for the global
    const { Live2DModel } = await import("pixi-live2d-display/cubism4");
    Live2DModel.registerTicker(PIXI.Ticker);

    const app = new PIXI.Application({
      view: canvas,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
    });
    const file = path.split(/[\\/]/).pop() ?? path;
    // relative texture/motion paths inside model3.json resolve against this URL
    const model = await Live2DModel.from(avatarUrl(file), { autoInteract: false });
    app.stage.addChild(model as unknown as InstanceType<Pixi["DisplayObject"]>);
    return new Live2DAvatar(canvas, app, model, options.fullBody ?? false);
  }

  private fit(): void {
    const { width, height } = this.canvas.getBoundingClientRect();
    if (!width || !height) return;
    this.app.renderer.resize(width, height);
    const scale =
      Math.min(width / this.model.width, (height * 1.6) / this.model.height) * 0.95 || 1;
    this.model.scale.set(this.model.scale.x * scale);
    this.model.x = (width - this.model.width) / 2;
    // desktop mascot: stand on the bottom edge, room for a speech bubble on top
    this.model.y = this.fullBody ? height - this.model.height : height * 0.05;
  }

  update({ state, emotion, outputLevel, time, dt }: AvatarInput): void {
    const core = this.model.internalModel.coreModel as unknown as {
      setParameterValueById(id: string, value: number): void;
    };
    const set = (id: string, value: number) => {
      try {
        core.setParameterValueById(id, value);
      } catch {
        /* the model has no such parameter */
      }
    };
    const k = (speed: number) => Math.min(1, dt * speed);
    const target = state === "speaking" ? Math.min(1, outputLevel * 1.8) : 0;
    this.mouth += (target - this.mouth) * k(20);
    set("ParamMouthOpenY", this.mouth);
    set("ParamMouthForm", emotion === "joy" ? 1 : emotion === "sad" ? -0.6 : 0.2);

    // gaze: the cursor on the desktop, glances around in idle, up and aside when thinking
    this.nextGlance -= dt;
    const cursor = this.desktop?.look;
    if (state === "thinking") this.lookGoal = { x: 0.6, y: 0.6 };
    else if (cursor) this.lookGoal = { x: cursor[0], y: cursor[1] };
    else if (state !== "idle") this.lookGoal = { x: 0, y: 0 };
    else if (this.nextGlance <= 0) {
      const away = Math.random() < 0.4;
      this.lookGoal = away
        ? { x: (Math.random() - 0.5) * 1.4, y: (Math.random() - 0.4) * 0.6 }
        : { x: 0, y: 0 };
      this.nextGlance = 2 + Math.random() * 4;
    }
    this.look.x += (this.lookGoal.x - this.look.x) * k(4);
    this.look.y += (this.lookGoal.y - this.look.y) * k(4);
    set("ParamEyeBallX", this.look.x);
    set("ParamEyeBallY", this.look.y);

    // head and body: nods with speech, sway in idle, tilt when listening or thinking
    const speaking = state === "speaking" ? 1 : 0;
    this.nod += (outputLevel * speaking - this.nod) * k(outputLevel > this.nod ? 10 : 4);
    this.bounce = Math.max(0, this.bounce - dt * 1.5);
    const tilt = state === "thinking" ? 10 : state === "listening" ? -7 : 0;
    set("ParamAngleX", this.look.x * 18 + Math.sin(time * 0.37) * 4);
    set("ParamAngleY", this.look.y * 12 - this.nod * 14 + Math.sin(time * 0.9) * 2);
    set("ParamAngleZ", tilt + Math.sin(time * 0.7) * 3 + Math.sin(time * 2.1) * 3 * speaking);
    set("ParamBodyAngleX", Math.sin(time * 0.45) * 4 + (this.desktop?.facing ?? 0) * 8);
    set("ParamBodyAngleZ", Math.sin(time * 0.6) * 2 + this.bounce * Math.sin(time * 14) * 6);
    set("ParamBreath", (Math.sin(time * 1.7) + 1) / 2);

    if (emotion !== this.emotion) {
      this.emotion = emotion;
      if (emotion === "joy" || emotion === "surprise") this.bounce = 1;
      // models name expressions differently; try the emotion name itself
      if (emotion !== "neutral") void this.model.expression(emotion).catch(() => undefined);
      else void this.model.expression().catch(() => undefined);
    }
  }

  react(event: Reaction): void {
    this.bounce = 1;
    if (event === "wake") this.lookGoal = { x: 0, y: 0 };
    // models that ship tap motions play them on pokes and pats
    if (event === "poke" || event === "pat") void this.model.motion("Tap").catch(() => undefined);
  }

  setDesktop(input: DesktopInput | null): void {
    this.desktop = input;
  }

  layout(): Layout | null {
    if (!this.fullBody) return null;
    const { width, height } = this.canvas;
    const top = (height * this.model.y) / (this.canvas.clientHeight || height);
    return { feet: height, seat: height * 0.72, center: width / 2, head: Math.max(0, top) };
  }

  dispose(): void {
    this.resize.disconnect();
    this.app.destroy(false, { children: true });
  }
}
