// «Орб»: a wireframe + particle sphere (three.js) whose surface ripples with the voice.
// Idle: slow breathing. Listening: the mesh follows the microphone. Thinking: a bright band
// sweeps pole to pole and the sphere spins faster. Speaking: the mesh follows the voice.
// Colours come from the theme tokens (--orb-*) and follow theme switches.
//
// As the desktop mascot (`fullBody`) the orb floats a little above the surface it stands on
// (taskbar or the top edge of a window), reports its resting line through `layout()` and moves
// with the voice (see voice.ts): bobbing and swelling with the amplitude, leaning in while
// listening, wandering while thinking; squashing when carried and stretching when it falls.

import * as THREE from "three";
import type { State } from "../types";
import type { Avatar, AvatarInput, AvatarOptions, DesktopInput, Layout, Reaction } from "./types";
import { follow } from "./smooth";
import { VoiceMotion } from "./voice";

interface Look {
  /** Base surface displacement. */
  amp: number;
  /** Extra displacement per unit of voice level. */
  gain: number;
  /** Spatial frequency of the ripples. */
  freq: number;
  /** Speed of the ripple animation. */
  speed: number;
  /** Rotation speed, rad/s. */
  spin: number;
  /** Strength of the sweeping band (thinking). */
  band: number;
  /** Overall opacity. */
  glow: number;
}

const LOOKS: Record<State, Look> = {
  idle: { amp: 0.035, gain: 0, freq: 2.2, speed: 0.35, spin: 0.07, band: 0, glow: 0.6 },
  listening: { amp: 0.04, gain: 0.32, freq: 3.0, speed: 0.8, spin: 0.1, band: 0, glow: 0.85 },
  thinking: { amp: 0.05, gain: 0, freq: 2.6, speed: 1.1, spin: 0.55, band: 1, glow: 0.8 },
  speaking: { amp: 0.035, gain: 0.42, freq: 3.4, speed: 1.4, spin: 0.14, band: 0, glow: 0.95 },
};

const LATITUDES = 44;
const LONGITUDES = 64;
const RING_SEGMENTS = 128;
const PARTICLES = 2600;
/** Largest radius the surface reaches (for framing). */
const FIT_RADIUS = 1.42;
/** Camera distance of the desktop framing (the sphere is scaled instead of the camera moved). */
const DESKTOP_DISTANCE = 5.05;
/** Orb radius in the desktop frame, as a fraction of the frame height (and of its width). */
const DESKTOP_RADIUS = 0.17;
const DESKTOP_RADIUS_OF_WIDTH = 0.3;

const VERTEX = /* glsl */ `
  uniform float uPhase;
  uniform float uAmp;
  uniform float uLevel;
  uniform float uFreq;
  uniform float uBand;
  uniform float uBandPos;
  uniform float uPulse;
  uniform float uSize;
  uniform float uPixelRatio;
  varying float vFront;
  varying float vBand;
  varying float vPole;

  float ripple(vec3 p, float f, float t) {
    return sin(p.x * f + t * 1.3) * sin(p.y * f * 1.17 - t * 0.9) * sin(p.z * f * 0.83 + t * 1.1)
      + 0.5 * sin(p.x * f * 2.1 - t * 1.7 + p.z * 1.3) * sin(p.y * f * 1.9 + t * 1.4);
  }

  void main() {
    vec3 n = normalize(position);
    float base = ripple(n, uFreq, uPhase);
    float voice = ripple(n * 1.7, uFreq * 1.6, uPhase * 1.8);
    float band = exp(-pow((n.y - uBandPos) * 5.0, 2.0)) * uBand;
    float r = 1.0 + base * uAmp + uLevel * (0.55 + 0.45 * voice) + band * 0.05 + uPulse * 0.08;
    vec4 mv = modelViewMatrix * vec4(n * r, 1.0);
    vFront = normalize(normalMatrix * n).z * 0.5 + 0.5;
    vBand = band;
    vPole = abs(n.y);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * uPixelRatio * (3.2 / -mv.z);
  }
`;

const FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform vec3 uHighlight;
  uniform float uAlpha;
  uniform float uRound;
  varying float vFront;
  varying float vBand;
  varying float vPole;

  void main() {
    if (uRound > 0.5) {
      vec2 c = gl_PointCoord - 0.5;
      if (dot(c, c) > 0.25) discard;
    }
    float depth = mix(0.18, 1.0, vFront * vFront);
    // meridians converge at the poles: fade them so the poles do not glow
    depth *= 1.0 - 0.75 * pow(vPole, 10.0);
    vec3 color = mix(uColor, uHighlight, vBand * 0.6);
    gl_FragColor = vec4(color, uAlpha * depth * (1.0 + vBand * 1.6));
  }
`;

function wireGeometry(): THREE.BufferGeometry {
  const pts: number[] = [];
  const push = (theta: number, phi: number): void => {
    pts.push(Math.sin(theta) * Math.cos(phi), Math.cos(theta), Math.sin(theta) * Math.sin(phi));
  };
  // latitude rings
  for (let i = 1; i < LATITUDES; i++) {
    const theta = (i / LATITUDES) * Math.PI;
    for (let j = 0; j < RING_SEGMENTS; j++) {
      push(theta, (j / RING_SEGMENTS) * Math.PI * 2);
      push(theta, ((j + 1) / RING_SEGMENTS) * Math.PI * 2);
    }
  }
  // meridians
  const steps = RING_SEGMENTS / 2;
  for (let i = 0; i < LONGITUDES; i++) {
    const phi = (i / LONGITUDES) * Math.PI * 2;
    for (let j = 0; j < steps; j++) {
      push((j / steps) * Math.PI, phi);
      push(((j + 1) / steps) * Math.PI, phi);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  return geometry;
}

function particleGeometry(): THREE.BufferGeometry {
  // Fibonacci sphere: an even spread without visible rows
  const pts = new Float32Array(PARTICLES * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < PARTICLES; i++) {
    const y = 1 - (i / (PARTICLES - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const a = golden * i;
    pts.set([Math.cos(a) * r * 1.03, y * 1.03, Math.sin(a) * r * 1.03], i * 3);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(pts, 3));
  return geometry;
}

function cssColor(name: string): THREE.Color {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(value || "#38bdf0");
}

export class OrbAvatar implements Avatar {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(32, 1, 0.1, 50);
  private group = new THREE.Group();
  private wire: THREE.LineSegments;
  private dots: THREE.Points;
  private uniforms = {
    uPhase: { value: 0 },
    uAmp: { value: LOOKS.idle.amp },
    uLevel: { value: 0 },
    uFreq: { value: LOOKS.idle.freq },
    uBand: { value: 0 },
    uBandPos: { value: 0 },
    uPulse: { value: 0 },
    uPixelRatio: { value: 1 },
    // the thinking band brightens toward white on dark, toward ink on light
    uHighlight: { value: new THREE.Color(1, 1, 1) },
  };
  private look: Look = { ...LOOKS.idle };
  private color = new THREE.Color();
  private palette: Record<State, THREE.Color>;
  private light = false;
  private level = 0;
  private resize: ResizeObserver;
  private themeWatch: MutationObserver;
  private reduced: MediaQueryList;
  private sweep = 0;
  private fullBody: boolean;
  private voice = new VoiceMotion();
  private desktop: DesktopInput | null = null;
  private squash = { x: 1, y: 1 };
  private lean = 0;
  /** Desktop framing in fractions of the frame height: radius, centre row, world units per height. */
  private frame = { radius: DESKTOP_RADIUS, centre: 0.7, world: 1 };
  private dotSize = 2.2;

  constructor(
    private canvas: HTMLCanvasElement,
    options: AvatarOptions = {},
  ) {
    this.fullBody = options.fullBody ?? false;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setClearColor(0x000000, 0);
    this.palette = this.readPalette();
    this.color.copy(this.palette.idle);

    const material = (size: number, round: boolean): THREE.ShaderMaterial =>
      new THREE.ShaderMaterial({
        vertexShader: VERTEX,
        fragmentShader: FRAGMENT,
        uniforms: {
          ...this.uniforms,
          uColor: { value: this.color },
          uAlpha: { value: 0.5 },
          uSize: { value: size },
          uRound: { value: round ? 1 : 0 },
        },
        transparent: true,
        depthWrite: false,
      });
    this.wire = new THREE.LineSegments(wireGeometry(), material(0, false));
    this.dots = new THREE.Points(particleGeometry(), material(2.2, true));
    this.group.add(this.wire, this.dots);
    this.group.rotation.set(0.35, 0, -0.18);
    this.scene.add(this.group);
    this.applyTheme();

    this.reduced = matchMedia("(prefers-reduced-motion: reduce)");
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(canvas);
    this.themeWatch = new MutationObserver(() => {
      this.palette = this.readPalette();
      this.applyTheme();
    });
    this.themeWatch.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    this.fit();
  }

  private readPalette(): Record<State, THREE.Color> {
    return {
      idle: cssColor("--orb-idle"),
      listening: cssColor("--orb-listening"),
      thinking: cssColor("--orb-thinking"),
      speaking: cssColor("--orb-speaking"),
    };
  }

  /** Light lines add up to white on a dark window; on paper they are drawn normally. */
  private applyTheme(): void {
    this.light = document.documentElement.dataset.theme === "light";
    const blending = this.light ? THREE.NormalBlending : THREE.AdditiveBlending;
    this.uniforms.uHighlight.value.set(this.light ? 0x0d1b3a : 0xffffff);
    for (const obj of [this.wire, this.dots]) {
      const m = obj.material as THREE.ShaderMaterial;
      m.blending = blending;
      m.needsUpdate = true;
    }
  }

  private fit(): void {
    const { width, height } = this.canvas.getBoundingClientRect();
    if (!width || !height) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(width, height, false);
    this.uniforms.uPixelRatio.value = ratio;
    const aspect = width / height;
    this.camera.aspect = aspect;
    if (this.fullBody) {
      this.fitDesktop(aspect);
      return;
    }
    // keep the sphere inside the narrower side with a margin
    const vertical = THREE.MathUtils.degToRad(this.camera.fov);
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * aspect);
    const half = Math.min(vertical, horizontal) / 2;
    this.camera.position.set(0, 0, (FIT_RADIUS / Math.sin(half)) * 0.98);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateProjectionMatrix();
  }

  /** Desktop framing: a smaller orb in the lower part of a tall frame, room above for a bubble. */
  private fitDesktop(aspect: number): void {
    const vertical = THREE.MathUtils.degToRad(this.camera.fov);
    const world = 2 * DESKTOP_DISTANCE * Math.tan(vertical / 2);
    const radius = Math.min(DESKTOP_RADIUS, DESKTOP_RADIUS_OF_WIDTH * aspect);
    // keep the largest swell (FIT_RADIUS) inside the frame
    const centre = 1 - radius * FIT_RADIUS - 0.012;
    this.frame = { radius, centre, world };
    // the sphere is about half the size of the one in the window: finer dots
    this.dotSize = 2.2 * Math.min(1, Math.max(0.6, radius / 0.345));
    this.camera.position.set(0, 0, DESKTOP_DISTANCE);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateProjectionMatrix();
  }

  /** Voice-driven motion of the whole orb (desktop mascot). */
  private moveBody(input: AvatarInput, reduced: boolean): number {
    const { dt } = input;
    const pose = this.voice.update(input, reduced);
    const { radius, centre, world } = this.frame;
    const d = this.desktop;
    const mode = d?.mode ?? "stand";
    let sx = 1;
    let sy = 1;
    if (!reduced) {
      if (mode === "drag") {
        sx = 1.06;
        sy = 0.94;
      } else if (mode === "fall") {
        sy = 1 + Math.min(0.16, Math.abs(d?.vy ?? 0) * 0.14);
        sx = 1 / Math.sqrt(sy);
      }
    }
    this.squash.x += (sx - this.squash.x) * follow(12, dt);
    this.squash.y += (sy - this.squash.y) * follow(12, dt);
    // rolling along while walking: lean into the direction of travel
    const travel = reduced ? 0 : Math.max(-1, Math.min(1, (d?.vx ?? 0) * 3));
    this.lean += (travel - this.lean) * follow(6, dt);
    const look = reduced ? null : (d?.look ?? null);
    const scale = radius * world * pose.scale;
    const g = this.group;
    g.scale.set(scale * this.squash.x, scale * this.squash.y, scale);
    g.position.set(
      (pose.sway + (look ? look[0] * 0.008 : 0)) * world,
      (0.5 - centre + pose.lift + (look ? look[1] * 0.006 : 0)) * world,
      0,
    );
    g.rotation.z = -0.18 + pose.roll + this.lean * 0.25;
    g.rotation.y += this.lean * dt * 2.2;
    this.uniforms.uPulse.value = Math.max(this.uniforms.uPulse.value, pose.beat * 0.5);
    return pose.glow;
  }

  update(input: AvatarInput): void {
    if (document.hidden) return; // nobody sees it: spare the GPU
    const { state, outputLevel, inputLevel, dt } = input;
    const reduced = this.reduced.matches;
    const bodyGlow = this.fullBody ? this.moveBody(input, reduced) : 0;
    const target = LOOKS[state];
    const k = follow(3, dt);
    const look = this.look;
    for (const key of Object.keys(look) as (keyof Look)[]) {
      look[key] += (target[key] - look[key]) * k;
    }
    this.color.lerp(this.palette[state], follow(4, dt));

    // voice level: fast attack, slower release
    const raw = state === "speaking" ? outputLevel : state === "listening" ? inputLevel : 0;
    const rate = raw > this.level ? 18 : 5;
    this.level += (raw - this.level) * follow(rate, dt);

    const motion = reduced ? 0 : 1;
    const u = this.uniforms;
    u.uPhase.value += dt * look.speed * motion;
    u.uAmp.value = look.amp * (reduced ? 0.4 : 1);
    u.uFreq.value = look.freq;
    u.uLevel.value = this.level * look.gain;
    u.uBand.value = look.band;
    this.sweep += dt * 1.6 * motion;
    u.uBandPos.value = reduced ? 0 : Math.sin(this.sweep) * 0.9;
    u.uPulse.value *= Math.exp(-dt * 4);
    this.group.rotation.y += dt * look.spin * motion;

    const alpha = look.glow * (this.light ? 0.75 : 0.42) * (1 + bodyGlow * 0.35);
    (this.wire.material as THREE.ShaderMaterial).uniforms.uAlpha.value = alpha;
    const dots = (this.dots.material as THREE.ShaderMaterial).uniforms;
    dots.uAlpha.value = Math.min(1, alpha * 1.6);
    dots.uSize.value = this.dotSize;
    this.renderer.render(this.scene, this.camera);
  }

  react(event: Reaction): void {
    if (event === "wake" || event === "wave") this.uniforms.uPulse.value = 1;
  }

  setDesktop(input: DesktopInput | null): void {
    this.desktop = input;
  }

  /**
   * Where the orb rests in the frame, in drawing-buffer pixels. It floats a little above its
   * "feet" line (so it hovers over the taskbar) and its underside sits on window tops.
   */
  layout(): Layout | null {
    if (!this.fullBody) return null;
    const { width, height } = this.renderer.domElement;
    const { radius, centre } = this.frame;
    return {
      feet: Math.min(0.985, centre + radius * 1.1 + 0.02) * height,
      seat: (centre + radius) * height,
      center: width / 2,
      head: (centre - radius * 1.05) * height,
    };
  }

  dispose(): void {
    this.resize.disconnect();
    this.themeWatch.disconnect();
    for (const obj of [this.wire, this.dots]) {
      obj.geometry.dispose();
      (obj.material as THREE.Material).dispose();
    }
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }
}
