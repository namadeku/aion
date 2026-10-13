// Optional motion-capture layer for VRM avatars: user-supplied VRM Animation (.vrma) clips.
//
// Put .vrma files next to the .vrm model. A file named `idle*.vrma` loops as the base body
// motion while the character stands (several idle files take turns); any other file plays
// as a one-off action under its own name, e.g. `wave.vrma` replaces the procedural wave
// (names: wave, stretch, hairTouch, lookAround, rock, hum, yawn, hop, clap, startle, sigh,
// nod, poke, pat…). The avatar settings can map any clip to any slot instead (see
// actions.ts). The procedural layers (state poses, speech gestures, nods, gaze) still add on
// top. Aion ships no clips: use ones whose license allows it.

import * as THREE from "three";
import type { VRM, VRMHumanBoneName } from "@pixiv/three-vrm";
import {
  VRMAnimationLoaderPlugin,
  createVRMAnimationHumanoidTracks,
  type VRMAnimation,
} from "@pixiv/three-vrm-animation";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type { ClipPlan } from "./actions";
import { follow } from "./smooth";

/** Crossfade between clips and fade of the whole layer, seconds. */
const CROSSFADE = 0.45;
/** How long an idle clip keeps looping before another one takes over (if there are several). */
const IDLE_TURN = [20, 40];

export class ClipLayer {
  /** Clip rotation of every animated bone this frame (normalized bones, model convention). */
  readonly pose = new Map<VRMHumanBoneName, THREE.Quaternion>();
  /** Clip hips position this frame, if the clips move the hips. */
  readonly hips = new THREE.Vector3();
  hasHips = false;
  /** The clips move the torso: then the procedural idle life steps aside under them. */
  coversBody = false;
  /** How much the clip layer shows over the procedural body, 0..1 (smoothed). */
  weight = 0;
  private mixer: THREE.AnimationMixer;
  private idles: THREE.AnimationAction[] = [];
  private actions = new Map<string, THREE.AnimationAction>();
  private current: THREE.AnimationAction | null = null;
  private idleIndex = 0;
  private nextIdleTurn = IDLE_TURN[0];
  private actionLeft = 0;
  private bones: [VRMHumanBoneName, THREE.Object3D][] = [];

  private constructor(
    private vrm: VRM,
    idles: THREE.AnimationClip[],
    actions: Map<string, THREE.AnimationClip>,
  ) {
    this.mixer = new THREE.AnimationMixer(vrm.scene);
    const animated = new Set<string>();
    const clips = [...idles, ...actions.values()];
    for (const clip of clips) {
      for (const track of clip.tracks) animated.add(track.name.split(".")[0]);
    }
    for (const clip of idles) {
      // the same file may also be mapped to an action: a copy gets its own mixer action
      const action = this.mixer.clipAction(clip.clone());
      action.setLoop(THREE.LoopRepeat, Infinity);
      this.idles.push(action);
    }
    for (const [name, clip] of actions) {
      const action = this.mixer.clipAction(clip.clone());
      action.setLoop(THREE.LoopOnce, 1);
      action.clampWhenFinished = true;
      this.actions.set(name, action);
    }
    const humanoid = vrm.humanoid;
    for (const bone of Object.keys(humanoid.humanBones) as VRMHumanBoneName[]) {
      const node = humanoid.getNormalizedBoneNode(bone);
      if (node && animated.has(node.name)) {
        this.bones.push([bone, node]);
        this.pose.set(bone, new THREE.Quaternion());
      }
    }
    this.coversBody = this.pose.has("spine") || this.pose.has("hips");
    this.hasHips = clips.some((c) => c.tracks.some((t) => t.name.endsWith(".position")));
    if (this.idles.length) {
      this.current = this.idles[0];
      this.current.play();
    }
  }

  /**
   * Loads the clips of `plan` (file names relative to the model) through `url`; files that
   * fail are skipped with a console warning. Resolves to null when nothing usable was found.
   */
  static async load(
    vrm: VRM,
    plan: ClipPlan,
    url: (file: string) => string,
  ): Promise<ClipLayer | null> {
    const files = new Set([...plan.idle, ...plan.actions.values()]);
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
    const metaVersion = vrm.meta?.metaVersion === "0" ? "0" : "1";
    const clips = new Map<string, THREE.AnimationClip>();
    for (const file of files) {
      try {
        const gltf = await loader.loadAsync(url(file));
        const animation = (gltf.userData.vrmAnimations as VRMAnimation[] | undefined)?.[0];
        if (!animation) continue;
        // humanoid tracks only: the avatar drives the face and the eyes itself
        const { rotation, translation } = createVRMAnimationHumanoidTracks(
          animation,
          vrm.humanoid,
          metaVersion,
        );
        const tracks: THREE.KeyframeTrack[] = [...rotation.values()];
        const hips = translation.get("hips");
        if (hips) tracks.push(hips);
        clips.set(file, new THREE.AnimationClip(file, animation.duration, tracks));
      } catch (err) {
        console.warn(`VRMA ${file}:`, err);
      }
    }
    const idles = plan.idle.flatMap((f) => clips.get(f) ?? []);
    const actions = new Map<string, THREE.AnimationClip>();
    for (const [name, file] of plan.actions) {
      const clip = clips.get(file);
      if (clip) actions.set(name, clip);
    }
    return idles.length || actions.size ? new ClipLayer(vrm, idles, actions) : null;
  }

  has(name: string): boolean {
    return this.actions.has(name);
  }

  /** Starts the action clip `name`; returns its duration in seconds, or 0 if there is none. */
  play(name: string): number {
    const action = this.actions.get(name);
    if (!action) return 0;
    action.reset();
    action.setEffectiveWeight(1);
    if (this.current && this.current !== action)
      action.crossFadeFrom(this.current, CROSSFADE, false);
    action.play();
    this.current = action;
    this.actionLeft = action.getClip().duration;
    return this.actionLeft;
  }

  /**
   * Advance the clips and sample the pose. `standing`: the procedural body is in its normal
   * standing mode (idle clips make no sense while walking, sitting or being carried).
   */
  update(dt: number, standing: number): void {
    // back from an action clip: crossfade into the idle loop before it ends
    if (this.actionLeft > 0) {
      this.actionLeft -= dt;
      if (this.actionLeft <= CROSSFADE && this.current && !this.idles.includes(this.current)) {
        const idle = this.idles[this.idleIndex];
        if (idle) {
          idle.reset().play();
          idle.crossFadeFrom(this.current, CROSSFADE, false);
          this.current = idle;
        }
        // without an idle clip the layer fades out (below) while the action finishes
      }
    } else if (this.idles.length > 1) {
      this.nextIdleTurn -= dt;
      if (this.nextIdleTurn <= 0) {
        const from = this.idles[this.idleIndex];
        this.idleIndex = (this.idleIndex + 1) % this.idles.length;
        const to = this.idles[this.idleIndex];
        to.reset().play();
        to.crossFadeFrom(from, CROSSFADE * 2, false);
        this.current = to;
        this.nextIdleTurn = IDLE_TURN[0] + Math.random() * (IDLE_TURN[1] - IDLE_TURN[0]);
      }
    }
    const acting = this.actionLeft > CROSSFADE * 0.5;
    const goal = acting ? 1 : this.idles.length ? standing : 0;
    this.weight += (goal - this.weight) * follow((1 / CROSSFADE) * 2.5, dt);
    if (this.weight < 0.001 && !acting) return;
    this.mixer.update(dt);
    for (const [bone, node] of this.bones) this.pose.get(bone)?.copy(node.quaternion);
    if (this.hasHips) {
      const hips = this.vrm.humanoid.getNormalizedBoneNode("hips");
      if (hips) this.hips.copy(hips.position);
    }
  }

  dispose(): void {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.vrm.scene);
  }
}
