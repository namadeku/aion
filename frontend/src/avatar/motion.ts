// Procedural body animation for VRM avatars: idle life, state poses, speaking gestures, emotions,
// one-off actions (wave, stretch, hop…) and desktop-mode body modes (walk, sit, dangle, fall).
//
// Rotations are written in VRM 1.0 normalized-bone convention (model faces +Z, arms along ±X
// in T-pose) and converted for VRM 0.x by flipping X and Z (see `axis`).
// Signs used below: upperArm/upperLeg X < 0 swings the limb forward; lowerLeg X > 0 bends the
// knee; leftUpperArm Z > 0 / rightUpperArm Z < 0 raises the arm; head Y > 0 turns to screen right.

import * as THREE from "three";
import type { VRM, VRMHumanBoneName } from "@pixiv/three-vrm";
import type { Emotion, Mood, State } from "../types";
import type { BodyMode, DesktopInput } from "./types";

type Rot = [number, number, number];
type Pose = Partial<Record<VRMHumanBoneName, Rot>>;

/** Facial hints from the body animation, merged by the avatar ("at least" values). */
export interface Face {
  eyesClosed?: number;
  happy?: number;
  surprised?: number;
  sad?: number;
  aa?: number;
}

const FINGERS = ["Index", "Middle", "Ring", "Little"] as const;
const SEGMENTS = ["Proximal", "Intermediate", "Distal"] as const;

/** Relaxed standing pose (applied on top of the T-pose). */
function basePose(): Pose {
  const pose: Pose = {
    leftUpperArm: [0, 0, -1.22],
    rightUpperArm: [0, 0, 1.22],
    leftLowerArm: [0, -0.3, 0],
    rightLowerArm: [0, 0.3, 0],
    leftHand: [0, 0, -0.12],
    rightHand: [0, 0, 0.12],
  };
  // gently curled fingers instead of a flat palm
  for (const finger of FINGERS) {
    SEGMENTS.forEach((segment, i) => {
      const curl = 0.18 + i * 0.12;
      pose[`left${finger}${segment}` as VRMHumanBoneName] = [0, 0, -curl];
      pose[`right${finger}${segment}` as VRMHumanBoneName] = [0, 0, curl];
    });
  }
  return pose;
}

/** Offsets added to the base pose for each assistant state. */
const STATE_POSES: Record<State, Pose> = {
  idle: {},
  listening: {
    spine: [0.05, 0, 0],
    neck: [0.04, 0, 0.1],
    head: [0.04, 0, 0.06],
  },
  thinking: {
    // right hand raised to the chin, head tilted, looking up
    rightUpperArm: [-0.85, 0.25, -0.15],
    rightLowerArm: [0, 2.15, 0],
    rightHand: [0, 0, 0.35],
    leftUpperArm: [0.05, 0, 0.08],
    leftLowerArm: [0, -0.45, 0],
    neck: [-0.06, 0.08, -0.08],
    head: [-0.08, 0.06, -0.06],
    spine: [0.03, 0, 0],
  },
  speaking: {
    leftLowerArm: [0, -0.25, 0],
    rightLowerArm: [0, 0.25, 0],
  },
};

const EMOTION_POSES: Partial<Record<Emotion, Pose>> = {
  joy: {
    leftUpperArm: [0, 0, 0.12],
    rightUpperArm: [0, 0, -0.12],
    head: [-0.04, 0, 0.06],
    spine: [-0.03, 0, 0],
  },
  surprise: {
    leftShoulder: [0, 0, 0.12],
    rightShoulder: [0, 0, -0.12],
    leftUpperArm: [0, 0, 0.18],
    rightUpperArm: [0, 0, -0.18],
    head: [-0.1, 0, 0],
    spine: [-0.05, 0, 0],
  },
  sad: {
    head: [0.18, 0, 0.04],
    neck: [0.1, 0, 0],
    spine: [0.08, 0, 0],
    leftShoulder: [0, 0, -0.06],
    rightShoulder: [0, 0, 0.06],
  },
  angry: {
    // arms crossed, chin down
    // (the upper-arm Y twist turns the bent forearms inward so they cross)
    leftUpperArm: [-0.2, -0.9, 0.1],
    rightUpperArm: [-0.25, 0.9, -0.1],
    leftLowerArm: [0, -1.5, 0],
    rightLowerArm: [0, 1.5, 0],
    head: [0.08, 0, 0],
    spine: [-0.03, 0, 0],
  },
};

/** Short speaking gestures, played with an attack/hold/release envelope. */
const GESTURES: Pose[] = [
  // right hand explains, palm up
  { rightUpperArm: [-0.5, 0.15, -0.2], rightLowerArm: [0, 1.15, 0], rightHand: [0, -0.7, 0.25] },
  // left hand explains
  { leftUpperArm: [-0.5, -0.15, 0.2], leftLowerArm: [0, -1.15, 0], leftHand: [0, 0.7, -0.25] },
  // both hands open, palms up
  {
    rightUpperArm: [-0.5, 0.1, -0.3],
    rightLowerArm: [0, 1.0, 0],
    leftUpperArm: [-0.5, -0.1, 0.3],
    leftLowerArm: [0, -1.0, 0],
    rightHand: [0, -0.6, 0.15],
    leftHand: [0, 0.6, -0.15],
  },
  // emphatic nod
  { head: [0.14, 0, 0], neck: [0.07, 0, 0] },
  // small shrug
  {
    leftShoulder: [0, 0, 0.14],
    rightShoulder: [0, 0, -0.14],
    head: [0, 0, 0.1],
    leftUpperArm: [-0.2, 0, 0.15],
    rightUpperArm: [-0.2, 0, -0.15],
    leftLowerArm: [0, -0.7, 0],
    rightLowerArm: [0, 0.7, 0],
  },
  // hand on the chest ("я думаю", "мне кажется")
  {
    rightUpperArm: [-0.45, 0.3, 0.1],
    rightLowerArm: [0, 1.9, 0],
    rightHand: [0, -0.3, 0],
    head: [0.04, 0, 0.05],
  },
  // lean in and point forward with the left hand
  {
    leftUpperArm: [-0.9, -0.1, 0.45],
    leftLowerArm: [0, -0.5, 0],
    spine: [0.05, 0, 0],
    head: [0.03, -0.05, 0],
  },
  // head tilt with a small open hand
  {
    head: [0, 0, -0.12],
    neck: [0, 0, -0.05],
    rightUpperArm: [-0.3, 0, 0],
    rightLowerArm: [0, 0.8, 0],
  },
];

/** A one-off, time-varying animation (wave, stretch, hop…). */
interface Action {
  name: string;
  duration: number;
  t: number;
  pose(t: number): Pose;
  face?(t: number): Face;
  /** Hips lift in metres (hops, squats). */
  lift?(t: number): number;
  /** Gaze offset in camera space. */
  look?(t: number): [number, number] | null;
  attack?: number;
  release?: number;
}

type ActionSpec = Omit<Action, "t">;

const sin = Math.sin;
const bump = (t: number, a: number, b: number): number =>
  t <= a || t >= b ? 0 : sin(((t - a) / (b - a)) * Math.PI);

const ACTIONS: Record<string, () => ActionSpec> = {
  wave: () => ({
    name: "wave",
    duration: 2.4,
    pose: (t) => ({
      rightUpperArm: [-0.15, 0.2, -1.5],
      rightLowerArm: [0, -0.3, -1.3 + sin(t * 10) * 0.35],
      rightHand: [0, 0, -0.15 + sin(t * 10 + 0.6) * 0.15],
      head: [0, 0.05, -0.08],
      spine: [0, 0, -0.03],
    }),
    face: () => ({ happy: 0.6 }),
  }),
  stretch: () => ({
    name: "stretch",
    duration: 3.6,
    attack: 0.9,
    release: 0.9,
    pose: (t) => ({
      leftUpperArm: [-0.2, 0, 2.45],
      rightUpperArm: [-0.2, 0, -2.45],
      leftLowerArm: [0, 0.3, 0.2],
      rightLowerArm: [0, -0.3, -0.2],
      spine: [-0.1, 0, sin(t * 1.6) * 0.05],
      chest: [-0.08, 0, 0],
      head: [-0.15, 0, 0],
    }),
    face: (t) => ({ eyesClosed: 0.85 * bump(t, 0.6, 3.2), aa: 0.35 * bump(t, 0.8, 2.6) }),
  }),
  hairTouch: () => ({
    name: "hairTouch",
    duration: 2.6,
    pose: (t) => ({
      // elbow raised; X twists the raised arm so the forearm points up to the hair
      rightUpperArm: [-1.3, 0.2, -1.45],
      rightLowerArm: [0, 2.2 + sin(t * 4) * 0.08, 0],
      rightHand: [0, 0, 0.3],
      head: [0, -0.05, 0.12],
      neck: [0, 0, 0.05],
    }),
    face: () => ({ happy: 0.25 }),
  }),
  lookAround: () => ({
    name: "lookAround",
    duration: 4.2,
    pose: (t) => {
      const yaw = sin(t * 1.5) * 0.45;
      return { head: [0, yaw * 0.6, 0], neck: [0, yaw * 0.4, 0], spine: [0, yaw * 0.15, 0] };
    },
    look: (t) => [sin(t * 1.5) * 0.9, 0.05],
  }),
  rock: () => ({
    // hands clasped in front, rocking heel to toe
    name: "rock",
    duration: 5,
    pose: (t) => ({
      leftUpperArm: [-0.3, 0, -0.18],
      rightUpperArm: [-0.3, 0, 0.18],
      leftLowerArm: [0, -1.0, 0],
      rightLowerArm: [0, 1.0, 0],
      hips: [sin(t * 2.4) * 0.04, 0, 0],
      spine: [-sin(t * 2.4) * 0.03, 0, 0],
      head: [0, 0, sin(t * 1.2) * 0.06],
    }),
    face: () => ({ happy: 0.2 }),
  }),
  hum: () => ({
    name: "hum",
    duration: 4.5,
    pose: (t) => ({
      head: [0, 0, sin(t * 4.2) * 0.1],
      neck: [0, 0, sin(t * 4.2) * 0.04],
      spine: [0, 0, -sin(t * 4.2) * 0.03],
      hips: [0, sin(t * 2.1) * 0.05, 0],
    }),
    face: () => ({ happy: 0.45, eyesClosed: 0.5 }),
  }),
  yawn: () => ({
    name: "yawn",
    duration: 3.2,
    pose: () => ({
      rightUpperArm: [-0.8, 0.3, -0.25],
      rightLowerArm: [0, 2.25, 0],
      rightHand: [0, 0, 0.3],
      head: [-0.12, 0, 0.05],
      spine: [-0.05, 0, 0],
    }),
    face: (t) => ({ aa: 0.8 * bump(t, 0.4, 2.8), eyesClosed: 0.9 * bump(t, 0.3, 3.0) }),
  }),
  hop: () => ({
    name: "hop",
    duration: 1.1,
    attack: 0.15,
    release: 0.3,
    pose: () => ({
      leftUpperArm: [0, 0, 0.5],
      rightUpperArm: [0, 0, -0.5],
      leftLowerArm: [0, -0.6, 0],
      rightLowerArm: [0, 0.6, 0],
      head: [-0.06, 0, 0],
    }),
    lift: (t) => 0.07 * Math.max(bump(t, 0.1, 0.45), bump(t, 0.5, 0.85) * 0.6),
    face: () => ({ happy: 0.9 }),
  }),
  clap: () => ({
    name: "clap",
    duration: 1.6,
    pose: (t) => {
      const c = sin(t * 15) * 0.22;
      return {
        leftUpperArm: [-0.75, -0.35, -0.05],
        rightUpperArm: [-0.75, 0.35, 0.05],
        leftLowerArm: [0, -1.2 - c, 0],
        rightLowerArm: [0, 1.2 + c, 0],
        head: [-0.04, 0, 0],
      };
    },
    face: () => ({ happy: 0.9 }),
  }),
  startle: () => ({
    name: "startle",
    duration: 1.4,
    attack: 0.12,
    release: 0.6,
    pose: () => ({
      leftUpperArm: [-0.7, -0.3, 0.2],
      rightUpperArm: [-0.7, 0.3, -0.2],
      leftLowerArm: [0, -2.0, 0],
      rightLowerArm: [0, 2.0, 0],
      spine: [-0.12, 0, 0],
      head: [-0.08, 0, 0],
    }),
    lift: (t) => 0.025 * bump(t, 0, 0.3),
    face: () => ({ surprised: 0.9 }),
  }),
  sigh: () => ({
    name: "sigh",
    duration: 2.4,
    pose: (t) => {
      const up = bump(t, 0, 1.0);
      return {
        leftShoulder: [0, 0, 0.16 * up],
        rightShoulder: [0, 0, -0.16 * up],
        head: [0.12 * bump(t, 0.8, 2.4), 0, 0],
      };
    },
    face: (t) => ({ eyesClosed: 0.6 * bump(t, 0.8, 2.2) }),
  }),
  curious: () => ({
    name: "curious",
    duration: 1.6,
    pose: () => ({ head: [0, 0.06, 0.16], neck: [0, 0, 0.06] }),
  }),
  nod: () => ({
    name: "nod",
    duration: 0.7,
    attack: 0.15,
    release: 0.35,
    pose: () => ({ head: [0.13, 0, 0], neck: [0.05, 0, 0] }),
  }),
  poke: () => ({
    // poked / patted: flinch, then giggle
    name: "poke",
    duration: 1.5,
    attack: 0.08,
    release: 0.7,
    pose: () => ({ spine: [0.04, 0, 0.08], head: [0.05, 0, -0.14], leftShoulder: [0, 0, 0.1] }),
    face: () => ({ happy: 0.7, eyesClosed: 0.7 }),
  }),
  pat: () => ({
    name: "pat",
    duration: 2.2,
    pose: (t) => ({ head: [0.1, 0, sin(t * 3) * 0.06], neck: [0.05, 0, 0] }),
    face: () => ({ happy: 1, eyesClosed: 0.95 }),
  }),
  land: () => ({
    name: "land",
    duration: 0.8,
    attack: 0.05,
    release: 0.6,
    pose: () => ({
      leftUpperLeg: [-0.55, 0, 0],
      rightUpperLeg: [-0.55, 0, 0],
      leftLowerLeg: [1.05, 0, 0],
      rightLowerLeg: [1.05, 0, 0],
      leftFoot: [-0.45, 0, 0],
      rightFoot: [-0.45, 0, 0],
      spine: [0.15, 0, 0],
      leftUpperArm: [0, 0, 0.5],
      rightUpperArm: [0, 0, -0.5],
    }),
    lift: () => -0.09,
    face: () => ({ surprised: 0.6 }),
  }),
  plop: () => ({
    // landed on a window edge: the body sinks forward with the impact and straightens up
    name: "plop",
    duration: 0.9,
    attack: 0.05,
    release: 0.6,
    pose: () => ({
      spine: [0.22, 0, 0],
      head: [0.12, 0, 0],
      leftUpperArm: [-0.4, 0, 0.35],
      rightUpperArm: [-0.4, 0, -0.35],
      leftLowerLeg: [-0.35, 0, 0],
      rightLowerLeg: [-0.35, 0, 0],
    }),
    face: (t) => ({ surprised: 0.5 * bump(t, 0, 0.5), happy: 0.4 * bump(t, 0.3, 0.9) }),
  }),
  notice: () => ({
    // the cursor came close: a quick glance with a little smile
    name: "notice",
    duration: 1.4,
    attack: 0.15,
    release: 0.6,
    pose: () => ({ head: [-0.05, 0, 0.12], neck: [-0.02, 0, 0.05], spine: [-0.03, 0, 0] }),
    face: (t) => ({ surprised: 0.35 * bump(t, 0, 0.5), happy: 0.5 * bump(t, 0.3, 1.4) }),
  }),
  // -- while sitting on an edge --
  kick: () => ({
    // happy alternating kicks of the dangling legs
    name: "kick",
    duration: 2.6,
    pose: (t) => {
      const k = sin(t * 9);
      return {
        leftLowerLeg: [-0.5 - k * 0.45, 0, 0],
        rightLowerLeg: [-0.5 + k * 0.45, 0, 0],
        spine: [-0.04, 0, 0],
        head: [-0.05, 0, sin(t * 4.5) * 0.06],
      };
    },
    face: () => ({ happy: 0.8 }),
  }),
  leanBack: () => ({
    // leaning back on the hands, looking up and around
    name: "leanBack",
    duration: 5,
    attack: 0.8,
    release: 0.9,
    pose: (t) => ({
      spine: [-0.2, 0, 0],
      chest: [-0.06, 0, 0],
      leftUpperArm: [0.3, 0, 0.05],
      rightUpperArm: [0.3, 0, -0.05],
      head: [-0.12, sin(t * 0.9) * 0.2, 0],
      leftLowerLeg: [-0.25, 0, 0],
    }),
    look: (t) => [sin(t * 0.9) * 0.4, 0.4],
    face: () => ({ happy: 0.25 }),
  }),
  lookDown: () => ({
    // peeking down from the edge at what is below
    name: "lookDown",
    duration: 3.4,
    pose: (t) => ({
      spine: [0.16, 0, 0],
      neck: [0.12, 0, 0],
      head: [0.22, sin(t * 1.3) * 0.15, 0],
    }),
    look: (t) => [sin(t * 1.3) * 0.3, -0.6],
    face: (t) => ({ surprised: 0.2 * bump(t, 0.5, 2.5) }),
  }),
};

const IDLE_ACTIONS = ["lookAround", "hairTouch", "rock", "hum", "lookAround", "stretch"];
/** Idle actions that suit sitting on an edge (no hops or heel rocking). */
const SIT_ACTIONS = ["kick", "leanBack", "lookDown", "hum", "hairTouch", "lookAround", "kick"];
/** Actions that lift or rock the hips: they make no sense while sitting. */
const STANDING_ONLY = new Set(["hop", "rock", "land"]);
const EMOTION_ACTIONS: Partial<Record<Emotion, string[]>> = {
  joy: ["hop", "clap", "hop"],
  surprise: ["startle"],
  sad: ["sigh"],
};

/** Idle actions to pick from, weighted by mood (duplicates make an action likelier). */
function idlePool(mood: Mood, idleFor: number, sitting: boolean): string[] {
  if (sitting) {
    const pool = [...SIT_ACTIONS];
    if (idleFor > 90 || mood.energy < 0.4) pool.push("yawn");
    if (mood.energy > 0.7) pool.push("kick", "hum");
    if (mood.boredom > 0.6) pool.push("lookDown", "sigh");
    return pool;
  }
  const pool = [...IDLE_ACTIONS];
  if (idleFor > 90 || mood.energy < 0.4) pool.push("yawn");
  if (mood.energy < 0.35) pool.push("yawn", "sigh");
  if (mood.energy > 0.7) pool.push("hum", "rock");
  if (mood.boredom > 0.6) pool.push("lookAround", "sigh", "lookAround");
  if (mood.affection > 0.75) pool.push("hum", "hairTouch");
  return pool;
}

function addPose(target: Pose, delta: Pose, weight: number): void {
  if (weight <= 0.001) return;
  for (const [bone, r] of Object.entries(delta) as [VRMHumanBoneName, Rot][]) {
    const cur = target[bone] ?? [0, 0, 0];
    target[bone] = [cur[0] + r[0] * weight, cur[1] + r[1] * weight, cur[2] + r[2] * weight];
  }
}

function envelope(t: number, duration: number, attack = 0.35, release = 0.5): number {
  if (t < attack) return smooth(t / attack);
  if (t > duration - release) return smooth(Math.max(0, (duration - t) / release));
  return 1;
}

function smooth(x: number): number {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
}

function mergeFace(target: Face, face: Face, weight: number): void {
  for (const [key, value] of Object.entries(face) as [keyof Face, number][]) {
    target[key] = Math.max(target[key] ?? 0, value * weight);
  }
}

/** Per-frame inputs of the desktop body poses. */
interface BodyInput {
  time: number;
  /** Gait cycle phase in radians (advances with the walking speed). */
  walkPhase: number;
  /** Leg swing amplitude in radians, matched to the speed so the soles don't slide. */
  stride: number;
  /** Pendulum angle of the dangling body while carried (positive: legs trail to the left). */
  swing: number;
  /** Lean out from behind a screen edge: sign is the side of the screen, size how far. */
  peek: number;
}

/** Body poses of the desktop mascot (blended by weight like states). */
function bodyPose(mode: BodyMode, b: BodyInput): Pose {
  const { time } = b;
  switch (mode) {
    case "walk": {
      const s = sin(b.walkPhase);
      const c = Math.cos(b.walkPhase);
      const k = b.stride / 0.3; // pose amplitudes were tuned at a 0.3 rad stride
      return {
        leftUpperLeg: [-b.stride * s, 0, 0],
        rightUpperLeg: [b.stride * s, 0, 0],
        // the swinging leg lifts its foot: knee bends while it passes forward
        leftLowerLeg: [0.1 + 0.7 * k * Math.max(0, c), 0, 0],
        rightLowerLeg: [0.1 + 0.7 * k * Math.max(0, -c), 0, 0],
        leftFoot: [0.15 * k * s, 0, 0],
        rightFoot: [-0.15 * k * s, 0, 0],
        // arms swing against the legs, elbows bend more on the forward swing
        leftUpperArm: [0.4 * k * s, 0, 0.12],
        rightUpperArm: [-0.4 * k * s, 0, -0.12],
        leftLowerArm: [0, -0.25 - 0.25 * k * Math.max(0, -s), 0],
        rightLowerArm: [0, 0.25 + 0.25 * k * Math.max(0, s), 0],
        hips: [0, 0.07 * k * s, 0.03 * k * c],
        spine: [0.06, -0.07 * k * s, -0.02 * k * c],
        head: [0, 0.04 * k * s, 0],
      };
    }
    case "sit": {
      // sitting on an edge: hands on it beside the hips, legs dangling and swinging lazily
      const l = sin(time * 1.9) * 0.2 + sin(time * 0.7) * 0.06;
      const r = sin(time * 1.9 + 2.4) * 0.2 + sin(time * 0.8 + 1) * 0.06;
      return {
        leftUpperLeg: [-1.5, 0, -0.08],
        rightUpperLeg: [-1.5, 0, 0.08],
        leftLowerLeg: [1.3 + l, 0, 0],
        rightLowerLeg: [1.3 + r, 0, 0],
        leftFoot: [-0.3 + l * 0.5, 0, 0],
        rightFoot: [-0.3 + r * 0.5, 0, 0],
        leftShoulder: [0, 0, 0.08],
        rightShoulder: [0, 0, -0.08],
        leftUpperArm: [0.4, 0, 0.1],
        rightUpperArm: [0.4, 0, -0.1],
        leftLowerArm: [0, -0.1, 0],
        rightLowerArm: [0, 0.1, 0],
        leftHand: [0, 0, 0.6],
        rightHand: [0, 0, -0.6],
        spine: [0.08, 0, 0],
        head: [0.04, 0, 0],
      };
    }
    case "drag": {
      // lifted up: arms raised, the body hangs from the shoulders and swings like a pendulum
      const w = b.swing;
      const kick = sin(time * 3.1);
      return {
        leftShoulder: [0, 0, 0.18],
        rightShoulder: [0, 0, -0.18],
        leftUpperArm: [-0.15, 0, 1.75 - w * 0.25],
        rightUpperArm: [-0.15, 0, -1.75 - w * 0.25],
        leftLowerArm: [0, -0.35, 0.25],
        rightLowerArm: [0, 0.35, -0.25],
        // legs trail the motion, with a slow nervous kick
        leftUpperLeg: [-0.12 + kick * 0.12, 0, -w * 0.25 - 0.04],
        rightUpperLeg: [-0.05 - kick * 0.12, 0, -w * 0.25 + 0.04],
        leftLowerLeg: [0.3 + Math.max(0, -kick) * 0.35, 0, 0],
        rightLowerLeg: [0.3 + Math.max(0, kick) * 0.35, 0, 0],
        leftFoot: [0.35, 0, 0],
        rightFoot: [0.35, 0, 0],
        hips: [0, 0, -w * 0.45],
        spine: [0.04, 0, w * 0.2],
        chest: [0, 0, w * 0.1],
        head: [0.06, 0, w * 0.25],
      };
    }
    case "fall": {
      // flailing on the way down: arms windmill, legs pedal
      const f = time * 7;
      return {
        leftUpperArm: [-0.3 + sin(f) * 0.35, 0, 1.45 + sin(f * 1.3) * 0.3],
        rightUpperArm: [-0.3 - sin(f + 1) * 0.35, 0, -1.45 - sin(f * 1.3 + 2) * 0.3],
        leftLowerArm: [0, -0.3, 0.4 + sin(f + 0.5) * 0.3],
        rightLowerArm: [0, 0.3, -0.4 - sin(f + 1.5) * 0.3],
        leftUpperLeg: [-0.45 + sin(f * 0.8) * 0.3, 0, -0.12],
        rightUpperLeg: [-0.3 - sin(f * 0.8) * 0.3, 0, 0.12],
        leftLowerLeg: [0.8 + sin(f * 0.8 + 1) * 0.3, 0, 0],
        rightLowerLeg: [0.6 - sin(f * 0.8 + 1) * 0.3, 0, 0],
        spine: [-0.08, 0, 0],
        head: [-0.15, 0, 0],
      };
    }
    case "peek":
      return peekPose(b.peek, time);
    default:
      return {};
  }
}

/**
 * Hiding behind a screen edge, the body leans out sideways toward the screen (positive
 * `lean`: screen right) and the hand on that side holds on to the edge.
 */
function peekPose(lean: number, time: number): Pose {
  const side = lean < 0 ? -1 : 1;
  // VRM 1.0: leaning toward +X (screen right, the model's left) is a negative Z rotation
  const z = -lean;
  const pose: Pose = {
    spine: [0.02, 0, z * 0.24],
    chest: [0, 0, z * 0.18],
    upperChest: [0, 0, z * 0.12],
    neck: [0, 0, z * 0.08],
    head: [-0.03, 0, z * 0.14 + sin(time * 0.8) * 0.03],
    // weight on the outer leg, the inner one relaxed
    leftUpperLeg: [0, 0, side * 0.04],
    rightUpperLeg: [0, 0, side * 0.04],
    leftLowerLeg: [side > 0 ? 0.15 : 0, 0, 0],
    rightLowerLeg: [side < 0 ? 0.15 : 0, 0, 0],
  };
  // the screen-side hand comes up by the face, as if holding on to the edge
  const reach = Math.min(1, Math.abs(lean) + 0.3);
  if (side > 0) {
    pose.leftUpperArm = [-1.0 * reach, 0, 0.15 * reach];
    pose.leftLowerArm = [0, -1.6 * reach, 0];
    pose.leftHand = [0, 0, 0.35];
  } else {
    pose.rightUpperArm = [-1.0 * reach, 0, -0.15 * reach];
    pose.rightLowerArm = [0, 1.6 * reach, 0];
    pose.rightHand = [0, 0, -0.35];
  }
  return pose;
}

const BODY_MODES: BodyMode[] = ["stand", "walk", "sit", "drag", "fall", "peek"];

/**
 * Natural frequency (rad/s) of each bone's smoothing spring: the torso is heavy and slow,
 * hands and feet are light and quick, so a move ripples outward (overlapping action).
 */
function boneOmega(bone: VRMHumanBoneName): number {
  switch (bone) {
    case "hips":
    case "spine":
    case "chest":
    case "upperChest":
      return 9;
    case "neck":
    case "head":
      return 11;
    case "leftShoulder":
    case "rightShoulder":
    case "leftUpperArm":
    case "rightUpperArm":
    case "leftUpperLeg":
    case "rightUpperLeg":
      return 12;
    case "leftLowerArm":
    case "rightLowerArm":
    case "leftLowerLeg":
    case "rightLowerLeg":
      return 14;
    default:
      return 17; // hands, feet, fingers, eyes
  }
}

/** Under 1: a quick pose change overshoots a little and settles, instead of easing in flat. */
const SPRING_DAMPING = 0.72;
/** Integration step of the springs (stable for every stiffness above). */
const SPRING_STEP = 1 / 120;

/** Swing pendulum of the carried body: about 0.5 Hz, lightly damped so it swings a few times. */
const PENDULUM_OMEGA = 3.2;
const PENDULUM_DAMPING = 0.14;

export class Motion {
  private current = new Map<VRMHumanBoneName, THREE.Euler>();
  private velocity = new Map<VRMHumanBoneName, THREE.Vector3>();
  private stateWeights: Record<State, number> = { idle: 1, listening: 0, thinking: 0, speaking: 0 };
  private emotionWeights = new Map<Emotion, number>();
  private bodyWeights = new Map<BodyMode, number>([["stand", 1]]);
  private gesture: { pose: Pose; t: number; duration: number } | null = null;
  private action: Action | null = null;
  private nextGesture = 0.6;
  private lastGesture = -1;
  private nextIdle = 6;
  private idleFor = 0;
  private drowsy = 0;
  private nod = 0;
  private lastState: State = "idle";
  private lastEmotion: Emotion = "neutral";
  private heardPeak = 0;
  private lookTarget = new THREE.Object3D();
  private lookOffset = new THREE.Vector3();
  private lookGoal = new THREE.Vector3();
  private saccade = new THREE.Vector3();
  private nextGlance = 3;
  private nextSaccade = 0.5;
  private hipsY: number | null = null;
  private baseYaw: number;
  private yaw = 0;
  private attention = 0;
  private desktop: DesktopInput | null = null;
  private lastMode: BodyMode = "stand";
  private walkPhase = 0;
  private swing = 0;
  private swingVelocity = 0;
  private lastVx = 0;
  private accelX = 0;
  private nextNotice = 0;
  private peekLean = 0;
  private peekOut = false;
  private nextPeek = 3;
  /** Height of the desktop frame in metres (set by the avatar): converts screen speeds. */
  frameMetres = 2.2;
  /** Facial hints for this frame, read by the avatar after `update`. */
  face: Face = {};
  /** The character's mood: shapes idle actions and drowsiness. */
  mood: Mood = { energy: 0.6, affection: 0.5, boredom: 0 };

  constructor(
    private vrm: VRM,
    private axis: 1 | -1,
    camera: THREE.Camera,
  ) {
    camera.add(this.lookTarget); // looks at the camera, plus small glances around
    if (vrm.lookAt) vrm.lookAt.target = this.lookTarget;
    const hips = vrm.humanoid.getNormalizedBoneNode("hips");
    if (hips) this.hipsY = hips.position.y;
    this.baseYaw = vrm.scene.rotation.y;
  }

  /** Perk up: head lifts and turns to the user (decays over ~1.5 s). */
  wake(): void {
    this.attention = 1;
    this.lookGoal.set(0, 0, 0);
    this.drowsy = 0;
    this.idleFor = 0;
  }

  /** Play a named one-off action (wave, hop, pat…); replaces an idle action. */
  play(name: string): void {
    const make = ACTIONS[name];
    if (!make) return;
    if (this.desktop?.mode === "sit" && STANDING_ONLY.has(name)) return;
    this.drowsy = 0;
    this.idleFor = 0;
    if (this.action && this.action.name === name) {
      this.action.t = Math.min(this.action.t, (this.action.attack ?? 0.35) + 0.01);
      return;
    }
    this.action = { ...make(), t: 0 };
  }

  /** Desktop mascot input: body mode, facing, cursor gaze (null in the main window). */
  setDesktop(input: DesktopInput | null): void {
    if (input && input.mode !== this.lastMode) {
      if (input.mode === "stand" && this.lastMode === "fall") this.play("land");
      if (input.mode === "sit" && this.lastMode === "fall") this.play("plop");
      this.lastMode = input.mode;
    }
    // the cursor just came close to a resting character: she notices it
    const resting = input?.mode === "stand" || input?.mode === "sit";
    if (input?.look && !this.desktop?.look && resting && !this.action) {
      if (performance.now() > this.nextNotice) {
        this.play("notice");
        this.nextNotice = performance.now() + 25_000;
      }
    }
    this.desktop = input;
  }

  /** Debug (`?debug`): hold an extra pose on top of everything, to tune angles by hand. */
  preview: Pose | null = null;

  get actionName(): string | null {
    return this.action?.name ?? null;
  }

  update(state: State, emotion: Emotion, level: number, time: number, dt: number): void {
    const k = (speed: number) => Math.min(1, dt * speed);
    const face: Face = {};
    this.react(state, emotion, level, dt);

    // blend weights of state, emotion and body poses
    for (const s of Object.keys(this.stateWeights) as State[]) {
      this.stateWeights[s] += ((s === state ? 1 : 0) - this.stateWeights[s]) * k(4);
    }
    for (const e of Object.keys(EMOTION_POSES) as Emotion[]) {
      const w = this.emotionWeights.get(e) ?? 0;
      this.emotionWeights.set(e, w + ((e === emotion ? 1 : 0) - w) * k(3));
    }
    const mode = this.desktop?.mode ?? "stand";
    for (const m of BODY_MODES) {
      const w = this.bodyWeights.get(m) ?? 0;
      const speed = m === "drag" || m === "fall" || mode === "drag" ? 10 : 5;
      this.bodyWeights.set(m, w + ((m === mode ? 1 : 0) - w) * k(speed));
    }

    const pose = basePose();
    for (const s of Object.keys(STATE_POSES) as State[])
      addPose(pose, STATE_POSES[s], this.stateWeights[s]);
    for (const [e, w] of this.emotionWeights) addPose(pose, EMOTION_POSES[e] ?? {}, w);
    const stride = this.updateGait(mode, dt);
    this.updateSwing(mode, dt);
    const peek = this.updatePeek(mode, dt, face);
    const body: BodyInput = { time, walkPhase: this.walkPhase, stride, swing: this.swing, peek };
    for (const [m, w] of this.bodyWeights) addPose(pose, bodyPose(m, body), w);
    const carried = this.bodyWeights.get("drag") ?? 0;
    const falling = this.bodyWeights.get("fall") ?? 0;
    mergeFace(face, { surprised: Math.min(1, Math.abs(this.swing) * 1.2) }, carried);
    mergeFace(face, { surprised: 0.7 }, falling);

    if (this.preview) addPose(pose, this.preview, 1);
    this.updateGesture(state, level, dt);
    if (this.gesture) {
      addPose(pose, this.gesture.pose, envelope(this.gesture.t, this.gesture.duration));
    }

    let lift = 0;
    let actionLook: [number, number] | null = null;
    if (this.action) {
      const a = this.action;
      a.t += dt;
      const w = envelope(a.t, a.duration, a.attack, a.release);
      addPose(pose, a.pose(a.t), w);
      if (a.face) mergeFace(face, a.face(a.t), w);
      if (a.lift) lift += a.lift(a.t) * w;
      if (a.look) actionLook = a.look(a.t);
      if (a.t >= a.duration) this.action = null;
    }

    // life: breathing (faster when talking or happy), weight shift, small head drift
    const excited = Math.max(this.stateWeights.speaking, this.emotionWeights.get("joy") ?? 0);
    const breathRate = 1.5 + excited * 0.6 - this.drowsy * 0.6;
    const breath = sin(time * breathRate);
    const sway = sin(time * 0.45);
    const standing = this.bodyWeights.get("stand") ?? 0;
    addPose(pose, { chest: [breath * 0.014, 0, 0], upperChest: [breath * 0.012, 0, 0] }, 1);
    addPose(
      pose,
      { hips: [0, sway * 0.03, sway * 0.025], spine: [0, -sway * 0.02, -sway * 0.02] },
      standing,
    );
    addPose(
      pose,
      { leftShoulder: [0, 0, breath * 0.015], rightShoulder: [0, 0, -breath * 0.015] },
      1,
    );
    addPose(
      pose,
      { neck: [sin(time * 0.9) * 0.015, sin(time * 0.37) * 0.05, sin(time * 0.6) * 0.02] },
      1,
    );
    const speaking = state === "speaking" ? 1 : 0;
    this.nod += (level * speaking - this.nod) * k(level > this.nod ? 10 : 4);
    addPose(pose, { head: [this.nod * 0.1, 0, sin(time * 2.1) * 0.03 * speaking] }, 1);

    // dozing off after a long quiet time
    if (this.drowsy > 0) {
      addPose(
        pose,
        { head: [0.32, 0, 0.12], neck: [0.12, 0, 0], spine: [0.06, 0, 0] },
        this.drowsy,
      );
      mergeFace(face, { eyesClosed: 1 }, smooth(this.drowsy * 1.4 - 0.3));
    }

    // perking up after the wake word
    this.attention = Math.max(0, this.attention - dt * 0.7);
    if (this.attention > 0) {
      const a = smooth(this.attention);
      addPose(pose, { head: [-0.09, 0, -0.05], spine: [-0.04, 0, 0], neck: [-0.03, 0, 0] }, a);
    }

    // desktop: turn the head toward the cursor
    const look = this.desktop?.look;
    if (look && !actionLook && state !== "thinking") {
      const x = Math.max(-1, Math.min(1, look[0]));
      const y = Math.max(-1, Math.min(1, look[1]));
      addPose(pose, { head: [-y * 0.22, x * 0.4, 0], neck: [-y * 0.08, x * 0.18, 0] }, 1);
    }

    // apply through damped springs (and the VRM 0.x axis flip); walking and being carried
    // need snappier limbs to keep up with the cycle and the hand
    const humanoid = this.vrm.humanoid;
    const quick = mode === "walk" || mode === "drag" || mode === "fall" ? 1.5 : 1;
    const steps = Math.max(1, Math.ceil(dt / SPRING_STEP));
    const h = dt / steps;
    for (const [bone, r] of Object.entries(pose) as [VRMHumanBoneName, Rot][]) {
      const node = humanoid.getNormalizedBoneNode(bone);
      if (!node) continue;
      let euler = this.current.get(bone);
      let v = this.velocity.get(bone);
      if (!euler || !v) {
        euler = new THREE.Euler(r[0], r[1], r[2]);
        v = new THREE.Vector3();
        this.current.set(bone, euler);
        this.velocity.set(bone, v);
      }
      const w = boneOmega(bone) * quick;
      const stiffness = w * w;
      const damping = 2 * SPRING_DAMPING * w;
      for (let i = 0; i < steps; i++) {
        v.x += (stiffness * (r[0] - euler.x) - damping * v.x) * h;
        v.y += (stiffness * (r[1] - euler.y) - damping * v.y) * h;
        v.z += (stiffness * (r[2] - euler.z) - damping * v.z) * h;
        euler.x += v.x * h;
        euler.y += v.y * h;
        euler.z += v.z * h;
      }
      node.rotation.set(euler.x * this.axis, euler.y, euler.z * this.axis);
    }

    // hips height: joy bounce, hops, walking bob (lowest with the legs apart), landing squat
    const hips = humanoid.getNormalizedBoneNode("hips");
    if (hips && this.hipsY !== null) {
      const joy = this.emotionWeights.get("joy") ?? 0;
      const walk = this.bodyWeights.get("walk") ?? 0;
      const seated = this.bodyWeights.get("sit") ?? 0;
      const bounce = Math.abs(sin(time * 6)) * 0.012 * joy * standing;
      const bob = (0.5 - Math.abs(sin(this.walkPhase))) * stride * 0.07 * walk;
      hips.position.y = this.hipsY + bounce + bob + lift * (1 - seated);
    }

    // whole-body turn toward the walking direction
    const facing = mode === "walk" ? (this.desktop?.facing ?? 0) : 0;
    this.yaw += (facing * 1.2 - this.yaw) * k(6);
    this.vrm.scene.rotation.y = this.baseYaw + this.yaw;

    this.updateGaze(state, time, dt, actionLook);
    this.face = face;
  }

  /** Spontaneous reactions: emotion one-shots, listening nods, idle actions, drowsiness. */
  private react(state: State, emotion: Emotion, level: number, dt: number): void {
    if (emotion !== this.lastEmotion) {
      const options = EMOTION_ACTIONS[emotion];
      if (options) this.play(options[Math.floor(Math.random() * options.length)]);
      this.lastEmotion = emotion;
    }
    if (state !== this.lastState) {
      if (state === "listening") this.play("curious");
      if (state !== "idle") {
        this.drowsy = 0;
        this.idleFor = 0;
      }
      this.lastState = state;
    }
    // backchannel: a small nod when the user pauses after a phrase
    if (state === "listening") {
      this.heardPeak = Math.max(this.heardPeak * (1 - dt * 0.5), level);
      if (this.heardPeak > 0.25 && level < 0.06 && !this.action) {
        this.play("nod");
        this.heardPeak = 0;
      }
    }
    const sitting = this.desktop?.mode === "sit";
    const busyBody = this.desktop && this.desktop.mode !== "stand" && !sitting;
    if (state !== "idle" || busyBody) {
      this.nextIdle = Math.max(this.nextIdle, 3);
      if (busyBody) this.drowsy = 0;
      return;
    }
    this.idleFor += dt;
    // a tired character dozes off sooner: ~3 min at night, ~6 min when lively
    const dozeAfter = 120 + 360 * this.mood.energy;
    if (this.idleFor > dozeAfter) this.drowsy = Math.min(1, this.drowsy + dt * 0.08);
    if (this.drowsy > 0) return;
    this.nextIdle -= dt;
    if (this.nextIdle <= 0 && !this.action && emotion === "neutral") {
      const pool = idlePool(this.mood, this.idleFor, sitting);
      const idleFor = this.idleFor;
      this.play(pool[Math.floor(Math.random() * pool.length)]);
      this.idleFor = idleFor; // an idle action is not activity: keep counting toward a nap
      // lively: fidgets more often; tired: stays still longer
      this.nextIdle = (7 + Math.random() * 9) * (1.4 - 0.6 * this.mood.energy);
    }
  }

  /** Advance the gait cycle; returns the leg swing amplitude for the current speed. */
  private updateGait(mode: BodyMode, dt: number): number {
    // walking speed in m/s (0.3 frame heights/s, the backend's pace, until it reports one)
    const vx = Math.abs(this.desktop?.vx ?? 0) || 0.3;
    const speed = vx * this.frameMetres;
    const leg = Math.max(0.5, (this.hipsY ?? 0.85) * 0.95);
    // a slow stroll: about 1.6 steps a second, a little quicker when going faster
    const cadence = 0.75 + 0.12 * speed;
    // per cycle the body covers two steps of 2·leg·sin(stride)
    const stride = Math.asin(Math.min(0.6, speed / (4 * cadence * leg)));
    if (mode === "walk") this.walkPhase += dt * cadence * 2 * Math.PI;
    else this.walkPhase = 0;
    return Math.max(0.12, Math.min(0.5, stride));
  }

  /**
   * How far to lean out from behind a screen edge: now and then on her own, curiously when
   * the cursor is around, and back into hiding (giggling) when it comes too close.
   */
  private updatePeek(mode: BodyMode, dt: number, face: Face): number {
    if (mode !== "peek") {
      this.peekLean = 0;
      this.peekOut = false;
      return 0;
    }
    const look = this.desktop?.look;
    let goal: number;
    if (look) {
      const near = Math.hypot(look[0], look[1]) < 0.35;
      goal = near ? 0.1 : 1;
      mergeFace(face, near ? { happy: 0.8, eyesClosed: 0.6 } : { surprised: 0.3, happy: 0.3 }, 1);
    } else {
      this.nextPeek -= dt;
      if (this.nextPeek <= 0) {
        this.peekOut = !this.peekOut;
        this.nextPeek = this.peekOut ? 1.5 + Math.random() * 2 : 3 + Math.random() * 6;
      }
      goal = this.peekOut ? 0.9 : 0.35;
    }
    this.peekLean += (goal - this.peekLean) * Math.min(1, dt * 3);
    return this.peekLean * (this.desktop?.facing || 1);
  }

  /** The carried body swings like a pendulum, pushed by the hand's acceleration. */
  private updateSwing(mode: BodyMode, dt: number): void {
    const vx = mode === "drag" ? (this.desktop?.vx ?? 0) : 0;
    if (dt > 0) {
      const raw = (vx - this.lastVx) / dt;
      this.accelX += (raw - this.accelX) * Math.min(1, dt * 12);
    }
    this.lastVx = vx;
    if (mode !== "drag" && Math.abs(this.swing) < 0.01 && Math.abs(this.swingVelocity) < 0.01) {
      this.swing = this.swingVelocity = this.accelX = 0;
      return;
    }
    const leg = Math.max(0.5, this.hipsY ?? 0.85);
    // air drag holds the legs a little behind while the hand moves steadily
    const trail = Math.max(-0.35, Math.min(0.35, vx * 0.25));
    const push = (this.accelX * this.frameMetres) / leg;
    const steps = Math.max(1, Math.ceil(dt / SPRING_STEP));
    const h = dt / steps;
    for (let i = 0; i < steps; i++) {
      const a =
        -(PENDULUM_OMEGA ** 2) * (this.swing - trail) -
        2 * PENDULUM_DAMPING * PENDULUM_OMEGA * this.swingVelocity +
        push;
      this.swingVelocity += a * h;
      this.swing = Math.max(-0.9, Math.min(0.9, this.swing + this.swingVelocity * h));
    }
  }

  private updateGesture(state: State, level: number, dt: number): void {
    if (this.gesture) {
      this.gesture.t += dt;
      if (this.gesture.t >= this.gesture.duration) this.gesture = null;
      return;
    }
    if (state !== "speaking" || this.action) {
      this.nextGesture = 0.4;
      return;
    }
    this.nextGesture -= dt * (0.6 + level); // louder speech -> livelier gestures
    if (this.nextGesture > 0) return;
    let index = Math.floor(Math.random() * GESTURES.length);
    if (index === this.lastGesture) index = (index + 1) % GESTURES.length;
    this.lastGesture = index;
    this.gesture = { pose: GESTURES[index], t: 0, duration: 1.3 + Math.random() * 1.2 };
    this.nextGesture = 0.7 + Math.random() * 1.6;
  }

  private updateGaze(
    state: State,
    time: number,
    dt: number,
    actionLook: [number, number] | null,
  ): void {
    this.nextGlance -= dt;
    this.nextSaccade -= dt;
    const cursor = this.desktop?.look;
    if (actionLook) {
      this.lookGoal.set(actionLook[0], actionLook[1], 0);
    } else if (state === "thinking") {
      this.lookGoal.set(0.35, 0.45, 0); // up and aside
    } else if (cursor) {
      this.lookGoal.set(cursor[0] * 0.8, cursor[1] * 0.6, 0);
    } else if (state === "idle" && this.nextGlance <= 0) {
      const away = Math.random() < 0.35;
      this.lookGoal.set(
        away ? (Math.random() - 0.5) * 0.9 : 0,
        away ? (Math.random() - 0.4) * 0.3 : 0,
        0,
      );
      this.nextGlance = 2.5 + Math.random() * 4;
    } else if (state !== "idle") {
      this.lookGoal.set(0, 0, 0);
    }
    // tiny eye saccades keep the gaze from looking frozen
    if (this.nextSaccade <= 0) {
      this.saccade.set((Math.random() - 0.5) * 0.06, (Math.random() - 0.5) * 0.04, 0);
      this.nextSaccade = 0.4 + Math.random() * 1.6;
    }
    this.lookOffset.lerp(this.lookGoal, Math.min(1, dt * 3));
    this.lookTarget.position.set(
      this.lookOffset.x + this.saccade.x,
      this.lookOffset.y + this.saccade.y + sin(time * 0.3) * 0.02,
      0,
    );
  }

  dispose(): void {
    this.lookTarget.removeFromParent();
  }
}
