import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Tauri expects a fixed port; env variables only with prefix VITE_/TAURI_
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true, host: "localhost" },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: { target: "es2022", sourcemap: false, outDir: "dist" },
  // Camera detection runs as a classic worker (MediaPipe uses importScripts)
  worker: { format: "iife" },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
