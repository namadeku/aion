// End-to-end checks for the Commands page, the live work log (confirm / stop), the command
// palette, history search, the plugin showcase and the settings added with them.
// Only harmless actions run here: `say` with `confirm: always`. Never shutdown, delete or shell.

import { expect, test, type APIRequestContext } from "@playwright/test";
import { TOKEN } from "../../playwright.config";

const headers = { "X-Aion-Token": TOKEN };

/** Create (or overwrite) a command that only speaks, behind a «Да / Нет» question. */
async function confirmCommand(
  request: APIRequestContext,
  id: string,
  phrase: string,
): Promise<void> {
  const res = await request.put(`/api/commands/${id}`, {
    headers,
    data: {
      name: `Проверка ${id}`,
      triggers: [phrase],
      confirm: "always",
      actions: [{ type: "say", text: "Готово, проверка прошла" }],
    },
  });
  expect(res.ok()).toBeTruthy();
}

interface LiveConfig {
  audio: { normalize: boolean };
  wakeword: { backend: string };
  commands: { require_wake_name: boolean };
}

async function config(request: APIRequestContext): Promise<LiveConfig> {
  const res = await request.get("/api/config", { headers });
  return (await res.json()) as LiveConfig;
}

test("commands: create with a validation error, save, test with confirm buttons", async ({
  page,
}, info) => {
  const errors: string[] = [];
  // the validation answer (422) is expected and logged by the browser itself
  page.on(
    "console",
    (m) => m.type() === "error" && !m.text().includes("status of 422") && errors.push(m.text()),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  const name = `E2E ${info.project.name}`;
  await page.goto(`/?token=${TOKEN}#/commands`);
  await expect(page.getByRole("heading", { level: 1, name: "Команды" })).toBeVisible();
  // the example scenarios of the first start are listed with their folder switch
  await expect(page.getByRole("button", { name: "Протокол гостей", exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Папка examples включена" })).toBeChecked();

  await page.getByRole("button", { name: "Создать" }).click();
  await page.getByLabel("Название").fill(name);
  await page.getByLabel("Фраза 1").fill(`${name.toLowerCase()} запуск`);
  await page.getByLabel("Подтверждение", { exact: true }).selectOption("always");
  // the only action is an empty «Сказать»: the server explains what is wrong next to it
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.locator(".action-card .err-text")).toContainText("действие 1");
  await expect(page.locator(".error-box")).toBeVisible();

  await page.getByLabel("Фраза", { exact: true }).fill("Готово, проверка прошла");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.locator(".error-box")).toBeHidden();
  await expect(page.getByText("Есть несохранённые изменения")).toHaveCount(0);

  // reorder with buttons: add a pause and move it up
  await page.getByLabel("Тип нового действия").selectOption("wait");
  await page.getByRole("button", { name: "Добавить действие" }).click();
  await page.getByRole("button", { name: "Поднять действие 2" }).click();
  await expect(page.getByLabel("Тип действия 1")).toHaveValue("wait");
  await page.getByRole("button", { name: "Удалить действие 1" }).click();
  await expect(page.getByLabel("Тип действия 1")).toHaveValue("say");

  await page.screenshot({ path: info.outputPath("editor.png"), fullPage: true });
  await page.getByRole("button", { name: "Проверить" }).click();
  const log = page.getByRole("region", { name: "Ход работы" });
  await expect(log.getByRole("group", { name: "Подтверждение" })).toBeVisible();
  await log.getByRole("button", { name: "Да", exact: true }).click();
  await expect(log.locator(".worklog-status")).toHaveText("Готово", { timeout: 15_000 });
  await expect(log.getByText("Сказать: Готово, проверка прошла")).toBeVisible();

  // back to the list: the command is there, can be switched off and deleted
  await page.getByRole("button", { name: "К списку" }).first().click();
  const row = page.locator(".cmd-row", { hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole("checkbox", { name: /включена/ }).uncheck();
  await expect(row).toHaveClass(/off/);
  page.once("dialog", (d) => void d.accept());
  await row.getByRole("button", { name: /Удалить/ }).click();
  await expect(row).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("work log: confirm buttons and the stop button interrupt a run", async ({ page }, info) => {
  const id = `e2e-stop-${info.project.name}`;
  await confirmCommand(page.request, id, `проверка стоп ${info.project.name}`);
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByText("соединение есть")).toBeAttached();
  const input = page.getByLabel("Сообщение");
  await input.fill(`проверка стоп ${info.project.name}`);
  await input.press("Enter");
  const log = page.getByRole("region", { name: "Ход работы" });
  await expect(log.getByRole("group", { name: "Подтверждение" })).toBeVisible();
  await expect(log.getByRole("button", { name: "Нет", exact: true })).toBeVisible();
  await expect(log.locator(".worklog-countdown")).toContainText("ещё");
  // a run is active: the round button is «Стоп»
  const stop = page.getByRole("button", { name: "Стоп", exact: true });
  await expect(stop).toBeVisible();
  await page.screenshot({ path: info.outputPath("confirm.png") });
  await stop.click();
  await expect(log.locator(".worklog-status")).toHaveText("Прервано", { timeout: 15_000 });
  await expect(log.getByRole("group", { name: "Подтверждение" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Отправить", exact: true })).toBeVisible();
  // clean up
  await page.request.delete(`/api/commands/${id}`, { headers });
});

test("work log: answering «Нет» cancels the command", async ({ page }, info) => {
  const id = `e2e-no-${info.project.name}`;
  await confirmCommand(page.request, id, `проверка отказ ${info.project.name}`);
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByText("соединение есть")).toBeAttached();
  const input = page.getByLabel("Сообщение");
  await input.fill(`проверка отказ ${info.project.name}`);
  await input.press("Enter");
  const log = page.getByRole("region", { name: "Ход работы" });
  await log.getByRole("button", { name: "Нет", exact: true }).click();
  await expect(log.locator(".worklog-status")).not.toHaveText("Выполняется", { timeout: 15_000 });
  await expect(page.locator(".msg.assistant").last()).toContainText("Отменено");
  await page.request.delete(`/api/commands/${id}`, { headers });
});

test("code mode: the palette filters commands and Enter runs the selected one", async ({
  page,
}, info) => {
  const id = `e2e-palette-${info.project.name}`;
  await confirmCommand(page.request, id, `палитра ${info.project.name}`);
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByText("соединение есть")).toBeAttached();
  const code = page.getByRole("button", { name: "Режим команды или кода" });
  await code.click();
  const input = page.getByLabel("Сообщение");
  await input.fill(`Проверка ${id}`);
  const option = page.getByRole("option", { name: new RegExp(id) });
  await expect(option).toBeVisible();
  await expect(option).toHaveAttribute("aria-selected", "true");
  await input.press("Enter");
  const log = page.getByRole("region", { name: "Ход работы" });
  await expect(log.getByRole("group", { name: "Подтверждение" })).toBeVisible();
  await log.getByRole("button", { name: "Да", exact: true }).click();
  await expect(log.locator(".worklog-status")).toHaveText("Готово", { timeout: 15_000 });
  // Esc leaves the mode
  await input.press("Escape");
  await expect(code).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("listbox", { name: "Команды" })).toBeHidden();
  await page.request.delete(`/api/commands/${id}`, { headers });
});

test("packs page: prefix mode and the games warning", async ({ page }, info) => {
  await page.goto(`/?token=${TOKEN}#/commands/packs`);
  await expect(page.getByRole("heading", { level: 1, name: "Паки и режим" })).toBeVisible();
  await expect(page.getByText(/античитом/).first()).toBeVisible();
  const prefix = page.getByRole("checkbox", { name: "Режим префикса" });
  await prefix.check();
  await expect.poll(async () => (await config(page.request)).commands.require_wake_name).toBe(true);
  await page.screenshot({ path: info.outputPath("packs.png"), fullPage: true });
  await page.getByRole("checkbox", { name: "Режим префикса" }).uncheck();
  await expect
    .poll(async () => (await config(page.request)).commands.require_wake_name)
    .toBe(false);
});

test("history: search highlights matches and shows kind badges", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByText("соединение есть")).toBeAttached();
  const input = page.getByLabel("Сообщение");
  await input.fill("сколько будет семь плюс семь");
  await input.press("Enter");
  await expect(page.locator(".msg.assistant").last()).toHaveText(/Будет 14\./);
  await page.getByRole("link", { name: "История" }).click();
  await page.getByLabel("Поиск по истории").fill("семь");
  await expect(page.locator(".history-item mark").first()).toHaveText(/семь/i);
  await expect(page.locator(".kind-message").first()).toHaveText("сообщение");
  await page.getByLabel("Поиск по истории").fill("такого слова нет нигде");
  await expect(page.getByText("Ничего не найдено")).toBeVisible();
});

test("plugins showcase: cards, dangerous permissions, settings drawer with Esc", async ({
  page,
}, info) => {
  await page.goto(`/?token=${TOKEN}#/plugins`);
  const telegram = page.locator(".plugin-card[data-name=telegram]");
  await expect(telegram).toBeVisible();
  await expect(telegram.getByText("Как подключить бота")).toBeVisible();
  // a dangerous permission is highlighted
  await expect(page.locator(".plugin-card .perms .tag.warn").first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("plugins.png"), fullPage: true });
  await telegram.getByRole("button", { name: "Настройки" }).click();
  const drawer = page.getByRole("dialog", { name: "Telegram-бот" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("AION_TELEGRAM_TOKEN=ваш_токен")).toBeVisible();
  await expect(drawer.getByLabel("Токен бота")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  // search and filters
  await page.getByLabel("Поиск плагинов").fill("погода");
  await expect(page.locator(".plugin-card")).toHaveCount(1);
  await page.getByLabel("Поиск плагинов").fill("");
  await page.getByRole("button", { name: "Мои" }).click();
  await expect(page.locator(".plugin-card[data-name=telegram]")).toHaveCount(0);
});

test("microphone settings save the new fields", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}#/settings/audio`);
  const normalize = page.getByRole("checkbox", { name: /Выравнивать тихую речь/ });
  await expect(normalize).toBeVisible();
  const before = (await config(page.request)).audio.normalize;
  await normalize.setChecked(!before);
  await expect.poll(async () => (await config(page.request)).audio.normalize).toBe(!before);
  await page.getByRole("checkbox", { name: /Выравнивать тихую речь/ }).setChecked(before);
  await expect.poll(async () => (await config(page.request)).audio.normalize).toBe(before);
  // push-to-talk only mode switches the wake word backend off and back
  const ptt = page.getByRole("checkbox", { name: /Только кнопка или горячая клавиша/ });
  await ptt.check();
  await expect.poll(async () => (await config(page.request)).wakeword.backend).toBe("none");
  await page.getByRole("checkbox", { name: /Только кнопка или горячая клавиша/ }).uncheck();
  await expect.poll(async () => (await config(page.request)).wakeword.backend).toBe("stt");
});

test("voice presets: apply one and see it highlighted", async ({ page }, info) => {
  await page.goto(`/?token=${TOKEN}#/settings/voice`);
  // the backend keeps the choice between the desktop and mobile runs: take one not chosen yet
  const card = page.locator(".preset-card:not(.current)").first();
  await expect(card).toBeVisible();
  const id = (await card.getAttribute("data-preset")) ?? "";
  await card.getByRole("button", { name: /Выбрать голос/ }).click();
  await expect(page.locator(".preset-card.current")).toHaveAttribute("data-preset", id);
  await expect(page.locator(".preset-card.current").locator(".tag.ok")).toHaveText("выбран");
  // the badges for licence and network are there
  await expect(page.getByText("только некоммерческое").first()).toBeVisible();
  await expect(page.getByText("нужен интернет").first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("voice.png") });
});

test("console: filter by event type, pause and clear", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}#/console`);
  await expect(page.getByText("соединение есть")).toBeAttached();
  await page.request.post("/api/say", { headers, data: { text: "сколько будет один плюс один" } });
  await expect(page.locator(".console-type", { hasText: "speech_recognized" })).toBeVisible();
  const types = page.getByLabel("Тип события");
  await types.selectOption("speech_recognized");
  await expect(page.locator(".console-type", { hasText: "state_changed" })).toHaveCount(0);
  await expect(page.locator(".console-type", { hasText: "speech_recognized" })).toBeVisible();
  await page.getByRole("button", { name: "Пауза" }).click();
  await expect(page.getByRole("button", { name: "Продолжить" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByRole("button", { name: "Очистить" }).click();
  await expect(page.locator(".console-line")).toHaveCount(0);
});
