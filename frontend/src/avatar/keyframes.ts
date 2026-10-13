// Keyframed motion for the procedural avatar: eased bone tracks with overlapping timing.
//
// A motion is a set of per-bone tracks, each a list of keys [time, x, y, z] (rotation offsets
// on top of the pose below, VRM 1.0 convention like motion.ts). Between keys the value follows
// a monotone cubic Hermite curve: no overshoot past a key, zero speed at turning points and at
// both ends, and continuous speed through intermediate keys, so a chain of keys reads as one
// eased gesture instead of a string of separate moves. A chain of alternating extremes with
// equal spacing (a wave) even has continuous acceleration: a smooth, low-jerk oscillation.
//
// Overlap: each bone may sample its track a little later than the motion clock (`lag`), so a
// move starts at the shoulder and ripples out to the elbow, wrist and fingers.

import type { VRMHumanBoneName } from "@pixiv/three-vrm";

export type Key = readonly [t: number, x: number, y: number, z: number];
export type Tracks = Partial<Record<VRMHumanBoneName, readonly Key[]>>;

/** Scalar keys [time, value] (hips lift, face hints). */
export type ScalarKey = readonly [t: number, v: number];

/** Monotone (Fritsch–Carlson) tangent at key i of a 1D sequence. */
function tangent(ts: readonly number[], vs: readonly number[], i: number): number {
  const n = ts.length;
  if (i === 0 || i === n - 1) return 0; // ease in and out of the motion
  const d0 = (vs[i] - vs[i - 1]) / Math.max(1e-6, ts[i] - ts[i - 1]);
  const d1 = (vs[i + 1] - vs[i]) / Math.max(1e-6, ts[i + 1] - ts[i]);
  if (d0 * d1 <= 0) return 0; // a turning point (or a hold): stop there
  // harmonic mean of the neighbouring slopes: never overshoots, smooth through the key
  return (2 * d0 * d1) / (d0 + d1);
}

/** Precomputed curve of one channel. */
class Channel {
  private ms: number[];

  constructor(
    private ts: readonly number[],
    private vs: readonly number[],
  ) {
    this.ms = ts.map((_, i) => tangent(ts, vs, i));
  }

  sample(t: number): number {
    const { ts, vs, ms } = this;
    const n = ts.length;
    if (t <= ts[0]) return vs[0];
    if (t >= ts[n - 1]) return vs[n - 1];
    let i = 0;
    while (i < n - 2 && t >= ts[i + 1]) i++;
    const h = ts[i + 1] - ts[i];
    const s = (t - ts[i]) / h;
    const s2 = s * s;
    const s3 = s2 * s;
    return (
      (2 * s3 - 3 * s2 + 1) * vs[i] +
      (s3 - 2 * s2 + s) * h * ms[i] +
      (-2 * s3 + 3 * s2) * vs[i + 1] +
      (s3 - s2) * h * ms[i + 1]
    );
  }
}

/** A scalar curve through [time, value] keys. */
export class Curve {
  private channel: Channel;
  readonly end: number;

  constructor(keys: readonly ScalarKey[]) {
    this.channel = new Channel(
      keys.map((k) => k[0]),
      keys.map((k) => k[1]),
    );
    this.end = keys[keys.length - 1][0];
  }

  at(t: number): number {
    return this.channel.sample(t);
  }
}

interface BoneTrack {
  bone: VRMHumanBoneName;
  lag: number;
  x: Channel;
  y: Channel;
  z: Channel;
  out: [number, number, number];
}

/**
 * Overlapping timing along the limbs: how much later (s) a bone follows the motion clock.
 * The upper arm leads, the forearm, hand and fingers trail; the head lags the torso a bit.
 */
export function defaultLag(bone: VRMHumanBoneName): number {
  if (/LowerArm$/.test(bone)) return 0.05;
  if (/Hand$/.test(bone)) return 0.09;
  if (/(Thumb|Index|Middle|Ring|Little)/.test(bone)) return 0.12;
  if (bone === "neck") return 0.03;
  if (bone === "head") return 0.06;
  if (/LowerLeg$/.test(bone)) return 0.03;
  if (/Foot$/.test(bone)) return 0.05;
  return 0;
}

/** A compiled keyframed motion: sample it at a time to get a pose (reused arrays, no garbage). */
export class KeyedMotion {
  private tracks: BoneTrack[] = [];
  /** Last key time plus the longest lag: the motion is back at its final keys after this. */
  readonly duration: number;

  constructor(tracks: Tracks, lag: (bone: VRMHumanBoneName) => number = defaultLag) {
    let end = 0;
    for (const bone in tracks) {
      const keys = tracks[bone as VRMHumanBoneName];
      if (!keys?.length) continue;
      const ts = keys.map((k) => k[0]);
      const l = lag(bone as VRMHumanBoneName);
      end = Math.max(end, ts[ts.length - 1] + l);
      this.tracks.push({
        bone: bone as VRMHumanBoneName,
        lag: l,
        x: new Channel(
          ts,
          keys.map((k) => k[1]),
        ),
        y: new Channel(
          ts,
          keys.map((k) => k[2]),
        ),
        z: new Channel(
          ts,
          keys.map((k) => k[3]),
        ),
        out: [0, 0, 0],
      });
    }
    this.duration = end;
  }

  /** Calls `add(bone, rotation)` for every track at time `t` (`scale` multiplies the angles). */
  sample(
    t: number,
    add: (bone: VRMHumanBoneName, x: number, y: number, z: number) => void,
    scale = 1,
  ): void {
    for (const tr of this.tracks) {
      const u = t - tr.lag;
      add(tr.bone, tr.x.sample(u) * scale, tr.y.sample(u) * scale, tr.z.sample(u) * scale);
    }
  }
}

/** Mirror a track set left ↔ right (VRM 1.0: Y and Z rotations change sign). */
export function mirror(tracks: Tracks): Tracks {
  const out: Tracks = {};
  for (const bone in tracks) {
    const keys = tracks[bone as VRMHumanBoneName];
    if (!keys) continue;
    const other = bone.startsWith("left")
      ? bone.replace(/^left/, "right")
      : bone.startsWith("right")
        ? bone.replace(/^right/, "left")
        : bone;
    out[other as VRMHumanBoneName] = keys.map(([t, x, y, z]) => [t, x, -y, -z] as const);
  }
  return out;
}

/**
 * Keys of a pose held between `start` and `end`: rises from zero over `inT`, holds (with an
 * optional slow drift toward `drift` × pose), falls back to zero over `outT`.
 */
export function holdKeys(
  pose: readonly [number, number, number],
  start: number,
  inT: number,
  end: number,
  outT: number,
  drift = 1,
): Key[] {
  const [x, y, z] = pose;
  return [
    [start, 0, 0, 0],
    [start + inT, x, y, z],
    [end - outT, x * drift, y * drift, z * drift],
    [end, 0, 0, 0],
  ];
}
