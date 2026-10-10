import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// End-to-end tests run against the real Python backend in text mode (no microphone).
const PORT = 8799;
export const TOKEN = "e2e-token";

export default defineConfig({
  testDir: "tests/e2e",
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "uv run aion run --no-voice --headless",
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      AION_HOME: mkdtempSync(join(tmpdir(), "aion-e2e-")),
      AION_UI_TOKEN: TOKEN,
      AION_UI_PORT: String(PORT),
      PYTHONIOENCODING: "utf-8",
    },
  },
});
