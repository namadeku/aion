// 3D VRM avatar (three.js + @pixiv/three-vrm): lip sync, blinking, body motion, emotions.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM, type VRMSpringBoneJoint } from "@pixiv/three-vrm";
import { avatarUrl, debugMode } from "../api";
import type { Emotion } from "../types";
import { Motion } from "./motion";
import type { Avatar, AvatarInput, AvatarOptions, DesktopInput, Layout, Reaction } from "./types";

const EMOTION_EXPRESSION: Partial<Record<Emotion, string>> = {
  joy: "happy",
  surprise: "surprised",
  sad: "sad",
  angry: "angry",
  thinking: "relaxed",
};
const EMOTIONS = ["happy", "surprised", "sad", "angry", "relaxed"];
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
  private lastLevel = 0;
  private nextBlink = 2;
  private blink = 0;
  private doubleBlink = false;
  private emotionWeights = new Map<string, number>();
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
  /** Each spring joint's own gravity, before the window's motion is added to it. */
  private springGravity = new Map<VRMSpringBoneJoint, THREE.Vector3>();
  private windowVelocity = new THREE.Vector3();
  private inertia = new THREE.Vector3();

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
    for (const joint of vrm.springBoneManager?.joints ?? []) {
      const { gravityDir, gravityPower } = joint.settings;
      this.springGravity.set(joint, gravityDir.clone().multiplyScalar(gravityPower));
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
    return new VrmAvatar(canvas, vrm, options);
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

  update({ state, emotion, outputLevel, time, dt, mood }: AvatarInput): void {
    const vrm = this.vrm;
    const expressions = vrm.expressionManager;
    if (mood) this.motion.mood = mood;
    this.motion.update(state, emotion, outputLevel, time, dt);
    const face = this.motion.face;

    // lip sync: openness follows output loudness, the vowel changes on every syllable
    const target = state === "speaking" ? Math.min(1, outputLevel * 1.6) : 0;
    this.mouth += (target - this.mouth) * Math.min(1, dt * (target > this.mouth ? 25 : 12));
    this.nextSyllable -= dt;
    const onset = outputLevel - this.lastLevel > 0.08;
    if (state === "speaking" && (onset || this.nextSyllable <= 0)) {
      this.vowel = pickVowel(this.vowel);
      this.nextSyllable = 0.09 + Math.random() * 0.12;
    }
    this.lastLevel = outputLevel;
    for (const [name] of VOWELS) {
      const goal = name === this.vowel ? this.mouth : 0;
      const current = this.vowelWeights.get(name) ?? 0;
      const next = current + (goal - current) * Math.min(1, dt * 18);
      this.vowelWeights.set(name, next);
      const extra = name === "aa" ? (face.aa ?? 0) : 0;
      expressions?.setValue(name, Math.min(1, Math.max(next * (name === "aa" ? 1 : 0.8), extra)));
    }

    // blinking every 2-6 s, sometimes twice; also on emotion changes
    if (emotion !== this.lastEmotion) {
      this.lastEmotion = emotion;
      this.nextBlink = Math.min(this.nextBlink, 0.05);
    }
    this.nextBlink -= dt;
    if (this.nextBlink <= 0) {
      this.blink = 1;
      if (this.doubleBlink) {
        this.doubleBlink = false;
        this.nextBlink = 2 + Math.random() * 4;
      } else if (Math.random() < 0.2) {
        this.doubleBlink = true;
        this.nextBlink = 0.22;
      } else {
        this.nextBlink = 2 + Math.random() * 4;
      }
    }
    this.blink = Math.max(0, this.blink - dt * 7);
    const blink = Math.max(Math.sin(this.blink * Math.PI), face.eyesClosed ?? 0);
    expressions?.setValue("blink", Math.min(1, blink));

    // idle micro-expression: an occasional soft smile
    this.nextSmile -= dt;
    if (this.nextSmile <= 0) {
      this.smileGoal = this.smileGoal > 0 ? 0 : 0.25 + Math.random() * 0.2;
      this.nextSmile = this.smileGoal > 0 ? 2 + Math.random() * 2 : 8 + Math.random() * 12;
    }
    const smileTarget = emotion === "neutral" && state !== "speaking" ? this.smileGoal : 0;
    this.smile += (smileTarget - this.smile) * Math.min(1, dt * 2);

    // emotions fade in/out; body actions add their own expression hints
    const wanted = EMOTION_EXPRESSION[emotion];
    const hints: Record<string, number> = {
      happy: Math.max(this.smile, face.happy ?? 0),
      surprised: face.surprised ?? 0,
      sad: face.sad ?? 0,
    };
    for (const name of EMOTIONS) {
      const current = this.emotionWeights.get(name) ?? 0;
      let goal = name === wanted ? (name === "relaxed" ? 0.4 : 0.8) : 0;
      goal = Math.max(goal, hints[name] ?? 0);
      const next = current + (goal - current) * Math.min(1, dt * 4);
      this.emotionWeights.set(name, next);
      expressions?.setValue(name, next);
    }

    this.applyInertia(dt);
    vrm.update(dt);
    this.renderer.render(this.scene, this.camera);
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
    // screen y points down, the scene's y up
    const velocity = new THREE.Vector3(
      (this.desktop?.vx ?? 0) * metres,
      -(this.desktop?.vy ?? 0) * metres,
      0,
    );
    const accel = velocity.clone().sub(this.windowVelocity).divideScalar(dt);
    this.windowVelocity.copy(velocity);
    const force = accel.multiplyScalar(-INERTIA_ACCEL).addScaledVector(velocity, -INERTIA_WIND);
    force.clampLength(0, INERTIA_MAX);
    force.y = Math.min(force.y, INERTIA_LIFT_MAX);
    this.inertia.lerp(force, Math.min(1, dt * 10));
    const gravity = new THREE.Vector3();
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
    this.resize.disconnect();
    this.motion.dispose();
    VRMUtils.deepDispose(this.vrm.scene);
    this.renderer.dispose();
  }
}
