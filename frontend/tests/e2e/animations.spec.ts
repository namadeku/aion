import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { TOKEN } from "../../playwright.config";

const headers = { "X-Aion-Token": TOKEN };

test("avatar settings: «Анимации» lists clips and saves a per-action choice", async ({
  page,
}, info) => {
  // a model folder with two clips (the files are never loaded: no preview is opened)
  const folder = mkdtempSync(join(tmpdir(), "aion-e2e-model-"));
  writeFileSync(join(folder, "me.vrm"), "glTF");
  writeFileSync(join(folder, "idle_calm.vrma"), "glTF");
  writeFileSync(join(folder, "greeting.vrma"), "glTF");
  const avatar = (value: Record<string, unknown>) =>
    page.request.patch("/api/config", {
      headers,
      data: { profiles: { aion: { avatar: value } } },
    });
  await avatar({ kind: "vrm", path: join(folder, "me.vrm"), animations: { wave: "" } });
  try {
    await page.goto(`/?token=${TOKEN}#/settings/voice`);
    const panel = page.locator(".panel", { has: page.getByRole("heading", { name: "Анимации" }) });
    await expect(panel).toBeVisible();
    const found = panel.getByLabel("Найденные клипы");
    await expect(found.getByText("idle_calm.vrma")).toBeVisible();
    await expect(found.getByText("greeting.vrma")).toBeVisible();

    // naming convention by default: idle*.vrma is the idle loop, no wave.vrma → built-in wave
    await expect(panel.getByLabel("Анимация: Покой (фоновый цикл)")).toHaveValue("");
    await expect(
      panel.getByLabel("Анимация: Покой (фоновый цикл)").locator("option:checked"),
    ).toHaveText("Авто: idle_calm.vrma");
    const wave = panel.getByLabel("Анимация: Машет рукой");
    await expect(wave.locator("option:checked")).toHaveText("Авто: встроенная");
    await expect(panel.getByRole("button", { name: "Показать: Машет рукой" })).toBeVisible();

    await wave.selectOption("greeting.vrma");
    await expect
      .poll(async () => {
        const res = await page.request.get("/api/config", { headers });
        const cfg = (await res.json()) as {
          profiles: { aion: { avatar: { animations: Record<string, string> } } };
        };
        return cfg.profiles.aion.avatar.animations.wave;
      })
      .toBe("greeting.vrma");
    // the page re-renders after saving and shows the saved choice
    await expect(panel.getByLabel("Анимация: Машет рукой")).toHaveValue("greeting.vrma");
    await panel.screenshot({ path: info.outputPath("animations.png") });
  } finally {
    await avatar({ kind: "hud", path: null, animations: { wave: "" } });
  }
});
