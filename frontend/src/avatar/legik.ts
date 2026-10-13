// Foot grounding for the standing VRM avatar: two-bone leg IK on the normalized bones.
//
// The procedural layers move the pelvis (weight shift, hops, squats on landing) and rotate the
// legs in forward kinematics, so without help the soles slide and sink into the floor. After
// the pose is applied, each leg is re-solved so the ankle lands on its planted spot (the rest
// stance, in model space) and the sole stays flat; a heel can lift with the toe kept on the
// floor (the unloaded leg during a weight shift). When the pelvis rises past the leg's reach
// the leg simply straightens and the foot leaves the floor (hops).

import * as THREE from "three";
import type { VRM } from "@pixiv/three-vrm";

interface Leg {
  upper: THREE.Object3D;
  lower: THREE.Object3D;
  foot: THREE.Object3D;
  /** Rest ankle and the toe pivot (ball of the foot), model (vrm.scene) space. */
  ankle: THREE.Vector3;
  toe: THREE.Vector3;
  /** Rest foot rotation in model space (sole flat on the floor). */
  footRest: THREE.Quaternion;
  thigh: number;
  shin: number;
  /** Scratch: the forward-kinematics rotations, blended with the solution by weight. */
  fkUpper: THREE.Quaternion;
  fkLower: THREE.Quaternion;
  fkFoot: THREE.Quaternion;
}

const UP = new THREE.Vector3(0, 1, 0);

export class LegIK {
  private legs: Leg[] = [];
  /** The model's forward and right (heel-lift axis) in model space, from the rest feet. */
  private forward = new THREE.Vector3(0, 0, 1);
  private side = new THREE.Vector3(1, 0, 0);
  // scratch objects (no allocation per frame)
  private h = new THREE.Vector3();
  private k = new THREE.Vector3();
  private a = new THREE.Vector3();
  private t = new THREE.Vector3();
  private u = new THREE.Vector3();
  private v = new THREE.Vector3();
  private p = new THREE.Vector3();
  private from = new THREE.Vector3();
  private to = new THREE.Vector3();
  private fwdWorld = new THREE.Vector3();
  private q = new THREE.Quaternion();
  private pq = new THREE.Quaternion();
  private pqi = new THREE.Quaternion();
  private heel = new THREE.Quaternion();
  private sceneQ = new THREE.Quaternion();

  private constructor(private vrm: VRM) {}

  /** Builds the solver from the model's rest pose; null without leg bones. */
  static create(vrm: VRM): LegIK | null {
    const ik = new LegIK(vrm);
    const humanoid = vrm.humanoid;
    const scene = vrm.scene;
    scene.updateMatrixWorld(true);
    const sceneInv = scene.getWorldQuaternion(new THREE.Quaternion()).invert();
    const fwd = new THREE.Vector3();
    for (const side of ["left", "right"] as const) {
      const upper = humanoid.getNormalizedBoneNode(`${side}UpperLeg`);
      const lower = humanoid.getNormalizedBoneNode(`${side}LowerLeg`);
      const foot = humanoid.getNormalizedBoneNode(`${side}Foot`);
      if (!upper || !lower || !foot) return null;
      const toes = humanoid.getNormalizedBoneNode(`${side}Toes`);
      const hip = scene.worldToLocal(upper.getWorldPosition(new THREE.Vector3()));
      const knee = scene.worldToLocal(lower.getWorldPosition(new THREE.Vector3()));
      const ankle = scene.worldToLocal(foot.getWorldPosition(new THREE.Vector3()));
      const toe = toes
        ? scene.worldToLocal(toes.getWorldPosition(new THREE.Vector3()))
        : ankle.clone();
      const thigh = hip.distanceTo(knee);
      const shin = knee.distanceTo(ankle);
      if (thigh < 1e-3 || shin < 1e-3) return null;
      if (toes) fwd.add(new THREE.Vector3(toe.x - ankle.x, 0, toe.z - ankle.z));
      ik.legs.push({
        upper,
        lower,
        foot,
        ankle,
        toe,
        footRest: sceneInv.clone().multiply(foot.getWorldQuaternion(new THREE.Quaternion())),
        thigh,
        shin,
        fkUpper: new THREE.Quaternion(),
        fkLower: new THREE.Quaternion(),
        fkFoot: new THREE.Quaternion(),
      });
    }
    // the model faces +Z (VRM 1.0) or -Z (VRM 0.x before the scene turn) in model space:
    // read it from the toes when there are toe bones, else from the metadata convention
    if (fwd.lengthSq() > 1e-6) ik.forward.copy(fwd.normalize());
    else ik.forward.set(0, 0, vrm.meta?.metaVersion === "0" ? -1 : 1);
    ik.side.crossVectors(UP, ik.forward).normalize();
    for (const leg of ik.legs) {
      if (leg.toe.distanceToSquared(leg.ankle) < 1e-6) {
        // no toe bone: the ball of the foot is ahead of the ankle, near the floor
        leg.toe
          .copy(leg.ankle)
          .addScaledVector(ik.forward, (leg.thigh + leg.shin) * 0.15)
          .setY(leg.ankle.y * 0.3);
      }
    }
    return ik;
  }

  /**
   * Re-solves both legs after the pose is applied. `weight` 0..1 blends the solution in (0:
   * plain forward kinematics); `heelLift` raises each heel (radians, left then right) about the
   * toe, as when the weight leaves that leg.
   */
  solve(weight: number, heelLeft: number, heelRight: number): void {
    if (weight <= 0.001) return;
    const scene = this.vrm.scene;
    const hips = this.vrm.humanoid.getNormalizedBoneNode("hips");
    if (!hips) return;
    hips.updateWorldMatrix(true, true);
    scene.getWorldQuaternion(this.sceneQ);
    this.fwdWorld.copy(this.forward).applyQuaternion(this.sceneQ);
    this.legs.forEach((leg, i) => this.solveLeg(leg, i === 0 ? heelLeft : heelRight, weight));
  }

  private solveLeg(leg: Leg, heelLift: number, weight: number): void {
    const { h, k, a, t, u, v, p } = this;
    leg.fkUpper.copy(leg.upper.quaternion);
    leg.fkLower.copy(leg.lower.quaternion);
    leg.fkFoot.copy(leg.foot.quaternion);

    // planted ankle target: the rest ankle, pivoted about the toe when the heel lifts
    this.heel.setFromAxisAngle(this.side, heelLift);
    t.copy(leg.ankle).sub(leg.toe).applyQuaternion(this.heel).add(leg.toe);
    this.vrm.scene.localToWorld(t);

    leg.upper.getWorldPosition(h);
    leg.lower.getWorldPosition(k);
    const { thigh, shin } = leg;
    u.subVectors(t, h);
    const d = Math.min(thigh + shin - 1e-4, Math.max(Math.abs(thigh - shin) + 1e-4, u.length()));
    u.normalize();
    // bend plane: toward where the knee already points, biased forward (knees bend forward)
    p.subVectors(k, h);
    p.addScaledVector(u, -p.dot(u)).addScaledVector(this.fwdWorld, 0.02);
    v.copy(p).addScaledVector(u, -p.dot(u)).normalize();
    const cos = (thigh * thigh + d * d - shin * shin) / (2 * thigh * d);
    const alpha = Math.acos(Math.min(1, Math.max(-1, cos)));
    // the knee where the two bones meet
    p.copy(h)
      .addScaledVector(u, thigh * Math.cos(alpha))
      .addScaledVector(v, thigh * Math.sin(alpha));
    this.rotateToward(leg.upper, this.from.subVectors(k, h), this.to.subVectors(p, h));
    leg.upper.updateWorldMatrix(false, true);
    leg.lower.getWorldPosition(k);
    leg.foot.getWorldPosition(a);
    this.rotateToward(leg.lower, this.from.subVectors(a, k), this.to.subVectors(t, k));
    leg.lower.updateWorldMatrix(false, true);

    // sole: flat on the floor (rest rotation in model space), tilted by the heel lift
    leg.lower.getWorldQuaternion(this.pq);
    this.q
      .copy(this.sceneQ)
      .multiply(this.heel)
      .multiply(leg.footRest)
      .premultiply(this.pq.invert());
    leg.foot.quaternion.copy(this.q);

    if (weight < 0.999) {
      leg.upper.quaternion.copy(leg.fkUpper.slerp(leg.upper.quaternion, weight));
      leg.lower.quaternion.copy(leg.fkLower.slerp(leg.lower.quaternion, weight));
      leg.foot.quaternion.copy(leg.fkFoot.slerp(leg.foot.quaternion, weight));
    }
    leg.upper.updateWorldMatrix(false, true);
  }

  /** Turns `node` (in world space) so its bone direction `from` points along `to`. */
  private rotateToward(node: THREE.Object3D, from: THREE.Vector3, to: THREE.Vector3): void {
    if (from.lengthSq() < 1e-10 || to.lengthSq() < 1e-10) return;
    const delta = this.q.setFromUnitVectors(from.normalize(), to.normalize());
    const parent = node.parent;
    if (parent) {
      parent.getWorldQuaternion(this.pq);
      // local' = parent⁻¹ · delta · parent · local
      delta.premultiply(this.pqi.copy(this.pq).invert()).multiply(this.pq);
    }
    node.quaternion.premultiply(delta);
  }
}
