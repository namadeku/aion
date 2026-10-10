import { defineConfig } from "vite";

// The UI is served by the Python backend (FastAPI) from src/aion/ui/static.
// In dev (`pnpm dev`) API and WebSocket calls are proxied to a running `aion run`.
const BACKEND = process.env.AION_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  base: "/",
  build: {
    outDir: "../src/aion/ui/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    proxy: {
      "/api": BACKEND,
      "/avatar": BACKEND,
      "/ws": { target: BACKEND.replace("http", "ws"), ws: true },
    },
  },
});
