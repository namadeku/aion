import { expect, test } from "@playwright/test";
import { TOKEN } from "../../playwright.config";

test("home: connects and answers a typed command", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByText("соединение есть")).toBeAttached();
  await expect(page).toHaveURL(/^[^?]*$/); // token removed from the address bar

  const input = page.getByLabel("Сообщение");
  await input.fill("сколько будет два плюс два");
  await input.press("Enter");
  await expect(page.locator(".msg.assistant").last()).toHaveText(/Будет 4\./);
  expect(errors).toEqual([]);
});

test("plugins page lists builtin skills", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}#/plugins`);
  await expect(page.getByRole("heading", { name: "Погода" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Таймеры и напоминания" })).toBeVisible();
});

test("without a token the API refuses", async ({ request }) => {
  const res = await request.get("/api/config");
  expect(res.status()).toBe(401);
});

test("llm page saves event commentary settings", async ({ page }, info) => {
  await page.goto(`/?token=${TOKEN}#/llm`);
  const panel = page.locator(".panel", { hasText: "Комментарии к событиям" });
  await expect(panel).toBeVisible();
  const slider = panel.locator('input[type="range"]').first();
  await slider.fill("0.9");
  await expect(panel.getByText("болтушка")).toBeVisible();
  await expect
    .poll(async () => {
      const res = await page.request.get("/api/config", { headers: { "X-Aion-Token": TOKEN } });
      return ((await res.json()) as { initiative: { talkativeness: number } }).initiative
        .talkativeness;
    })
    .toBe(0.9);
  // the page re-renders after saving: check the fresh panel shows the saved value
  await expect(panel.getByText("болтушка")).toBeVisible();
  await page.screenshot({ path: info.outputPath("initiative.png"), fullPage: true });
});

test("game plugins are listed", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}#/plugins`);
  for (const name of ["Counter-Strike 2", "Dota 2", "Valorant"]) {
    await expect(page.getByRole("heading", { name })).toBeVisible();
  }
});

test("home shows the character's mood", async ({ page }, info) => {
  await page.goto(`/?token=${TOKEN}`);
  const mood = page.getByRole("group", { name: "Настроение" });
  await expect(mood).toBeVisible();
  // the meters are filled from the snapshot sent by the backend
  await expect(mood.locator(".mood-row").first()).toHaveAttribute("title", /энергия: \d+%/);
  await page.screenshot({ path: info.outputPath("home.png") });
});

test("mcp servers can be added and removed", async ({ page }, info) => {
  page.on("dialog", (d) => void d.accept());
  await page.goto(`/?token=${TOKEN}#/plugins`);
  const mcp = page.locator(".panel", { hasText: "MCP-серверы" });
  await expect(mcp).toBeVisible();
  const name = `e2e${info.project.name}`;
  await mcp.getByPlaceholder("имя, например time").fill(name);
  await mcp.getByPlaceholder(/uvx mcp-server-time/).fill('aion-missing-server --flag "two words"');
  await mcp.getByRole("button", { name: "Подключить" }).click();
  const row = mcp.locator(".mcp-server", { hasText: name });
  await expect(row.getByText("aion-missing-server --flag two words")).toBeVisible();
  // the command does not exist: the server reports an error, nothing else breaks
  await expect(row.locator(".tag.err")).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: info.outputPath("mcp.png"), fullPage: true });
  await row.getByRole("button", { name: "Удалить" }).click();
  await expect(row).toHaveCount(0);
});
