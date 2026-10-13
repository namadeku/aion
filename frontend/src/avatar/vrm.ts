// 3D VRM avatar (three.js + @pixiv/three-vrm): lip sync, blinking, body motion, emotions.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM, type VRMSpringBoneJoint } from "@pixiv/three-vrm";
import { avatarUrl, debugMode } from "../api";
import type { Emotion } from "../types";
import type { ClipPlan } from "./actions";
import { Motion } from "./motion";
import { approach, clampDt, envelope, follow, smoothstep, voiceOpenness } from "./smooth";
import type { Avatar, AvatarInput, AvatarOptions, DesktopInput, Layout, Reaction } from "./types";

const EMOTION_EXPRESSION: Partial<Record<Emotion, string>> = {
  joy: "happy",
  surprise: "surprised",
  sad: "sad",
  angry: "angry",
  thinking: "relaxed",
};
const EMOTIONS = ["happy", "surprised", "sad", "angry", "relaxed"];
/**
 * Mouth opening scale in the window and on the desktop (bigger: the mascot is small). The
 * voice level is gated and compressed first (see `voiceOpenness`): a plain linear gain
 * pinned the mouth wide open on every loud word.
 */
const MOUTH_GAIN = 0.9;
const MOUTH_GAIN_DESKTOP = 1;
/** Lip sync envelope, 1/s: the jaw opens fast and closes a bit slower (no flicker). */
const MOUTH_ATTACK = 32;
const MOUTH_RELEASE = 14;
/** How fast one vowel shape crossfades into the next, 1/s. */
const VOWEL_BLEND = 22;
/** How much stronger the voice moves head and shoulders of the desktop mascot (see Motion). */
const VOICE_GAIN_DESKTOP = 1.8;
/**
 * Spring bones (hair, cloth) were authored for 60 Hz updates and their Verlet step is not
 * frame-rate independent: they are integrated in steps of at most this size, with drag and
 * forces rescaled, so hair moves the same at 30 fps (desktop) and on 144 Hz monitors.
 */
const SPRING_BONE_STEP = 1 / 60;
/** Blink: eyelids close quickly and open slower. */
const BLINK_CLOSE = 0.06;
const BLINK_HOLD = 0.03;
const BLINK_OPEN = 0.13;
/** Hair and cloth lag behind the moving window: how much its acceleration and speed push them. */
const INERTIA_ACCEL = 0.03;
const INERTIA_WIND = 0.15;
const INERTIA_MAX = 1.5;
/** Falling wind lifts hair a bit; full strength would flip the skirt and stand hair on end. */
const INERTIA_LIFT_MAX = 0.3;
/** Mouth shapes for lip sync and how often each one comes up. */
const VOWELS: [string, number][] = [
  ["aa", 0.4],
  ["ih", 0.2],
  ["ou", 0.15],
  ["ee", 0.12],
  ["oh", 0.13],
];

function pickVowel(previous: string): string {
  for (;;) {
    let r = Math.random();
    for (const [name, p] of VOWELS) {
      r -= p;
      if (r <= 0) {
        if (name !== previous) return name;
        break;
      }
    }
  }
}

export class VrmAvatar implements Avatar {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private resize: ResizeObserver;
  private mouth = 0;
  private vowel = "aa";
  private vowelWeights = new Map<string, number>();
  private nextSyllable = 0;
  private lastOpen = 0;
  private nextBlink = 2;
  /** Time since the current blink started; negative when no blink is running. */
  private blinkT = -1;
  private doubleBlink = false;
  private emotionWeights = new Map<string, number>();
  private hints: Record<string, number> = { happy: 0, surprised: 0, sad: 0 };
  private lastEmotion: Emotion = "neutral";
  /**
   * VRM 0.x normalized bones have X and Z axes flipped relative to VRM 1.0 (the same
   * correction three-vrm applies when retargeting animations), so rotations about X/Z
   * are multiplied by this sign.
   */
  private axis: 1 | -1;
  private motion: Motion;
  private smile = 0;
  private smileGoal = 0;
  private nextSmile = 8;
  private fullBody: boolean;
  private height = 1.6;
  private desktop: DesktopInput | null = null;
  private disposed = false;
  /** Each spring joint's own gravity, before the window's motion is added to it. */
  private springGravity = new Map<VRMSpringBoneJoint, THREE.Vector3>();
  /** Each spring joint's authored drag (per 60 Hz step). */
  private springDrag = new Map<VRMSpringBoneJoint, number>();
  private springStep = SPRING_BONE_STEP;
  private windowVelocity = new THREE.Vector3();
  private inertia = new THREE.Vector3();
  private velocity = new THREE.Vector3();
  private force = new THREE.Vector3();
  private gravity = new THREE.Vector3();

  private constructor(
    private canvas: HTMLCanvasElement,
    private vrm: VRM,
    options: AvatarOptions,
  ) {
    this.fullBody = options.fullBody ?? false;
    this.axis = vrm.meta?.metaVersion === "0" ? -1 : 1;
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    this.renderer.setClearColor(0x000000, 0);
    // the desktop mascot needs the drawing buffer at full device resolution
    const dpr = window.devicePixelRatio || 1;
    this.renderer.setPixelRatio(this.fullBody ? dpr : Math.min(dpr, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(0.6, 1.4, 1.6);
    const rim = new THREE.DirectionalLight(0x54d6ff, 1.2);
    rim.position.set(-1.2, 1.6, -1.4);
    this.scene.add(key, rim, new THREE.AmbientLight(0xb8dcff, 0.35));
    this.scene.add(vrm.scene);

    const head = vrm.humanoid.getNormalizedBoneNode("head");
    const headPos = new THREE.Vector3(0, 1.4, 0);
    head?.getWorldPosition(headPos);
    this.height = headPos.y + 0.14;
    if (this.fullBody) {
      // whole figure, soles at the bottom edge; the top quarter stays free for a speech bubble
      this.camera = new THREE.PerspectiveCamera(18, 1, 0.1, 40);
    } else {
      // upper body around the head
      this.camera = new THREE.PerspectiveCamera(26, 1, 0.1, 20);
      this.camera.position.set(0, headPos.y - 0.05, 2.3);
      this.camera.lookAt(0, headPos.y - 0.22, 0);
    }
    this.scene.add(this.camera);
    this.motion = new Motion(vrm, this.axis, this.camera);
    if (this.fullBody) this.motion.voiceGain = VOICE_GAIN_DESKTOP;
    for (const joint of vrm.springBoneManager?.joints ?? []) {
      const { gravityDir, gravityPower } = joint.settings;
      this.springGravity.set(joint, gravityDir.clone().multiplyScalar(gravityPower));
      this.springDrag.set(joint, joint.settings.dragForce);
    }
    if (debugMode) {
      const debug = window as unknown as { aionMotion: Motion; aionAvatar: VrmAvatar };
      debug.aionMotion = this.motion;
      debug.aionAvatar = this;
    }

    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(canvas);
    this.fit();
  }

  static async create(
    canvas: HTMLCanvasElement,
    path: string,
    options: AvatarOptions = {},
    clips: ClipPlan | null = null,
  ): Promise<VrmAvatar> {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const file = path.split(/[\\/]/).pop() ?? path;
    const gltf = await loader.loadAsync(avatarUrl(file));
    const vrm = gltf.userData.vrm as VRM | undefined;
    if (!vrm) throw new Error("Файл не похож на VRM-модель");
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.combineMorphs(vrm);
    VRMUtils.rotateVRM0(vrm); // VRM 0.x models face -Z
    vrm.scene.traverse((obj) => (obj.frustumCulled = false));
    const avatar = new VrmAvatar(canvas, vrm, options);
    if (clips && (clips.idle.length || clips.actions.size)) void avatar.loadClips(clips);
    return avatar;
  }

  /** Optional mocap layer: .vrma clips next to the model (see clips.ts). */
  private async loadClips(plan: ClipPlan): Promise<void> {
    const { ClipLayer } = await import("./clips");
    const clips = await ClipLayer.load(this.vrm, plan, avatarUrl);
    if (this.disposed) clips?.dispose();
    else this.motion.clips = clips;
  }

  private fit(): void {
    const { width, height } = this.canvas.getBoundingClientRect();
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    if (this.fullBody) {
      const visible = (this.height + 0.04) / 0.74;
      const distance = visible / 2 / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
      const centerY = -0.03 + visible / 2;
      this.motion.frameMetres = visible;
      this.camera.position.set(0, centerY, distance);
      this.camera.lookAt(0, centerY, 0);
    }
    this.camera.updateProjectionMatrix();
  }

  update(input: AvatarInput): void {
    const { state, emotion, outputLevel, inputLevel, time, mood } = input;
    // a stalled frame (GC, hidden tab, busy renderer) must not jolt the body or the hair
    const dt = clampDt(input.dt);
    const vrm = this.vrm;
    const expressions = vrm.expressionManager;
    if (mood) this.motion.mood = mood;
    this.motion.inputLevel = inputLevel;
    this.motion.update(state, emotion, outputLevel, time, dt);
    const face = this.motion.face;

    this.updateMouth(state === "speaking" ? outputLevel : 0, dt);
    for (const [name] of VOWELS) {
      const goal = name === this.vowel ? this.mouth : 0;
      const next = approach(this.vowelWeights.get(name) ?? 0, goal, VOWEL_BLEND, dt);
      this.vowelWeights.set(name, next);
      const extra = name === "aa" ? (face.aa ?? 0) : 0;
      expressions?.setValue(name, Math.min(1, Math.max(next * (name === "aa" ? 1 : 0.8), extra)));
    }

    const blink = Math.max(this.updateBlink(emotion, dt), face.eyesClosed ?? 0);
    expressions?.setValue("blink", Math.min(1, blink));

    // idle micro-expression: an occasional soft smile
    this.nextSmile -= dt;
    if (this.nextSmile <= 0) {
      this.smileGoal = this.smileGoal > 0 ? 0 : 0.25 + Math.random() * 0.2;
      this.nextSmile = this.smileGoal > 0 ? 2 + Math.random() * 2 : 8 + Math.random() * 12;
    }
    const smileTarget = emotion === "neutral" && state !== "speaking" ? this.smileGoal : 0;
    this.smile = approach(this.smile, smileTarget, 2, dt);

    // emotions fade in/out; body actions add their own expression hints
    const wanted = EMOTION_EXPRESSION[emotion];
    const hints = this.hints;
    hints.happy = Math.max(this.smile, face.happy ?? 0);
    hints.surprised = face.surprised ?? 0;
    hints.sad = face.sad ?? 0;
    for (const name of EMOTIONS) {
      let goal = name === wanted ? (name === "relaxed" ? 0.4 : 0.8) : 0;
      goal = Math.max(goal, hints[name] ?? 0);
      const next = approach(this.emotionWeights.get(name) ?? 0, goal, 4, dt);
      this.emotionWeights.set(name, next);
      expressions?.setValue(name, next);
    }

    this.applyInertia(dt);
    this.updateVrm(dt);
    this.renderer.render(this.scene, this.camera);
  }

  /**
   * Lip sync: the jaw follows the voice envelope (gated, fast attack, slower release) and
   * the vowel shape changes on syllable onsets, crossfading instead of snapping.
   */
  private updateMouth(level: number, dt: number): void {
    const gain = this.fullBody ? MOUTH_GAIN_DESKTOP : MOUTH_GAIN;
    const target = voiceOpenness(level) * gain;
    this.mouth = envelope(this.mouth, target, MOUTH_ATTACK, MOUTH_RELEASE, dt);
    this.nextSyllable -= dt;
    // a new syllable: the opening rises again after a dip (levels arrive at ~30 Hz)
    const onset = target - this.lastOpen > 0.12 && this.nextSyllable < 0.06;
    if (target > 0.05 && (onset || this.nextSyllable <= 0)) {
      this.vowel = pickVowel(this.vowel);
      this.nextSyllable = 0.1 + Math.random() * 0.12;
    }
    this.lastOpen = target;
  }

  /**
   * Blinks every 1.5–6 s (skewed to short gaps, like people), sometimes a double blink;
   * also on emotion changes and often when the gaze jumps far.
   */
  private updateBlink(emotion: Emotion, dt: number): number {
    if (emotion !== this.lastEmotion) {
      this.lastEmotion = emotion;
      this.nextBlink = Math.min(this.nextBlink, 0.05);
    }
    const shift = this.motion.takeGazeShift();
    if (shift > 0.3 && this.blinkT < 0 && Math.random() < 0.7) this.nextBlink = 0;
    this.nextBlink -= dt;
    if (this.nextBlink <= 0 && this.blinkT < 0) {
      this.blinkT = 0;
      if (this.doubleBlink) {
        this.doubleBlink = false;
        this.nextBlink = 1.5 + Math.random() * Math.random() * 5;
      } else if (Math.random() < 0.15) {
        this.doubleBlink = true;
        this.nextBlink = BLINK_CLOSE + BLINK_HOLD + BLINK_OPEN + 0.05;
      } else {
        this.nextBlink = 1.5 + Math.random() * Math.random() * 5;
      }
    }
    if (this.blinkT < 0) return 0;
    const t = this.blinkT;
    this.blinkT += dt;
    if (this.blinkT > BLINK_CLOSE + BLINK_HOLD + BLINK_OPEN) this.blinkT = -1;
    if (t < BLINK_CLOSE) return smoothstep(t / BLINK_CLOSE);
    if (t < BLINK_CLOSE + BLINK_HOLD) return 1;
    return 1 - smoothstep((t - BLINK_CLOSE - BLINK_HOLD) / BLINK_OPEN);
  }

  /**
   * `vrm.update(dt)`, with the spring bones integrated in fixed-size substeps. Their Verlet
   * step carries `1 - dragForce` of the velocity per call and adds forces × delta as a
   * displacement, so for a step h (vs the authored 1/60 s) drag becomes 1 − (1 − d)^(h·60)
   * and forces get delta = h²·60: the same motion at any frame rate.
   */
  private updateVrm(dt: number): void {
    const vrm = this.vrm;
    vrm.humanoid.update();
    vrm.lookAt?.update(dt);
    vrm.expressionManager?.update();
    vrm.nodeConstraintManager?.update();
    const springs = vrm.springBoneManager;
    if (springs && dt > 0) {
      const steps = Math.ceil(dt / SPRING_BONE_STEP - 1e-6);
      const h = dt / steps;
      if (Math.abs(h - this.springStep) > 1e-5) {
        this.springStep = h;
        const ratio = h / SPRING_BONE_STEP;
        for (const [joint, drag] of this.springDrag) {
          joint.settings.dragForce = 1 - Math.pow(1 - drag, ratio);
        }
      }
      const delta = (h * h) / SPRING_BONE_STEP;
      for (let i = 0; i < steps; i++) springs.update(delta);
    }
    for (const material of vrm.materials ?? []) {
      (material as { update?: (delta: number) => void }).update?.(dt);
    }
  }

  react(event: Reaction): void {
    if (event === "wake") {
      this.motion.wake();
      this.smileGoal = 0.45;
      this.nextSmile = 1.5;
    } else {
      this.motion.play(event);
    }
  }

  play(action: string): void {
    this.motion.play(action);
  }

  setDesktop(input: DesktopInput | null): void {
    this.desktop = input;
    this.motion.setDesktop(input);
  }

  /**
   * The desktop window moves on screen while the scene stands still, so spring bones would
   * never feel it. Feed its motion in as extra gravity: hair and skirt stream against the
   * movement and swing on starts and stops.
   */
  private applyInertia(dt: number): void {
    if (!this.springGravity.size || dt <= 0) return;
    const metres = this.motion.frameMetres;
    // screen y points down, the scene's y up; speeds arrive at ~15 Hz in steps, so smooth
    // them before differentiating (a step would read as a huge one-frame acceleration)
    const velocity = this.velocity;
    velocity.set((this.desktop?.vx ?? 0) * metres, -(this.desktop?.vy ?? 0) * metres, 0);
    const previous = this.windowVelocity;
    const force = this.force.copy(previous);
    previous.lerp(velocity, follow(14, dt));
    // force = −accel · INERTIA_ACCEL − velocity · INERTIA_WIND
    force
      .sub(previous)
      .multiplyScalar(INERTIA_ACCEL / dt)
      .addScaledVector(previous, -INERTIA_WIND);
    force.clampLength(0, INERTIA_MAX);
    force.y = Math.min(force.y, INERTIA_LIFT_MAX);
    this.inertia.lerp(force, follow(10, dt));
    const gravity = this.gravity;
    for (const [joint, base] of this.springGravity) {
      gravity.copy(base).add(this.inertia);
      const power = gravity.length();
      joint.settings.gravityPower = power;
      if (power > 1e-5) joint.settings.gravityDir.copy(gravity).divideScalar(power);
    }
  }

  layout(): Layout | null {
    if (!this.fullBody) return null;
    const { width, height } = this.renderer.domElement;
    const project = (v: THREE.Vector3): [number, number] => {
      const p = v.clone().project(this.camera);
      return [((p.x + 1) / 2) * width, ((1 - p.y) / 2) * height];
    };
    const hips = new THREE.Vector3(0, this.height * 0.53, 0);
    this.vrm.humanoid.getNormalizedBoneNode("hips")?.getWorldPosition(hips);
    const [center] = project(new THREE.Vector3(hips.x, 0, 0));
    const [, feet] = project(new THREE.Vector3(0, 0, 0));
    // the seat is a little below the hip joint; the rest height keeps it steady while hopping
    const [, seat] = project(new THREE.Vector3(0, this.height * 0.5, 0));
    const [, head] = project(new THREE.Vector3(0, this.height, 0));
    return { feet, seat, center, head };
  }

  dispose(): void {
    this.disposed = true;
    this.resize.disconnect();
    this.motion.clips?.dispose();
    this.motion.dispose();
    VRMUtils.deepDispose(this.vrm.scene);
    this.renderer.dispose();
  }
}
