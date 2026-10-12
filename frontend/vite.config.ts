/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.FPV_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/v1/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      "/api": backend,
      "/health": backend,
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node" },
});
