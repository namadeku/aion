// Frame-rate independent smoothing helpers shared by the avatars.
//
// `x += (target - x) * min(1, dt * rate)` behaves differently at 30, 60 and 144 fps and
// overshoots on long frames; the exponential form below gives the same curve at any rate.

/** Fraction of the remaining distance covered in `dt` seconds at `rate` (1/s). */
export function follow(rate: number, dt: number): number {
  return 1 - Math.exp(-rate * dt);
}

/** Exponential approach of `current` to `target` (frame-rate independent). */
export function approach(current: number, target: number, rate: number, dt: number): number {
  return current + (target - current) * follow(rate, dt);
}

/** Envelope follower: fast `attack` when the signal rises, slower `release` when it falls. */
export function envelope(
  current: number,
  target: number,
  attack: number,
  release: number,
  dt: number,
): number {
  return approach(current, target, target > current ? attack : release, dt);
}

/**
 * Output voice level (speaker RMS × 4, clipped to 0..1) → openness 0..1. Levels under the
 * floor (hiss, breath, consonant tails) close the mouth; the curve compresses the rest, so
 * quiet syllables still move the jaw and loud ones don't pin it wide open.
 */
export function voiceOpenness(level: number): number {
  const x = Math.min(1, Math.max(0, (level - 0.04) / 0.5));
  return Math.pow(x, 0.7);
}

/** Smoothstep 0..1. */
export function smoothstep(x: number): number {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
}

/** Clamp the frame time: long gaps (hidden tab, GC, a stalled renderer) must not jolt anything. */
export function clampDt(dt: number, max = 1 / 20): number {
  return dt > 0 ? Math.min(dt, max) : 0;
}

// -- 1D gradient noise --------------------------------------------------------------------
// Smooth, non-repeating wander for idle life: unlike a sum of sines it has no visible period.

const PERM = new Uint8Array(512);
const GRAD = new Float32Array(256);
{
  // fixed seed: the same model moves the same way on every start (deterministic tests)
  let seed = 0x9e3779b9;
  const rand = (): number => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const p = Array.from({ length: 256 }, (_, i) => i);
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [p[i], p[j]] = [p[j], p[i]];
  }
  for (let i = 0; i < 512; i++) PERM[i] = p[i & 255];
  for (let i = 0; i < 256; i++) GRAD[i] = rand() * 2 - 1;
}

/** Perlin-style gradient noise in about -0.5..0.5, smooth (C2) in `x`. */
export function noise1(x: number): number {
  const i = Math.floor(x);
  const f = x - i;
  const a = GRAD[PERM[i & 255]] * f;
  const b = GRAD[PERM[(i + 1) & 255]] * (f - 1);
  const u = f * f * f * (f * (f * 6 - 15) + 10);
  return a + (b - a) * u;
}

/**
 * Two octaves of noise normalised to about -1..1. `seed` picks an independent channel, so
 * several bones can wander without moving in sync.
 */
export function wander(t: number, seed: number): number {
  const s = seed * 37.17;
  return (noise1(t + s) + noise1(t * 2.3 + s + 11.3) * 0.4) * 1.4;
}

/**
 * Critically damped spring on a scalar (no overshoot, smooth start and stop). Good for
 * values that arrive in steps, like the 15 Hz cursor position from the backend.
 */
export class Spring {
  value: number;
  velocity = 0;

  constructor(
    value = 0,
    /** Natural frequency, rad/s: about 4/omega seconds to settle. */
    public omega = 8,
  ) {
    this.value = value;
  }

  update(target: number, dt: number): number {
    // exact solution of the critically damped oscillator over dt: stable for any step
    const w = this.omega;
    const x = this.value - target;
    const e = Math.exp(-w * dt);
    const t = (this.velocity + w * x) * dt;
    this.value = target + (x + t) * e;
    this.velocity = (this.velocity - w * t) * e;
    return this.value;
  }

  /** Give the spring a push (e.g. a nod on a stressed syllable). */
  kick(velocity: number): void {
    this.velocity += velocity;
  }
}
