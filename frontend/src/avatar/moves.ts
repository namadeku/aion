// Keyframed one-off actions and speech-gesture phrases for the VRM avatar (see keyframes.ts).
//
// Every move follows the classic animation beats: anticipation (a small move the other way),
// the action itself, follow-through (the hand and fingers trail the arm, the head trails the
// torso) and a settle back to rest. Rotations are offsets on top of the relaxed base pose in
// VRM 1.0 convention (see motion.ts): right arm Z < 0 raises it, X < 0 swings it forward;
// forearm Y bends the elbow (right +, left −); finger Z curls them (right +, left −).

import type { VRMHumanBoneName } from "@pixiv/three-vrm";
import { Curve, KeyedMotion, mirror, type Key, type ScalarKey, type Tracks } from "./keyframes";

type Rot = readonly [number, number, number];
type PoseSpec = Partial<Record<VRMHumanBoneName, Rot>>;

const FINGERS = ["Index", "Middle", "Ring", "Little"] as const;
const SEGMENTS = ["Proximal", "Intermediate", "Distal"] as const;

/** Curl of each finger segment in the base pose (right hand: +Z). */
const BASE_CURL = [0.18, 0.3, 0.42];

/** Finger keys for one hand: `amount` × base curl over time (−1 opens the hand flat). */
function fingerTracks(
  side: "left" | "right",
  keys: readonly ScalarKey[],
  only?: readonly (typeof FINGERS)[number][],
): Tracks {
  const sign = side === "right" ? 1 : -1;
  const out: Tracks = {};
  for (const finger of only ?? FINGERS) {
    SEGMENTS.forEach((segment, i) => {
      const bone = `${side}${finger}${segment}` as VRMHumanBoneName;
      out[bone] = keys.map(([t, v]) => [t, 0, 0, v * BASE_CURL[i] * sign] as const);
    });
  }
  return out;
}

/** Scale a rotation. */
const sc = (r: Rot, s: number): [number, number, number] => [r[0] * s, r[1] * s, r[2] * s];

/**
 * A gesture phrase from a pose: preparation (part of the way), stroke (slightly past the
 * pose, the accent), hold (settles onto `hold` or the pose), retraction back to rest.
 */
function phrase(
  pose: PoseSpec,
  timing: { prep: number; stroke: number; end: number; out: number },
  hold: PoseSpec = pose,
  overshoot = 1.08,
): Tracks {
  const { prep, stroke, end, out } = timing;
  const tracks: Tracks = {};
  for (const bone in pose) {
    const p = pose[bone as VRMHumanBoneName];
    if (!p) continue;
    const q = hold[bone as VRMHumanBoneName] ?? p;
    tracks[bone as VRMHumanBoneName] = [
      [0, 0, 0, 0],
      [prep, ...sc(p, 0.45)],
      [stroke, ...sc(p, overshoot)],
      [end - out, ...q],
      [end, 0, 0, 0],
    ];
  }
  return tracks;
}

const merge = (...parts: Tracks[]): Tracks => Object.assign({}, ...parts) as Tracks;

// -- wave ---------------------------------------------------------------------------------

/** Wave: anticipation, the arm rises, the forearm swings with the wrist trailing, settle. */
function waveTracks(): Tracks {
  const rise = 0.8; // the hand is up
  const t0 = 1.0; // first swing extreme
  const half = 0.33; // half a swing: 1.5 swings a second, a relaxed friendly wave
  const amps = [0.14, 0.23, 0.24, 0.22, 0.13, 0];
  const end = t0 + half * (amps.length - 1);
  const forearm: Key[] = [
    [0, 0, 0, 0],
    [0.2, 0, 0.2, 0], // anticipation: the elbow tucks in a little
    [rise, 0, -0.3, -1.2],
  ];
  const hand: Key[] = [
    [0, 0, 0, 0],
    [rise, 0, 0, -0.12],
  ];
  const upper: Key[] = [
    [0, 0, 0, 0],
    [0.18, 0.07, 0, 0.05], // a small dip back before the arm rises
    [rise, -0.19, 0.2, -1.54], // a touch past the hold height…
    [t0 + half, -0.15, 0.2, -1.48], // …and settles
  ];
  amps.forEach((amp, i) => {
    const s = i % 2 === 0 ? 1 : -1;
    const t = t0 + i * half;
    forearm.push([t, 0, -0.3, -1.2 + s * amp]);
    // the loose wrist trails the forearm: it flaps the other way
    hand.push([t, 0, 0, -0.12 - s * amp * 0.6]);
  });
  forearm.push([end + 0.55, 0, 0.12, -0.3], [end + 1.0, 0, 0, 0]);
  hand.push([end + 0.6, 0, 0, 0.05], [end + 1.0, 0, 0, 0]);
  upper.push([end, -0.15, 0.2, -1.46], [end + 0.6, 0.04, 0.05, -0.22], [end + 1.0, 0, 0, 0]);
  return merge(
    {
      rightUpperArm: upper,
      rightLowerArm: forearm,
      rightHand: hand,
      rightShoulder: [
        [0, 0, 0, 0],
        [0.18, 0, 0, 0.04],
        [rise, 0, 0, -0.09],
        [end, 0, 0, -0.07],
        [end + 0.7, 0, 0, 0],
      ],
      // the torso leans away a touch to balance the raised arm
      spine: [
        [0, 0, 0, 0],
        [0.2, 0.02, 0, 0],
        [rise + 0.1, -0.01, 0.03, -0.035],
        [end, -0.01, 0.02, -0.03],
        [end + 0.8, 0, 0, 0],
      ],
      // a little nod of greeting, the head tilted toward the waving hand
      head: [
        [0, 0, 0, 0],
        [0.25, 0.03, 0, 0],
        [rise + 0.1, -0.04, 0.06, -0.09],
        [rise + 0.65, 0.03, 0.07, -0.1],
        [rise + 1.1, -0.03, 0.06, -0.08],
        [end + 0.2, -0.02, 0.04, -0.06],
        [end + 0.9, 0, 0, 0],
      ],
      leftUpperArm: [
        [0, 0, 0, 0],
        [rise, 0.04, 0, -0.05],
        [end, 0.04, 0, -0.05],
        [end + 0.8, 0, 0, 0],
      ],
    },
    fingerTracks("right", [
      [0, 0],
      [rise - 0.1, -0.75],
      [end, -0.7],
      [end + 0.7, 0],
    ]),
  );
}

// -- stretch ------------------------------------------------------------------------------

function stretchTracks(): Tracks {
  const right: Tracks = {
    rightShoulder: [
      [0, 0, 0, 0],
      [0.45, 0, 0, 0.05], // shoulders drop first
      [1.5, 0, 0, -0.12],
      [2.6, 0, 0, -0.14],
      [3.3, 0, 0, 0.04],
      [3.9, 0, 0, 0],
    ],
    rightUpperArm: [
      [0, 0, 0, 0],
      [0.45, -0.2, 0, 0.04],
      [0.95, -0.75, 0, -1.0], // up through the front…
      [1.55, -0.2, 0, -2.42], // …overhead
      [2.1, -0.22, 0, -2.55], // reaching higher
      [2.6, -0.2, 0, -2.45],
      [3.0, -0.05, 0, -1.3], // down to the sides
      [3.45, 0.02, 0, 0.06],
      [3.9, 0, 0, 0],
    ],
    rightLowerArm: [
      [0, 0, 0, 0],
      [0.95, 0, 0.9, 0], // elbows bent on the way up…
      [1.6, 0, -0.3, -0.2], // …straighten at the top
      [2.6, 0, -0.32, -0.2],
      [3.1, 0, 0.1, 0],
      [3.9, 0, 0, 0],
    ],
    rightHand: [
      [0, 0, 0, 0],
      [1.6, 0, 0, -0.2],
      [2.6, 0, 0, -0.25],
      [3.2, 0, 0, 0.1],
      [3.9, 0, 0, 0],
    ],
  };
  return merge(
    right,
    mirror(right),
    fingerTracks("right", [
      [0, 0],
      [1.5, -0.8],
      [2.6, -0.9],
      [3.3, 0],
    ]),
    fingerTracks("left", [
      [0, 0],
      [1.5, -0.8],
      [2.6, -0.9],
      [3.3, 0],
    ]),
    {
      spine: [
        [0, 0, 0, 0],
        [0.45, 0.05, 0, 0],
        [1.5, -0.08, 0, 0.02],
        [2.0, -0.1, 0, 0.045],
        [2.5, -0.1, 0, -0.035],
        [3.0, -0.02, 0, 0],
        [3.45, 0.035, 0, 0], // the exhale: a little slump…
        [3.9, 0, 0, 0], // …and back
      ],
      chest: [
        [0, 0, 0, 0],
        [1.5, -0.07, 0, 0],
        [2.5, -0.08, 0, 0],
        [3.2, 0, 0, 0],
      ],
      head: [
        [0, 0, 0, 0],
        [0.45, 0.06, 0, 0],
        [1.6, -0.15, 0, 0],
        [2.5, -0.15, 0, 0.03],
        [3.2, 0.02, 0, 0],
        [3.9, 0, 0, 0],
      ],
    },
  );
}

// -- hop ----------------------------------------------------------------------------------

/** Hips height over the hop (m): crouch, take-off, landing absorbed, a small rebound. */
const HOP_LIFT: ScalarKey[] = [
  [0, 0],
  [0.2, -0.05],
  [0.37, 0.075],
  [0.46, 0.07],
  [0.6, -0.035],
  [0.74, 0.004],
  [0.85, 0.02],
  [0.97, -0.008],
  [1.15, 0],
];

function hopTracks(): Tracks {
  const right: Tracks = {
    rightUpperArm: [
      [0, 0, 0, 0],
      [0.22, 0.18, 0, 0.05], // arms swing back in the crouch…
      [0.42, -0.32, 0, -0.38], // …and up with the take-off
      [0.66, -0.08, 0, -0.26], // out for balance on landing
      [1.15, 0, 0, 0],
    ],
    rightLowerArm: [
      [0, 0, 0, 0],
      [0.22, 0, 0.25, 0],
      [0.44, 0, 0.55, 0],
      [0.68, 0, 0.4, 0],
      [1.15, 0, 0, 0],
    ],
  };
  return merge(right, mirror(right), {
    spine: [
      [0, 0, 0, 0],
      [0.2, 0.11, 0, 0],
      [0.4, -0.04, 0, 0],
      [0.62, 0.07, 0, 0],
      [0.85, -0.01, 0, 0],
      [1.15, 0, 0, 0],
    ],
    head: [
      [0, 0, 0, 0],
      [0.2, -0.08, 0, 0],
      [0.4, 0.02, 0, 0],
      [0.62, -0.06, 0, 0],
      [1.1, 0, 0, 0],
    ],
  });
}

// -- clap ---------------------------------------------------------------------------------

function clapTracks(): Tracks {
  const claps = [0.5, 0.8, 1.1, 1.4];
  const forearm: Key[] = [
    [0, 0, 0, 0],
    [0.38, 0, 1.0, 0],
  ];
  claps.forEach((t, i) => {
    forearm.push([t, 0, 1.42, 0]); // palms meet
    if (i < claps.length - 1) forearm.push([t + 0.15, 0, 1.08, 0]);
  });
  forearm.push([1.6, 0, 1.1, 0], [2.0, 0, 0, 0]);
  const right: Tracks = {
    rightUpperArm: [
      [0, 0, 0, 0],
      [0.35, -0.75, 0.35, 0.05],
      [1.55, -0.72, 0.35, 0.05],
      [2.0, 0, 0, 0],
    ],
    rightLowerArm: forearm,
  };
  const bob: Key[] = [[0, 0, 0, 0]];
  claps.forEach((t) => bob.push([t + 0.02, -0.05, 0, 0], [t + 0.15, -0.02, 0, 0]));
  bob.push([2.0, 0, 0, 0]);
  return merge(right, mirror(right), {
    head: bob,
    spine: [
      [0, 0, 0, 0],
      [0.4, 0.03, 0, 0],
      [1.5, 0.03, 0, 0],
      [2.0, 0, 0, 0],
    ],
  });
}

// -- smaller actions ----------------------------------------------------------------------

function hairTouchTracks(): Tracks {
  return merge(
    {
      rightUpperArm: [
        [0, 0, 0, 0],
        [0.2, 0.05, 0, 0.05],
        [0.85, -1.3, 0.2, -1.45],
        [2.0, -1.28, 0.2, -1.42],
        [2.55, -0.25, 0.05, -0.2],
        [2.9, 0, 0, 0],
      ],
      rightLowerArm: [
        [0, 0, 0, 0],
        [0.85, 0, 2.2, 0],
        [1.15, 0, 2.34, 0], // two strokes along the hair
        [1.45, 0, 2.12, 0],
        [1.75, 0, 2.3, 0],
        [2.0, 0, 2.2, 0],
        [2.6, 0, 0.3, 0],
        [2.9, 0, 0, 0],
      ],
      rightHand: [
        [0, 0, 0, 0],
        [0.85, 0, 0, 0.3],
        [1.15, 0, 0, 0.45],
        [1.45, 0, 0, 0.22],
        [1.75, 0, 0, 0.42],
        [2.1, 0, 0, 0.3],
        [2.9, 0, 0, 0],
      ],
      head: [
        [0, 0, 0, 0],
        [0.9, 0, -0.05, 0.12],
        [1.5, 0.02, -0.06, 0.15],
        [2.1, 0, -0.04, 0.1],
        [2.9, 0, 0, 0],
      ],
      neck: [
        [0, 0, 0, 0],
        [0.9, 0, 0, 0.05],
        [2.2, 0, 0, 0.05],
        [2.9, 0, 0, 0],
      ],
    },
    fingerTracks("right", [
      [0, 0],
      [0.8, -0.4],
      [2.1, -0.4],
      [2.8, 0],
    ]),
  );
}

function yawnTracks(): Tracks {
  return {
    rightUpperArm: [
      [0, 0, 0, 0],
      [0.5, -0.3, 0.1, -0.05],
      [1.0, -0.8, 0.3, -0.25],
      [2.45, -0.78, 0.3, -0.24],
      [3.0, -0.15, 0.05, 0.02],
      [3.4, 0, 0, 0],
    ],
    rightLowerArm: [
      [0, 0, 0, 0],
      [1.0, 0, 2.25, 0],
      [2.45, 0, 2.2, 0],
      [3.1, 0, 0.2, 0],
      [3.4, 0, 0, 0],
    ],
    rightHand: [
      [0, 0, 0, 0],
      [1.05, 0, 0, 0.3],
      [2.4, 0, 0, 0.3],
      [3.4, 0, 0, 0],
    ],
    // the inhale lifts the shoulders and tips the head back; the exhale drops them
    leftShoulder: [
      [0, 0, 0, 0],
      [1.1, 0, 0, 0.08],
      [2.4, 0, 0, 0.06],
      [2.9, 0, 0, -0.03],
      [3.4, 0, 0, 0],
    ],
    rightShoulder: [
      [0, 0, 0, 0],
      [1.1, 0, 0, -0.08],
      [2.4, 0, 0, -0.06],
      [2.9, 0, 0, 0.03],
      [3.4, 0, 0, 0],
    ],
    head: [
      [0, 0, 0, 0],
      [0.3, 0.03, 0, 0],
      [1.2, -0.14, 0, 0.05],
      [2.4, -0.12, 0, 0.06],
      [2.9, 0.05, 0, 0.02],
      [3.4, 0, 0, 0],
    ],
    spine: [
      [0, 0, 0, 0],
      [1.2, -0.05, 0, 0],
      [2.4, -0.04, 0, 0],
      [2.9, 0.03, 0, 0],
      [3.4, 0, 0, 0],
    ],
  };
}

function startleTracks(): Tracks {
  const right: Tracks = phrase(
    {
      rightUpperArm: [-0.7, 0.3, -0.2],
      rightLowerArm: [0, 2.0, 0],
      rightHand: [0, 0, -0.2],
    },
    { prep: 0.05, stroke: 0.14, end: 1.5, out: 0.75 },
    { rightUpperArm: [-0.6, 0.28, -0.15], rightLowerArm: [0, 1.8, 0], rightHand: [0, 0, -0.1] },
    1.05,
  );
  return merge(
    right,
    mirror(right),
    phrase(
      { spine: [-0.12, 0, 0], head: [-0.08, 0, 0], neck: [-0.03, 0, 0] },
      { prep: 0.04, stroke: 0.12, end: 1.5, out: 0.8 },
      { spine: [-0.06, 0, 0], head: [-0.05, 0, 0], neck: [-0.02, 0, 0] },
    ),
  );
}

function sighTracks(): Tracks {
  return {
    leftShoulder: [
      [0, 0, 0, 0],
      [0.9, 0, 0, 0.16],
      [1.7, 0, 0, -0.05],
      [2.6, 0, 0, 0],
    ],
    rightShoulder: [
      [0, 0, 0, 0],
      [0.9, 0, 0, -0.16],
      [1.7, 0, 0, 0.05],
      [2.6, 0, 0, 0],
    ],
    chest: [
      [0, 0, 0, 0],
      [0.9, -0.05, 0, 0],
      [1.7, 0.04, 0, 0],
      [2.6, 0, 0, 0],
    ],
    spine: [
      [0, 0, 0, 0],
      [0.9, -0.02, 0, 0],
      [1.8, 0.05, 0, 0],
      [2.6, 0, 0, 0],
    ],
    head: [
      [0, 0, 0, 0],
      [0.9, -0.04, 0, 0],
      [1.8, 0.13, 0, 0.03],
      [2.6, 0, 0, 0],
    ],
  };
}

function nodTracks(): Tracks {
  return {
    head: [
      [0, 0, 0, 0],
      [0.17, 0.14, 0, 0],
      [0.36, -0.025, 0, 0],
      [0.52, 0.04, 0, 0],
      [0.78, 0, 0, 0],
    ],
    neck: [
      [0, 0, 0, 0],
      [0.15, 0.05, 0, 0],
      [0.36, -0.01, 0, 0],
      [0.7, 0, 0, 0],
    ],
  };
}

function curiousTracks(): Tracks {
  return phrase(
    { head: [0, 0.06, 0.16], neck: [0, 0, 0.06], spine: [0.02, 0, 0.02] },
    { prep: 0.15, stroke: 0.4, end: 1.8, out: 0.55 },
    { head: [0.01, 0.05, 0.14], neck: [0, 0, 0.05], spine: [0.02, 0, 0.02] },
    1.06,
  );
}

function pokeTracks(): Tracks {
  return phrase(
    { spine: [0.04, 0, 0.08], head: [0.05, 0, -0.14], leftShoulder: [0, 0, 0.1] },
    { prep: 0.03, stroke: 0.1, end: 1.5, out: 0.7 },
    { spine: [0.03, 0, 0.05], head: [0.04, 0, -0.1], leftShoulder: [0, 0, 0.06] },
    1.1,
  );
}

/** Yaw of the look-around (rad): turn, linger, turn the other way, linger, back. */
const LOOK_AROUND: ScalarKey[] = [
  [0, 0],
  [0.75, 0.44],
  [1.6, 0.38],
  [2.55, -0.45],
  [3.4, -0.4],
  [4.1, 0],
  [4.4, 0],
];

function lookAroundTracks(): Tracks {
  const yaw = (s: number): Key[] => LOOK_AROUND.map(([t, v]) => [t, 0, v * s, 0] as const);
  return { head: yaw(0.6), neck: yaw(0.4), spine: yaw(0.15) };
}

// -- registry -----------------------------------------------------------------------------

/** A compiled keyframed action, plus optional scalar curves. */
export interface KeyedAction {
  motion: KeyedMotion;
  lift?: Curve;
  face?: Partial<Record<"happy" | "eyesClosed" | "aa" | "surprised", Curve>>;
  /** Gaze offset over time (x, y) in camera space. */
  look?: [Curve, Curve];
}

function compile(): Record<string, KeyedAction> {
  const wave = new KeyedMotion(waveTracks());
  return {
    wave: {
      motion: wave,
      face: {
        happy: new Curve([
          [0, 0],
          [0.4, 0.65],
          [wave.duration - 0.7, 0.6],
          [wave.duration, 0],
        ]),
      },
    },
    stretch: {
      motion: new KeyedMotion(stretchTracks()),
      face: {
        eyesClosed: new Curve([
          [0.5, 0],
          [1.2, 0.85],
          [2.8, 0.85],
          [3.3, 0],
        ]),
        aa: new Curve([
          [0.9, 0],
          [1.4, 0.4],
          [2.3, 0.3],
          [2.7, 0],
        ]),
      },
    },
    hop: {
      motion: new KeyedMotion(hopTracks()),
      lift: new Curve(HOP_LIFT),
      face: {
        happy: new Curve([
          [0, 0.3],
          [0.3, 0.9],
          [0.9, 0.9],
          [1.2, 0],
        ]),
      },
    },
    clap: {
      motion: new KeyedMotion(clapTracks()),
      face: {
        happy: new Curve([
          [0, 0],
          [0.3, 0.9],
          [1.6, 0.9],
          [2.0, 0],
        ]),
      },
    },
    hairTouch: {
      motion: new KeyedMotion(hairTouchTracks()),
      face: {
        happy: new Curve([
          [0, 0],
          [0.8, 0.3],
          [2.2, 0.25],
          [2.9, 0],
        ]),
      },
    },
    yawn: {
      motion: new KeyedMotion(yawnTracks()),
      face: {
        aa: new Curve([
          [0.3, 0],
          [1.0, 0.85],
          [2.3, 0.8],
          [2.8, 0],
        ]),
        eyesClosed: new Curve([
          [0.2, 0],
          [0.9, 0.9],
          [2.6, 0.9],
          [3.1, 0],
        ]),
      },
    },
    startle: {
      motion: new KeyedMotion(startleTracks()),
      lift: new Curve([
        [0, 0],
        [0.1, 0.02],
        [0.3, 0],
        [1.5, 0],
      ]),
      face: {
        surprised: new Curve([
          [0, 0],
          [0.08, 0.9],
          [0.9, 0.8],
          [1.5, 0],
        ]),
      },
    },
    sigh: {
      motion: new KeyedMotion(sighTracks()),
      face: {
        eyesClosed: new Curve([
          [0.8, 0],
          [1.2, 0.6],
          [2.0, 0.5],
          [2.5, 0],
        ]),
      },
    },
    nod: { motion: new KeyedMotion(nodTracks()) },
    curious: { motion: new KeyedMotion(curiousTracks()) },
    poke: {
      motion: new KeyedMotion(pokeTracks()),
      face: {
        happy: new Curve([
          [0, 0],
          [0.15, 0.7],
          [1.0, 0.7],
          [1.5, 0],
        ]),
        eyesClosed: new Curve([
          [0, 0],
          [0.1, 0.7],
          [1.0, 0.7],
          [1.5, 0],
        ]),
      },
    },
    lookAround: {
      motion: new KeyedMotion(lookAroundTracks()),
      // the eyes lead the head by a moment
      look: [
        new Curve(LOOK_AROUND.map(([t, v]) => [Math.max(0, t - 0.15), v * 2] as const)),
        new Curve([
          [0, 0.05],
          [4.4, 0.05],
        ]),
      ],
    },
  };
}

export const KEYED_ACTIONS: Record<string, KeyedAction> = compile();

// -- speech gestures ----------------------------------------------------------------------

/** A short gesture phrase accompanying speech. */
export interface Gesture {
  name: string;
  motion: KeyedMotion;
  /** Which hand gestures ("" for head/shoulders only): consecutive ones alternate hands. */
  hand: "left" | "right" | "both" | "";
  /** Fits phrases of this voice energy (0..1). */
  energy: [number, number];
  /** Long gestures open a phrase; short ones fit in the middle of one. */
  long: boolean;
}

function openPalm(): Tracks {
  return merge(
    phrase(
      {
        rightUpperArm: [-0.6, 0.1, -0.12],
        rightLowerArm: [0, 0.85, 0],
        rightHand: [0, -0.7, 0.25],
      },
      { prep: 0.25, stroke: 0.5, end: 1.8, out: 0.5 },
      {
        rightUpperArm: [-0.56, 0.1, -0.16],
        rightLowerArm: [0, 0.78, 0],
        rightHand: [0, -0.72, 0.22],
      },
    ),
    fingerTracks("right", [
      [0, 0],
      [0.45, -0.5],
      [1.4, -0.4],
      [1.8, 0],
    ]),
  );
}

function beat(): Tracks {
  // the forearm comes up and gives two small downward flicks on the stressed words
  return merge(
    phrase(
      { rightUpperArm: [-0.35, 0.1, -0.12], rightShoulder: [0, 0, -0.03] },
      { prep: 0.2, stroke: 0.38, end: 1.45, out: 0.45 },
    ),
    {
      rightLowerArm: [
        [0, 0, 0, 0],
        [0.3, 0, 0.95, 0],
        [0.48, 0, 0.74, 0],
        [0.68, 0, 0.95, 0],
        [0.86, 0, 0.78, 0],
        [1.0, 0, 0.9, 0],
        [1.45, 0, 0, 0],
      ],
      rightHand: [
        [0, 0, 0, 0],
        [0.3, 0, -0.4, 0.12],
        [0.5, 0, -0.4, -0.05],
        [0.7, 0, -0.4, 0.12],
        [0.88, 0, -0.4, -0.02],
        [1.45, 0, 0, 0],
      ],
    },
  );
}

function twoHandsOpen(): Tracks {
  // both palms up, spreading apart a little through the hold ("you see…")
  const right = phrase(
    {
      rightUpperArm: [-0.5, 0.1, -0.3],
      rightLowerArm: [0, 1.0, 0],
      rightHand: [0, -0.6, 0.15],
    },
    { prep: 0.28, stroke: 0.55, end: 2.0, out: 0.55 },
    {
      rightUpperArm: [-0.45, 0.06, -0.42],
      rightLowerArm: [0, 0.85, 0],
      rightHand: [0, -0.66, 0.1],
    },
  );
  const fingers: ScalarKey[] = [
    [0, 0],
    [0.5, -0.55],
    [1.5, -0.5],
    [2.0, 0],
  ];
  return merge(
    right,
    mirror(right),
    fingerTracks("right", fingers),
    fingerTracks("left", fingers),
    phrase({ spine: [0.03, 0, 0] }, { prep: 0.3, stroke: 0.6, end: 2.0, out: 0.6 }),
  );
}

function point(): Tracks {
  // lean in a touch, the hand goes forward with the index finger out
  const fingersIn: ScalarKey[] = [
    [0, 0],
    [0.35, 0.6],
    [1.2, 0.6],
    [1.6, 0],
  ];
  const indexOut: ScalarKey[] = [
    [0, 0],
    [0.35, -0.9],
    [1.2, -0.85],
    [1.6, 0],
  ];
  return merge(
    phrase(
      {
        rightUpperArm: [-0.9, 0.1, -0.45],
        rightLowerArm: [0, 0.5, 0],
        rightHand: [0, 0, 0.05],
        spine: [0.05, 0, 0],
        head: [0.03, 0.05, 0],
      },
      { prep: 0.2, stroke: 0.42, end: 1.6, out: 0.5 },
      {
        rightUpperArm: [-0.85, 0.1, -0.42],
        rightLowerArm: [0, 0.55, 0],
        rightHand: [0, 0, 0.05],
        spine: [0.04, 0, 0],
        head: [0.03, 0.04, 0],
      },
      1.12,
    ),
    fingerTracks("right", fingersIn, ["Middle", "Ring", "Little"]),
    fingerTracks("right", indexOut, ["Index"]),
  );
}

function shrug(): Tracks {
  const right = phrase(
    {
      rightShoulder: [0, 0, -0.14],
      rightUpperArm: [-0.2, 0, -0.15],
      rightLowerArm: [0, 0.7, 0],
      rightHand: [0, -0.5, 0.2],
    },
    { prep: 0.15, stroke: 0.35, end: 1.35, out: 0.5 },
    {
      rightShoulder: [0, 0, -0.1],
      rightUpperArm: [-0.2, 0, -0.18],
      rightLowerArm: [0, 0.68, 0],
      rightHand: [0, -0.5, 0.2],
    },
  );
  return merge(
    right,
    mirror(right),
    phrase(
      { head: [0, 0, 0.1], neck: [0, 0, 0.04] },
      { prep: 0.15, stroke: 0.4, end: 1.35, out: 0.5 },
    ),
  );
}

function handToChest(): Tracks {
  return merge(
    phrase(
      {
        rightUpperArm: [-0.2, 0.35, 0.22],
        rightLowerArm: [0, 1.45, 0],
        rightHand: [0, -0.3, 0.1],
      },
      { prep: 0.3, stroke: 0.62, end: 2.0, out: 0.6 },
      undefined,
      1.04,
    ),
    fingerTracks("right", [
      [0, 0],
      [0.6, -0.6],
      [1.5, -0.6],
      [2.0, 0],
    ]),
    {
      // a small sincere nod while the hand rests on the chest
      head: [
        [0, 0, 0, 0],
        [0.6, 0.02, 0, 0.05],
        [0.9, 0.08, 0, 0.05],
        [1.3, 0.03, 0, 0.05],
        [2.0, 0, 0, 0],
      ],
    },
  );
}

function frame(): Tracks {
  // both hands in front, palms facing, shaping something with two small accents
  const right: Tracks = {
    rightUpperArm: [
      [0, 0, 0, 0],
      [0.25, -0.3, 0.15, 0],
      [0.5, -0.66, 0.3, -0.08],
      [0.8, -0.6, 0.3, -0.06],
      [1.1, -0.66, 0.3, -0.08],
      [1.4, -0.62, 0.3, -0.07],
      [1.9, 0, 0, 0],
    ],
    rightLowerArm: [
      [0, 0, 0, 0],
      [0.5, 0, 1.15, 0],
      [0.8, 0, 1.0, 0],
      [1.1, 0, 1.15, 0],
      [1.4, 0, 1.05, 0],
      [1.9, 0, 0, 0],
    ],
    rightHand: [
      [0, 0, 0, 0],
      [0.5, 0.5, 0, 0],
      [1.4, 0.5, 0, 0],
      [1.9, 0, 0, 0],
    ],
  };
  const open: ScalarKey[] = [
    [0, 0],
    [0.5, -0.45],
    [1.4, -0.4],
    [1.9, 0],
  ];
  return merge(right, mirror(right), fingerTracks("right", open), fingerTracks("left", open));
}

function headTilt(): Tracks {
  return phrase(
    {
      head: [0, 0, -0.12],
      neck: [0, 0, -0.05],
      rightUpperArm: [-0.3, 0, 0],
      rightLowerArm: [0, 0.8, 0],
      rightHand: [0, -0.5, 0.15],
    },
    { prep: 0.25, stroke: 0.5, end: 1.6, out: 0.5 },
    {
      head: [0.02, 0, -0.1],
      neck: [0, 0, -0.04],
      rightUpperArm: [-0.28, 0, -0.02],
      rightLowerArm: [0, 0.75, 0],
      rightHand: [0, -0.5, 0.15],
    },
  );
}

function nodBeat(): Tracks {
  return {
    head: [
      [0, 0, 0, 0],
      [0.2, 0.13, 0, 0],
      [0.4, 0.02, 0, 0],
      [0.58, 0.1, 0, 0],
      [0.95, 0, 0, 0],
    ],
    neck: [
      [0, 0, 0, 0],
      [0.2, 0.06, 0, 0],
      [0.58, 0.05, 0, 0],
      [0.95, 0, 0, 0],
    ],
  };
}

function gestures(): Gesture[] {
  const g = (
    name: string,
    tracks: Tracks,
    hand: Gesture["hand"],
    energy: [number, number],
    long: boolean,
  ): Gesture => ({ name, motion: new KeyedMotion(tracks), hand, energy, long });
  return [
    g("beatR", beat(), "right", [0, 0.8], false),
    g("beatL", mirror(beat()), "left", [0, 0.8], false),
    g("openR", openPalm(), "right", [0.2, 1], true),
    g("openL", mirror(openPalm()), "left", [0.2, 1], true),
    g("twoOpen", twoHandsOpen(), "both", [0.45, 1], true),
    g("pointR", point(), "right", [0.5, 1], false),
    g("pointL", mirror(point()), "left", [0.5, 1], false),
    g("shrug", shrug(), "both", [0, 0.7], false),
    g("chest", handToChest(), "right", [0, 0.6], true),
    g("frame", frame(), "both", [0.45, 1], true),
    g("tilt", headTilt(), "right", [0, 0.55], true),
    g("nod", nodBeat(), "", [0, 0.6], false),
  ];
}

export const GESTURES: Gesture[] = gestures();

/**
 * Picks the next gesture: one that fits the voice energy, prefers long ones at the start of a
 * phrase and short ones inside it, never repeats the last two and alternates the hands.
 */
export function pickGesture(
  energy: number,
  phraseStart: boolean,
  recent: readonly string[],
  lastHand: Gesture["hand"],
  random: () => number = Math.random,
): Gesture {
  const fits = GESTURES.filter(
    (g) => !recent.includes(g.name) && energy >= g.energy[0] - 0.05 && energy <= g.energy[1] + 0.05,
  );
  const pool = fits.length ? fits : GESTURES.filter((g) => !recent.includes(g.name));
  const weights = pool.map((g) => {
    let w = g.long === phraseStart ? 2 : 1;
    if (lastHand && g.hand === lastHand && g.hand !== "both") w *= 0.4;
    return w;
  });
  let r = random() * weights.reduce((s, w) => s + w, 0);
  for (let i = 0; i < pool.length; i++) {
    r -= weights[i];
    if (r <= 0) return pool[i];
  }
  return pool[pool.length - 1];
}
