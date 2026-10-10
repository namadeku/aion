import { toast } from "../dom";
import type { AvatarConfig } from "../types";
import { HudAvatar } from "./hud";
import type { Avatar, AvatarOptions } from "./types";

/**
 * Creates the configured avatar. VRM/Live2D are loaded lazily (big libraries) and fall back
 * to the holographic HUD ring when the model is missing or fails to load.
 */
export async function createAvatar(
  canvas: HTMLCanvasElement,
  config: AvatarConfig,
  options: AvatarOptions = {},
): Promise<Avatar> {
  if (config.kind !== "hud" && config.path) {
    try {
      if (config.kind === "vrm") {
        const { VrmAvatar } = await import("./vrm");
        return await VrmAvatar.create(canvas, config.path, options);
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
  return new HudAvatar(canvas);
}
