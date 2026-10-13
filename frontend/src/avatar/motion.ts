// Procedural body animation for VRM avatars: idle life, state poses, speaking gestures, emotions,
// one-off actions (wave, stretch, hop…) and desktop-mode body modes (walk, sit, dangle, fall).
//
// Rotations are written in VRM 1.0 normalized-bone convention (model faces +Z, arms along ±X
// in T-pose) and converted for VRM 0.x by flipping X and Z (see `axis`).
// Signs used below: upperArm/upperLeg X < 0 swings the limb forward; lowerLeg X > 0 bends the
// knee; leftUpperArm Z > 0 / rightUpperArm Z < 0 raises the arm; head Y > 0 turns to screen right.

import * as THREE from "three";
import type { VRM, VRMHumanBoneName } from "@pixiv/three-vrm";
import type { ClipLayer } from "./clips";
import { LegIK } from "./legik";
import { GESTURES, KEYED_ACTIONS, pickGesture, type Gesture, type KeyedAction } from "./moves";
import type { Emotion, Mood, State } from "../types";
import type { BodyMode, DesktopInput } from "./types";
import {
  Spring,
  approach,
  envelope,
  follow,
  noise1,
  smoothstep,
  voiceOpenness,
  wander,
} from "./smooth";

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

/** A one-off, time-varying animation (wave, stretch, hop…). */
interface Action {
  name: string;
  duration: number;
  t: number;
  /** Procedural pose over time (loops like rocking or kicking). */
  pose?(t: number): Pose;
  /** Keyframed motion (moves.ts): starts and ends at rest, so it needs no envelope. */
  keyed?: KeyedAction;
  /** Playback speed: keyed actions vary a little every time (no two waves alike). */
  rate: number;
  face?(t: number): Face;
  /** Hips lift in metres (hops, squats). */
  lift?(t: number): number;
  /** Gaze offset in camera space. */
  look?(t: number): [number, number] | null;
  attack?: number;
  release?: number;
  /** Played by a .vrma clip: the procedural pose is skipped, face hints stay. */
  clip?: boolean;
}

type ActionSpec = Omit<Action, "t" | "rate" | "keyed">;

const sin = Math.sin;
const bump = (t: number, a: number, b: number): number =>
  t <= a || t >= b ? 0 : sin(((t - a) / (b - a)) * Math.PI);

const ACTIONS: Record<string, () => ActionSpec> = {
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
/** Actions that only move the head: speech gestures may go on during them. */
const HEAD_ACTIONS = new Set(["curious", "nod", "notice"]);
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

/**
 * Per-frame pose accumulator: one reusable [x, y, z] per bone, so blending the layers does
 * not allocate a new pose per frame (less garbage, fewer collection hitches).
 * Bones once touched stay in the set and relax back to the base pose when nothing drives
 * them, instead of freezing wherever the last layer left them.
 */
class PoseBuffer {
  readonly rot = new Map<VRMHumanBoneName, Float64Array>();

  reset(base: Pose): void {
    for (const r of this.rot.values()) r.fill(0);
    this.add(base, 1);
  }

  add(pose: Pose, weight: number): void {
    if (weight <= 0.001) return;
    for (const bone in pose) {
      const r = pose[bone as VRMHumanBoneName];
      if (r) this.addRot(bone as VRMHumanBoneName, r[0] * weight, r[1] * weight, r[2] * weight);
    }
  }

  addRot(bone: VRMHumanBoneName, x: number, y: number, z: number): void {
    let r = this.rot.get(bone);
    if (!r) {
      r = new Float64Array(3);
      this.rot.set(bone, r);
    }
    r[0] += x;
    r[1] += y;
    r[2] += z;
  }
}

/** Attack/hold/release weight of a timed gesture or action. */
function actionEnvelope(t: number, duration: number, attack = 0.35, release = 0.5): number {
  if (t < attack) return smoothstep(t / attack);
  if (t > duration - release) return smoothstep(Math.max(0, (duration - t) / release));
  return 1;
}

function mergeFace(target: Face, face: Face, weight: number): void {
  for (const key in face) {
    const value = face[key as keyof Face] ?? 0;
    target[key as keyof Face] = Math.max(target[key as keyof Face] ?? 0, value * weight);
  }
}

function clearFace(face: Face): void {
  face.eyesClosed = face.happy = face.surprised = face.sad = face.aa = 0;
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
const STATES: State[] = ["idle", "listening", "thinking", "speaking"];
/** Dozing off: head drops forward and aside. */
const DOZE_POSE: Pose = { head: [0.32, 0, 0.12], neck: [0.12, 0, 0], spine: [0.06, 0, 0] };
/** Perking up after the wake word: chin up, chest open. */
const ATTENTION_POSE: Pose = { head: [-0.09, 0, -0.05], spine: [-0.04, 0, 0], neck: [-0.03, 0, 0] };
const EMOTIONS_WITH_POSE = Object.keys(EMOTION_POSES) as Emotion[];
const BASE_POSE = basePose();
const LEFT_FINGERS = FINGERS.flatMap((f) =>
  SEGMENTS.map((s) => `left${f}${s}` as VRMHumanBoneName),
);
const RIGHT_FINGERS = FINGERS.flatMap((f) =>
  SEGMENTS.map((s) => `right${f}${s}` as VRMHumanBoneName),
);

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
      return 8;
    case "neck":
    case "head":
      return 10;
    case "leftShoulder":
    case "rightShoulder":
    case "leftUpperArm":
    case "rightUpperArm":
    case "leftUpperLeg":
    case "rightUpperLeg":
      return 11;
    case "leftLowerArm":
    case "rightLowerArm":
    case "leftLowerLeg":
    case "rightLowerLeg":
      return 13;
    default:
      return 16; // hands, feet, fingers, eyes
  }
}

/**
 * Damping ratio per bone: the heavy torso and the head settle without overshoot (a wobbly
 * head looks robotic), limbs overshoot a little and settle (follow-through).
 */
function boneDamping(bone: VRMHumanBoneName): number {
  switch (bone) {
    case "hips":
    case "spine":
    case "chest":
    case "upperChest":
    case "neck":
    case "head":
      return 0.92;
    default:
      return 0.7;
  }
}

/** Sideways pelvis travel of the weight shift (m): wider when foot IK keeps the soles put. */
const HIPS_SWAY = 0.012;
const HIPS_SWAY_IK = 0.022;
/** How much the pelvis dips (m) when the weight sits on one leg. */
const HIPS_DIP = 0.006;
/** Heel lift of the unloaded leg (rad, about the toe) at a full weight shift. */
const HEEL_LIFT = 0.1;

/** A running speech gesture: time, speed, size and the fade when its phrase ends. */
interface GestureRun {
  gesture: Gesture;
  t: number;
  rate: number;
  gain: number;
  fade: number;
  releasing: boolean;
}

/** Natural frequency of the state and emotion pose blends (rad/s): about 0.6 s to settle. */
const BLEND_OMEGA = 7;

/** Seconds the hands take back to rest when a phrase ends mid-gesture. */
const GESTURE_RELEASE = 0.5;

/** Integration step of the springs (stable for every stiffness above). */
const SPRING_STEP = 1 / 120;

/** Swing pendulum of the carried body: about 0.5 Hz, lightly damped so it swings a few times. */
const PENDULUM_OMEGA = 3.2;
const PENDULUM_DAMPING = 0.14;

/** Head nod oscillator kicked on stressed syllables: ~2 Hz, settles after one small bounce. */
const NOD_OMEGA = 13;
const NOD_DAMPING = 0.55;

/** One bone's smoothing spring: current rotation and angular velocity (Euler, VRM 1.0). */
interface BoneSpring {
  node: THREE.Object3D | null;
  x: Float64Array;
  v: Float64Array;
  omega: number;
  zeta: number;
}

export class Motion {
  private springs = new Map<VRMHumanBoneName, BoneSpring>();
  private pose = new PoseBuffer();
  private tmpEuler = new THREE.Euler();
  private tmpQuat = new THREE.Quaternion();
  private stateWeights: Record<State, number> = { idle: 1, listening: 0, thinking: 0, speaking: 0 };
  private emotionWeights = new Map<Emotion, number>();
  /**
   * State and emotion poses blend in through critically damped springs: unlike an
   * exponential approach they start moving gently, so a state change does not jolt the limbs.
   */
  private stateBlend = new Map<State, Spring>();
  private emotionBlend = new Map<Emotion, Spring>();
  private bodyWeights = new Map<BodyMode, number>([["stand", 1]]);
  /** The current speech gesture and the previous one still fading out under it. */
  private gesture: GestureRun | null = null;
  private gestureOut: GestureRun | null = null;
  private recentGestures: string[] = [];
  private lastHand: Gesture["hand"] = "";
  private action: Action | null = null;
  /** An action cut short by a new one: fades out instead of snapping away. */
  private actionOut: { action: Action; fade: number } | null = null;
  private nextGesture = 0.6;
  private nextIdle = 6;
  private idleFor = 0;
  private drowsy = 0;
  private lastState: State = "idle";
  private lastEmotion: Emotion = "neutral";
  private heardPeak = 0;
  private lookTarget = new THREE.Object3D();
  /** Where the eyes look (camera space), the glance goal, and a tiny saccade offset. */
  private lookOffset = new THREE.Vector3();
  private lookGoal = new THREE.Vector3();
  private saccade = new THREE.Vector3();
  private nextGlance = 3;
  private nextSaccade = 0.5;
  /** Size of the last gaze jump, read (and cleared) by the avatar to blink with it. */
  private gazeShift = 0;
  /** The head follows the eyes part of the way, slower (eyes lead, head follows). */
  private headYaw = new Spring(0, 5);
  private headPitch = new Spring(0, 5);
  /** Cursor position arrives at ~15 Hz in steps: smoothed before it turns the head. */
  private cursorX = new Spring(0, 9);
  private cursorY = new Spring(0, 9);
  private hipsY: number | null = null;
  private hipsX = 0;
  private hipsZ = 0;
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
  // idle life
  private breathPhase = 0;
  private shiftGoal = 0.7;
  private nextShift = 5;
  private weightShift = new Spring(0.7, 2.2);
  // speech
  private voice = 0;
  private voiceEnergy = 0;
  private voiceLow = 0;
  private silentFor = 1;
  private lastSilence = 0;
  private beatCooldown = 0;
  /** Beat gesture: a critically damped spring pulled by stressed syllables (smooth push). */
  private beat = new Spring(0, 14);
  private nodX = 0;
  private nodV = 0;
  /**
   * Beat and nod drives: a stressed syllable sets a pulse that decays, and the beat spring and
   * the nod oscillator follow it. Kicking their velocity directly made the hands and the head
   * jump (an instant change of speed).
   */
  private beatDrive = 0;
  private nodDrive = 0;
  /** Height of the desktop frame in metres (set by the avatar): converts screen speeds. */
  frameMetres = 2.2;
  /**
   * How strongly the voice moves the head and body. The desktop mascot is small on screen,
   * so the avatar raises it above 1 to keep the speech gestures readable.
   */
  voiceGain = 1;
  /** Loudness of the microphone 0..1: the character leans in while it listens. */
  inputLevel = 0;
  private listenLean = 0;
  private reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  /** Foot grounding while standing (null if the model has no usable legs). */
  private legs: LegIK | null;
  private groundWeight = 1;
  /** Adds a sampled keyframe rotation to this frame's pose (bound once: no garbage). */
  private addRot = (bone: VRMHumanBoneName, x: number, y: number, z: number): void =>
    this.pose.addRot(bone, x, y, z);
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
    if (hips) {
      this.hipsY = hips.position.y;
      this.hipsX = hips.position.x;
      this.hipsZ = hips.position.z;
    }
    this.baseYaw = vrm.scene.rotation.y;
    this.legs = LegIK.create(vrm);
  }

  /** Perk up: head lifts and turns to the user (decays over ~1.5 s). */
  wake(): void {
    this.attention = 1;
    this.setLookGoal(0, 0);
    this.drowsy = 0;
    this.idleFor = 0;
  }

  /** Play a named one-off action (wave, hop, pat…); replaces an idle action. */
  play(name: string): void {
    const keyed = KEYED_ACTIONS[name] as KeyedAction | undefined;
    const make = ACTIONS[name] as (() => ActionSpec) | undefined;
    const spec = keyed ? null : make?.();
    if (!keyed && !spec) return;
    if (this.desktop?.mode === "sit" && STANDING_ONLY.has(name)) return;
    this.drowsy = 0;
    this.idleFor = 0;
    if (this.action && this.action.name === name) {
      // asked again while it plays: a looping action keeps going, a keyed one just finishes
      if (!this.action.keyed) {
        this.action.t = Math.min(this.action.t, (this.action.attack ?? 0.35) + 0.01);
      }
      return;
    }
    const action: Action = keyed
      ? { name, duration: keyed.motion.duration, keyed, t: 0, rate: 0.93 + Math.random() * 0.14 }
      : { ...(spec as ActionSpec), t: 0, rate: 1 };
    // a mocap clip of the same name replaces the procedural pose (see clips.ts)
    const clip = this.clips?.play(name) ?? 0;
    if (clip > 0) {
      action.clip = true;
      action.duration = clip;
      action.rate = 1;
      action.lift = undefined;
    }
    if (this.action) this.actionOut = { action: this.action, fade: 1 };
    this.action = action;
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

  /** Optional .vrma clips under the procedural layers (set by the avatar once loaded). */
  clips: ClipLayer | null = null;

  get actionName(): string | null {
    return this.action?.name ?? null;
  }

  /** How far the gaze jumped since the last call (0 if it did not): big jumps come with a blink. */
  takeGazeShift(): number {
    const shift = this.gazeShift;
    this.gazeShift = 0;
    return shift;
  }

  update(state: State, emotion: Emotion, level: number, time: number, dt: number): void {
    const reduced = this.reducedMotion.matches;
    const face = this.face;
    clearFace(face);
    this.react(state, emotion, level, dt, reduced);

    // blend weights of state, emotion and body poses
    for (const s of STATES) {
      let blend = this.stateBlend.get(s);
      if (!blend) this.stateBlend.set(s, (blend = new Spring(this.stateWeights[s], BLEND_OMEGA)));
      this.stateWeights[s] = blend.update(s === state ? 1 : 0, dt);
    }
    for (const e of EMOTIONS_WITH_POSE) {
      let blend = this.emotionBlend.get(e);
      if (!blend) this.emotionBlend.set(e, (blend = new Spring(0, BLEND_OMEGA * 0.8)));
      this.emotionWeights.set(e, blend.update(e === emotion ? 1 : 0, dt));
    }
    const mode = this.desktop?.mode ?? "stand";
    for (const m of BODY_MODES) {
      const speed = m === "drag" || m === "fall" || mode === "drag" ? 10 : 5;
      this.bodyWeights.set(
        m,
        approach(this.bodyWeights.get(m) ?? 0, m === mode ? 1 : 0, speed, dt),
      );
    }

    // layers, bottom to top: base → state → emotion → body mode → gesture → action → life
    const pose = this.pose;
    pose.reset(BASE_POSE);
    for (const s of STATES) pose.add(STATE_POSES[s], this.stateWeights[s]);
    for (const [e, w] of this.emotionWeights) pose.add(EMOTION_POSES[e] ?? {}, w);
    const stride = this.updateGait(mode, dt);
    this.updateSwing(mode, dt);
    const peek = this.updatePeek(mode, dt, face);
    const body: BodyInput = { time, walkPhase: this.walkPhase, stride, swing: this.swing, peek };
    for (const [m, w] of this.bodyWeights) if (w > 0.001) pose.add(bodyPose(m, body), w);
    const carried = this.bodyWeights.get("drag") ?? 0;
    const falling = this.bodyWeights.get("fall") ?? 0;
    mergeFace(face, { surprised: Math.min(1, Math.abs(this.swing) * 1.2) }, carried);
    mergeFace(face, { surprised: 0.7 }, falling);

    if (this.preview) pose.add(this.preview, 1);
    this.updateVoice(state, level, dt, reduced);
    this.updateGesture(state, dt, reduced);
    for (const g of [this.gestureOut, this.gesture]) {
      if (!g) continue;
      // stressed syllables push the gesturing hands a little further (beat gestures)
      const w = smoothstep(g.fade) * (g.gain + this.beat.value * 0.3);
      g.gesture.motion.sample(g.t, this.addRot, w);
    }

    // one-off actions; one that was cut short by a new action fades out under it
    let lift = 0;
    let actionLook: [number, number] | null = null;
    const out = this.actionOut;
    if (out) {
      out.fade = approach(out.fade, 0, 7, dt);
      lift += this.runAction(out.action, dt, out.fade);
      if (out.fade < 0.01 || out.action.t >= out.action.duration) this.actionOut = null;
    }
    if (this.action) {
      const a = this.action;
      lift += this.runAction(a, dt, 1);
      actionLook = this.actionLook(a);
      if (a.t >= a.duration) this.action = null;
    }

    const standing = this.bodyWeights.get("stand") ?? 0;
    // mocap clips bring their own idle life: the procedural one fades out under them
    this.clips?.update(dt, standing);
    const clipBody = this.clips?.coversBody ? this.clips.weight : 0;
    const shift = this.idleLife(time, dt, standing, reduced, 1 - clipBody);
    this.speechMotion(time, dt, reduced);

    // dozing off after a long quiet time
    if (this.drowsy > 0) {
      pose.add(DOZE_POSE, this.drowsy);
      mergeFace(face, { eyesClosed: 1 }, smoothstep(this.drowsy * 1.4 - 0.3));
    }

    // perking up after the wake word
    this.attention = Math.max(0, this.attention - dt * 0.7);
    if (this.attention > 0) pose.add(ATTENTION_POSE, smoothstep(this.attention));

    // desktop: turn the head toward the cursor (smoothed: it arrives in 15 Hz steps)
    const look = this.desktop?.look;
    const followCursor = !!look && !actionLook && state !== "thinking";
    const cx = this.cursorX.update(followCursor ? Math.max(-1, Math.min(1, look[0])) : 0, dt);
    const cy = this.cursorY.update(followCursor ? Math.max(-1, Math.min(1, look[1])) : 0, dt);
    pose.addRot("head", -cy * 0.22, cx * 0.4, 0);
    pose.addRot("neck", -cy * 0.08, cx * 0.18, 0);

    this.updateGaze(state, time, dt, actionLook, reduced);
    // eyes lead, the head turns part of the way after them (not when an action or the
    // cursor already turns the head)
    const headFollows = !actionLook && !followCursor ? 1 : 0;
    const yaw = this.headYaw.update(this.lookOffset.x * 0.32 * headFollows, dt);
    const pitch = this.headPitch.update(this.lookOffset.y * 0.22 * headFollows, dt);
    pose.addRot("head", -pitch * 0.65, yaw * 0.6, 0);
    pose.addRot("neck", -pitch * 0.35, yaw * 0.4, 0);

    // walking and being carried need snappier limbs to keep up with the cycle and the hand
    this.applySprings(mode === "walk" || mode === "drag" || mode === "fall" ? 1.5 : 1, dt);

    // hips: joy bounce, hops, walking bob (lowest with the legs apart), landing squat, and
    // the pelvis drifting over the loaded leg
    const hips = this.vrm.humanoid.getNormalizedBoneNode("hips");
    if (hips && this.hipsY !== null) {
      const joy = this.emotionWeights.get("joy") ?? 0;
      const walk = this.bodyWeights.get("walk") ?? 0;
      const seated = this.bodyWeights.get("sit") ?? 0;
      const bounce = Math.abs(sin(time * 6)) * 0.012 * joy * standing;
      const bob = (0.5 - Math.abs(sin(this.walkPhase))) * stride * 0.07 * walk;
      // with planted feet the pelvis can travel over the loaded foot and dip a little
      const sway = this.legs ? HIPS_SWAY_IK : HIPS_SWAY;
      const dip = this.legs ? -HIPS_DIP * Math.abs(shift) : 0;
      hips.position.y = this.hipsY + bounce + bob + dip + lift * (1 - seated);
      hips.position.x = this.hipsX + shift * sway * this.axis;
      // clip hips motion (sway, bounce) on top of the procedural offsets
      const clips = this.clips;
      if (clips?.hasHips && clips.weight > 0.001) {
        hips.position.x += (clips.hips.x - this.hipsX) * clips.weight;
        hips.position.y += (clips.hips.y - this.hipsY) * clips.weight;
        hips.position.z = this.hipsZ + (clips.hips.z - this.hipsZ) * clips.weight;
      }
    }

    // whole-body turn toward the walking direction
    const facing = mode === "walk" ? (this.desktop?.facing ?? 0) : 0;
    this.yaw = approach(this.yaw, facing * 1.2, 6, dt);
    this.vrm.scene.rotation.y = this.baseYaw + this.yaw;

    // feet stay planted while standing (mocap clips place their own feet); the unloaded
    // leg's heel comes up a little
    // (the grounding weight comes in quicker than the standing pose: on landing the feet
    // must meet the floor at once instead of sinking through it while the legs straighten)
    this.groundWeight = approach(this.groundWeight, mode === "stand" ? 1 : 0, 25, dt);
    const ik = this.groundWeight * (1 - (this.clips?.weight ?? 0));
    const heel = HEEL_LIFT * (reduced ? 0.5 : 1);
    this.legs?.solve(ik, heel * Math.max(0, -shift), heel * Math.max(0, shift));
  }

  /** Advances an action and adds its pose and face at `weight`; returns its hips lift. */
  private runAction(a: Action, dt: number, weight: number): number {
    a.t += dt * a.rate;
    const t = a.t;
    if (a.keyed) {
      const k = a.keyed;
      if (!a.clip) k.motion.sample(t, this.addRot, weight);
      if (k.face) {
        for (const key in k.face) {
          const curve = k.face[key as keyof typeof k.face];
          if (curve) mergeFace(this.face, { [key]: curve.at(t) }, weight);
        }
      }
      return a.clip ? 0 : (k.lift?.at(t) ?? 0) * weight;
    }
    const w = actionEnvelope(t, a.duration, a.attack, a.release) * weight;
    if (!a.clip && a.pose) this.pose.add(a.pose(t), w);
    if (a.face) mergeFace(this.face, a.face(t), w);
    return a.lift ? a.lift(t) * w : 0;
  }

  private actionLook(a: Action): [number, number] | null {
    const look = a.keyed?.look;
    if (look) return [look[0].at(a.t), look[1].at(a.t)];
    return a.look ? a.look(a.t) : null;
  }

  /**
   * Idle life on top of every pose: breathing, a slow weight shift from leg to leg with the
   * spine counter-rotating to keep the head level, and small uncorrelated drifts of the
   * spine, head, arms and fingers (gradient noise: no visible period, no bones in sync).
   * Returns the weight shift (-1..1) for the hips' sideways drift.
   */
  private idleLife(
    time: number,
    dt: number,
    standing: number,
    reduced: boolean,
    strength = 1,
  ): number {
    const pose = this.pose;
    const amp = (reduced ? 0.35 : 1) * strength;
    const excited = Math.max(this.stateWeights.speaking, this.emotionWeights.get("joy") ?? 0);
    // breathing: ~14 breaths a minute, quicker when excited, slower when drowsy; the inhale
    // is shorter than the exhale and every breath has its own depth
    const rate = 0.23 + excited * 0.08 - this.drowsy * 0.07 + wander(time * 0.05, 1) * 0.025;
    this.breathPhase = (this.breathPhase + dt * rate) % 1;
    const p = this.breathPhase;
    const depth = (1 + wander(time * 0.11, 2) * 0.3) * (reduced ? 0.6 : 1);
    const b = (p < 0.4 ? smoothstep(p / 0.4) : 1 - smoothstep((p - 0.4) / 0.6)) * depth;
    pose.addRot("upperChest", -0.022 * b, 0, 0);
    pose.addRot("chest", -0.014 * b, 0, 0);
    pose.addRot("spine", 0.006 * b, 0, 0);
    pose.addRot("neck", 0.016 * b, 0, 0); // the head stays put while the chest rises
    pose.addRot("leftShoulder", 0, 0, 0.028 * b);
    pose.addRot("rightShoulder", 0, 0, -0.028 * b);
    pose.addRot("leftUpperArm", 0, 0, -0.012 * b);
    pose.addRot("rightUpperArm", 0, 0, 0.012 * b);

    // weight shift: now and then the weight moves to the other leg (standing only)
    this.nextShift -= dt;
    if (this.nextShift <= 0) {
      this.shiftGoal = -Math.sign(this.shiftGoal || 1) * (0.45 + Math.random() * 0.55);
      this.nextShift = 6 + Math.random() * 11;
    }
    const s =
      (this.weightShift.update(this.shiftGoal, dt) + wander(time * 0.13, 3) * 0.25) *
      standing *
      amp;
    const loadL = Math.max(0, s);
    const loadR = Math.max(0, -s);
    pose.addRot("hips", 0, wander(time * 0.08, 4) * 0.035 * amp, 0.04 * s);
    // the spine bends back so the shoulders stay level, the legs counter the pelvis roll
    pose.addRot("spine", 0, -wander(time * 0.08, 4) * 0.02 * amp, -0.032 * s);
    pose.addRot("chest", 0, 0, -0.012 * s);
    pose.addRot("leftUpperLeg", -0.05 * loadR, 0, -0.04 * s);
    pose.addRot("rightUpperLeg", -0.05 * loadL, 0, -0.04 * s);
    // the unloaded leg relaxes: knee forward, heel a little up
    pose.addRot("leftLowerLeg", 0.12 * loadR, 0, 0);
    pose.addRot("rightLowerLeg", 0.12 * loadL, 0, 0);
    pose.addRot("leftFoot", -0.06 * loadR, 0, 0);
    pose.addRot("rightFoot", -0.06 * loadL, 0, 0);

    // micro-motion: each channel wanders on its own
    const a = amp;
    pose.addRot("spine", wander(time * 0.21, 5) * 0.012 * a, wander(time * 0.17, 6) * 0.015 * a, 0);
    pose.addRot("chest", 0, wander(time * 0.23, 7) * 0.01 * a, 0);
    const hx = wander(time * 0.25, 8) * 0.035 * a;
    const hy = wander(time * 0.19, 9) * 0.06 * a;
    const hz = wander(time * 0.16, 10) * 0.035 * a;
    pose.addRot("head", hx, hy, hz - 0.35 * 0.04 * s); // keeps the head near level
    pose.addRot("neck", hx * 0.4, hy * 0.4, hz * 0.3 - 0.2 * 0.04 * s);
    pose.addRot(
      "leftUpperArm",
      wander(time * 0.2, 11) * 0.03 * a,
      0,
      wander(time * 0.15, 12) * 0.02 * a,
    );
    pose.addRot(
      "rightUpperArm",
      wander(time * 0.2, 13) * 0.03 * a,
      0,
      wander(time * 0.15, 14) * 0.02 * a,
    );
    pose.addRot("leftLowerArm", 0, wander(time * 0.22, 15) * 0.05 * a, 0);
    pose.addRot("rightLowerArm", 0, wander(time * 0.22, 16) * 0.05 * a, 0);
    pose.addRot(
      "leftHand",
      wander(time * 0.3, 17) * 0.05 * a,
      0,
      wander(time * 0.27, 18) * 0.04 * a,
    );
    pose.addRot(
      "rightHand",
      wander(time * 0.3, 19) * 0.05 * a,
      0,
      wander(time * 0.27, 20) * 0.04 * a,
    );
    const curlL = wander(time * 0.18, 21) * 0.07 * a;
    const curlR = wander(time * 0.18, 22) * 0.07 * a;
    for (const bone of LEFT_FINGERS) pose.addRot(bone, 0, 0, -curlL);
    for (const bone of RIGHT_FINGERS) pose.addRot(bone, 0, 0, curlR);
    return s;
  }

  /**
   * Speech energy from the voice level: a gated envelope (like the mouth), a slow
   * phrase-level energy, syllable beats (sharp rises after a dip) and silences.
   */
  private updateVoice(state: State, level: number, dt: number, reduced: boolean): void {
    const speaking = state === "speaking";
    const v = speaking ? voiceOpenness(level) : 0;
    this.voice = envelope(this.voice, v, 25, 7, dt);
    this.voiceEnergy = envelope(this.voiceEnergy, v, 2.5, 0.7, dt);
    this.voiceLow = v < this.voiceLow ? v : approach(this.voiceLow, v, 5, dt);
    this.silentFor = v < 0.05 ? this.silentFor + dt : 0;
    this.beatCooldown -= dt;
    this.beatDrive = approach(this.beatDrive, 0, 10, dt);
    this.nodDrive = approach(this.nodDrive, 0, 8, dt);
    this.beat.update(this.beatDrive, dt);
    if (speaking && v - this.voiceLow > 0.3 && this.beatCooldown <= 0) {
      // a stressed syllable: a small nod, stronger on loud ones; not on every syllable
      const strength = Math.min(1, v) * (0.6 + Math.random() * 0.4);
      if (Math.random() < 0.65) this.nodDrive += strength * (reduced ? 0.04 : 0.09);
      this.beatDrive += strength * 0.45;
      this.beatCooldown = 0.3 + Math.random() * 0.35;
      this.voiceLow = v;
    }
    // nod oscillator (head pitch, rad): kicked by beats, settles after a small rebound
    const steps = Math.max(1, Math.ceil(dt / SPRING_STEP));
    const h = dt / steps;
    for (let i = 0; i < steps; i++) {
      this.nodV +=
        (-NOD_OMEGA * NOD_OMEGA * (this.nodX - this.nodDrive) -
          2 * NOD_DAMPING * NOD_OMEGA * this.nodV) *
        h;
      this.nodX += this.nodV * h;
    }
  }

  /** Head and torso motion while talking: beat nods, prosody drift, leaning into loud parts. */
  private speechMotion(time: number, dt: number, reduced: boolean): void {
    const pose = this.pose;
    const talking = this.stateWeights.speaking;
    const energy = this.voiceEnergy * talking;
    // voice gain > 1 (desktop): bigger movement to stay readable; reduced motion: smaller
    const gain = reduced ? 0.5 : this.voiceGain;
    const nod = this.nodX * 0.09 * gain;
    pose.addRot("head", nod - this.voice * talking * 0.025, 0, 0);
    pose.addRot("neck", nod * 0.4, 0, 0);
    pose.addRot(
      "head",
      wander(time * 0.55, 30) * 0.03 * energy * gain,
      wander(time * 0.45, 31) * 0.07 * energy * gain,
      wander(time * 0.4, 32) * 0.05 * energy * gain,
    );
    pose.addRot("spine", 0.025 * energy * gain, wander(time * 0.3, 33) * 0.03 * energy * gain, 0);
    if (gain > 1) {
      const extra = (gain - 1) * energy;
      pose.addRot("leftShoulder", 0, 0, 0.08 * extra * this.voice);
      pose.addRot("rightShoulder", 0, 0, -0.08 * extra * this.voice);
      // listening: lean toward the speaker as the microphone level rises
      const heard = this.stateWeights.listening * this.inputLevel;
      this.listenLean = envelope(this.listenLean, heard, 8, 3, dt);
      pose.addRot("spine", this.listenLean * 0.12, 0, 0);
      pose.addRot("head", this.listenLean * 0.08, 0, 0);
    }
  }

  /** Drive every bone toward the pose through its damped spring (and the VRM 0.x flip). */
  private applySprings(quick: number, dt: number): void {
    const humanoid = this.vrm.humanoid;
    const clipWeight = this.clips?.weight ?? 0;
    const steps = Math.max(1, Math.ceil(dt / SPRING_STEP));
    const h = dt / steps;
    for (const [bone, r] of this.pose.rot) {
      let s = this.springs.get(bone);
      if (!s) {
        s = {
          node: humanoid.getNormalizedBoneNode(bone),
          x: Float64Array.from(r),
          v: new Float64Array(3),
          omega: boneOmega(bone),
          zeta: boneDamping(bone),
        };
        this.springs.set(bone, s);
      }
      if (!s.node) continue;
      const w = s.omega * quick;
      const stiffness = w * w;
      const damping = 2 * s.zeta * w;
      const { x, v } = s;
      for (let i = 0; i < steps; i++) {
        for (let k = 0; k < 3; k++) {
          v[k] += (stiffness * (r[k] - x[k]) - damping * v[k]) * h;
          x[k] += v[k] * h;
        }
      }
      s.node.rotation.set(x[0] * this.axis, x[1], x[2] * this.axis);
      if (clipWeight > 0.001) this.blendClip(bone, s.node, x, clipWeight);
    }
    // bones only the clips move (thumbs, toes…): from the rest pose to the clip by weight
    const clips = this.clips;
    if (!clips) return;
    for (const [bone, q] of clips.pose) {
      if (this.pose.rot.has(bone)) continue;
      humanoid.getNormalizedBoneNode(bone)?.quaternion.identity().slerp(q, clipWeight);
    }
  }

  /**
   * Mocap layer: the clip rotation with the procedural offsets (state poses, gestures,
   * speech, gaze: everything above the relaxed base pose) applied on top, blended in by the
   * layer weight. Bones the procedural layers never touch keep the clip as the mixer set it.
   */
  private blendClip(
    bone: VRMHumanBoneName,
    node: THREE.Object3D,
    x: Float64Array,
    weight: number,
  ): void {
    const clip = this.clips?.pose.get(bone);
    if (!clip) return;
    const base = BASE_POSE[bone];
    const offset = this.tmpEuler.set(
      (x[0] - (base?.[0] ?? 0)) * this.axis,
      x[1] - (base?.[1] ?? 0),
      (x[2] - (base?.[2] ?? 0)) * this.axis,
    );
    const target = this.tmpQuat.setFromEuler(offset).premultiply(clip);
    node.quaternion.slerp(target, weight);
  }

  /** Spontaneous reactions: emotion one-shots, listening nods, idle actions, drowsiness. */
  private react(state: State, emotion: Emotion, level: number, dt: number, reduced: boolean): void {
    if (emotion !== this.lastEmotion) {
      const options = EMOTION_ACTIONS[emotion];
      if (options && !reduced) this.play(options[Math.floor(Math.random() * options.length)]);
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
      this.heardPeak = Math.max(this.heardPeak * Math.exp(-dt * 0.5), level);
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
    // with reduced motion the character stays calm: no fidgeting on her own
    if (this.nextIdle <= 0 && !this.action && emotion === "neutral" && !reduced) {
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
    this.peekLean = approach(this.peekLean, goal, 3, dt);
    return this.peekLean * (this.desktop?.facing || 1);
  }

  /** The carried body swings like a pendulum, pushed by the hand's acceleration. */
  private updateSwing(mode: BodyMode, dt: number): void {
    const vx = mode === "drag" ? (this.desktop?.vx ?? 0) : 0;
    if (dt > 0) {
      const raw = (vx - this.lastVx) / dt;
      this.accelX = approach(this.accelX, raw, 12, dt);
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

  /**
   * Speech gestures: a phrase-opening gesture when the voice starts after a pause, shorter
   * ones inside long phrases; when the phrase ends (silence) the hands go back to rest.
   */
  private updateGesture(state: State, dt: number, reduced: boolean): void {
    const phraseStart = this.silentFor === 0 && this.voice > 0.2 && this.lastSilence > 0.25;
    this.lastSilence = this.silentFor > 0 ? this.silentFor : this.lastSilence * Math.exp(-dt * 8);
    for (const g of [this.gesture, this.gestureOut]) {
      if (!g) continue;
      g.t += dt * g.rate;
      if (g.releasing) g.fade = Math.max(0, g.fade - dt / GESTURE_RELEASE);
    }
    const out = this.gestureOut;
    if (out && (out.fade <= 0 || out.t >= out.gesture.motion.duration)) this.gestureOut = null;
    const g = this.gesture;
    if (g) {
      if (g.fade <= 0 || g.t >= g.gesture.motion.duration) this.gesture = null;
      else if (!g.releasing) {
        // the phrase is over (or the talk is): ease back to rest from wherever the hands are
        if (state !== "speaking" || this.silentFor > 0.35 || this.busyHands()) g.releasing = true;
        else return;
      }
    }
    if (state !== "speaking" || this.busyHands()) {
      this.nextGesture = 0.4;
      return;
    }
    // livelier speech gestures more; a new phrase after a pause is a natural moment to start
    this.nextGesture -= dt * (0.5 + this.voiceEnergy);
    if (this.nextGesture > 0 && !(phraseStart && this.nextGesture < 0.9)) return;
    if (this.voiceEnergy < 0.08 && !phraseStart) return; // waiting out a pause
    const next = pickGesture(this.voiceEnergy, phraseStart, this.recentGestures, this.lastHand);
    const gain = Math.min(1.1, 0.7 + this.voiceEnergy * 0.45) * (reduced ? 0.5 : 1);
    this.startGesture(next, gain);
    this.nextGesture = 0.5 + Math.random() * 1.2;
  }

  private startGesture(next: Gesture, gain: number): void {
    this.recentGestures = [next.name, ...this.recentGestures].slice(0, 2);
    if (next.hand) this.lastHand = next.hand;
    // the one still easing out keeps fading under the new one (co-articulation)
    if (this.gesture) this.gestureOut = this.gesture;
    this.gesture = {
      gesture: next,
      t: 0,
      rate: 0.9 + Math.random() * 0.25,
      gain,
      fade: 1,
      releasing: false,
    };
  }

  /** Debug (`?debug`): start a speech gesture by name (it runs while the state is speaking). */
  gestureNow(name: string, gain = 1): void {
    const gesture = GESTURES.find((g) => g.name === name);
    if (gesture) this.startGesture(gesture, gain);
  }

  /** An action that moves the arms runs: no speech gestures on top of it. */
  private busyHands(): boolean {
    return !!this.action && !HEAD_ACTIONS.has(this.action.name);
  }

  private setLookGoal(x: number, y: number): void {
    const jump = Math.hypot(x - this.lookGoal.x, y - this.lookGoal.y);
    if (jump > 0.05) this.gazeShift = Math.max(this.gazeShift, jump);
    this.lookGoal.set(x, y, 0);
  }

  /**
   * Eye gaze: mostly at the user (the camera), glances away now and then (more often while
   * talking, like people searching for words), with micro-saccades. The eyes jump quickly to
   * a new target, the head follows slower (see `update`).
   */
  private updateGaze(
    state: State,
    time: number,
    dt: number,
    actionLook: [number, number] | null,
    reduced: boolean,
  ): void {
    this.nextGlance -= dt;
    this.nextSaccade -= dt;
    const cursor = this.desktop?.look;
    if (actionLook) {
      this.setLookGoal(actionLook[0], actionLook[1]);
    } else if (state === "thinking") {
      this.setLookGoal(0.35, 0.45); // up and aside
    } else if (cursor) {
      this.setLookGoal(this.cursorX.value * 0.8, this.cursorY.value * 0.6);
    } else if (this.nextGlance <= 0 && (state === "idle" || state === "speaking")) {
      const away =
        this.lookGoal.lengthSq() < 1e-4 && Math.random() < (state === "idle" ? 0.4 : 0.3);
      if (away) {
        const side = Math.random() < 0.5 ? -1 : 1;
        this.setLookGoal(side * (0.25 + Math.random() * 0.5), (Math.random() - 0.4) * 0.35);
        // a glance away is short while talking, can linger in idle
        this.nextGlance = state === "idle" ? 1.2 + Math.random() * 2.5 : 0.5 + Math.random() * 0.6;
      } else {
        this.setLookGoal(0, 0);
        this.nextGlance = 2 + Math.random() * 4;
      }
    } else if (state === "listening") {
      this.setLookGoal(0, 0); // attentive: eyes on the user
    }
    // micro-saccades keep the gaze from looking frozen
    if (this.nextSaccade <= 0) {
      const size = reduced ? 0.5 : 1;
      this.saccade.set((Math.random() - 0.5) * 0.06 * size, (Math.random() - 0.5) * 0.04 * size, 0);
      this.nextSaccade = 0.3 + Math.random() * 1.4;
    }
    // eyes move fast (a saccade takes ~50 ms), not a slow drift
    this.lookOffset.lerp(this.lookGoal, follow(16, dt));
    this.lookTarget.position.set(
      this.lookOffset.x + this.saccade.x,
      this.lookOffset.y + this.saccade.y + noise1(time * 0.3) * 0.03,
      0,
    );
  }

  dispose(): void {
    this.lookTarget.removeFromParent();
  }
}
