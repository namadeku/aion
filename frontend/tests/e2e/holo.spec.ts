import { expect, test } from "@playwright/test";
import { TOKEN } from "../../playwright.config";

const headers = { "X-Aion-Token": TOKEN };

test("holo avatar draws the hologram without errors", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(e.message));

  const avatar = (value: Record<string, unknown>) =>
    page.request.patch("/api/config", {
      headers,
      data: { profiles: { aion: { avatar: value } } },
    });
  expect((await avatar({ kind: "holo", path: null })).ok()).toBe(true);
  try {
    await page.goto(`/?token=${TOKEN}`);
    await expect(page.getByText("соединение есть")).toBeAttached();
    const canvas = page.locator(".orb-panel[data-look='holo'] canvas.view");
    await expect(canvas).toBeAttached();
    // wait until the bust is drawn: a good share of pixels is not transparent
    await expect
      .poll(
        () =>
          canvas.evaluate((el) => {
            const c = el as HTMLCanvasElement;
            const ctx = c.getContext("2d");
            if (!ctx || !c.width || !c.height) return 0;
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            let lit = 0;
            for (let i = 3; i < data.length; i += 4 * 16) if ((data[i] ?? 0) > 24) lit++;
            return lit / (data.length / 64);
          }),
        { timeout: 10_000 },
      )
      .toBeGreaterThan(0.05);
    await page.screenshot({ path: info.outputPath("holo.png") });
    expect(errors).toEqual([]);
  } finally {
    await avatar({ kind: "hud", path: null });
  }
});
