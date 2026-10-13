import { toast } from "../dom";
import type { AvatarConfig } from "../types";
import { planClips } from "./actions";
import { HudAvatar } from "./hud";
import type { Avatar, AvatarOptions } from "./types";

/** Identity of an avatar setup: a new key means the avatar must be created again. */
export function avatarKeyOf(config: AvatarConfig): string {
  const clips = (config.clips ?? []).join("|");
  return `${config.kind}:${config.path ?? ""}:${JSON.stringify(config.animations ?? {})}:${clips}`;
}

/**
 * Creates the configured avatar. The orb needs WebGL (three.js, loaded lazily). VRM/Live2D are loaded lazily (big libraries) and fall back
 * to the holographic HUD ring when the model is missing or fails to load.
 */
export async function createAvatar(
  canvas: HTMLCanvasElement,
  config: AvatarConfig,
  options: AvatarOptions = {},
): Promise<Avatar> {
  if (config.kind === "orb") {
    try {
      const { OrbAvatar } = await import("./orb");
      return new OrbAvatar(canvas, options);
    } catch (err) {
      console.error(err); // no WebGL: fall back to the 2D ring below
    }
  }
  if ((config.kind === "vrm" || config.kind === "live2d") && config.path) {
    try {
      if (config.kind === "vrm") {
        const { VrmAvatar } = await import("./vrm");
        const clips = planClips(config.clips ?? [], config.animations);
        return await VrmAvatar.create(canvas, config.path, options, clips);
      }
      if (config.kind === "live2d") {
        const { Live2DAvatar } = await import("./live2d");
        return await Live2DAvatar.create(canvas, config.path, options);
      }
    } catch (err) {
      console.error(err);
      toast(
        "Аватар не загрузился",
        `${err instanceof Error ? err.message : err} — показываю HUD`,
        "warning",
      );
    }
  }
  return new HudAvatar(canvas, options);
}
