/// <reference types="vitest" />
import { resolve } from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";

// В деве отдаёт index.html нужного клиента для вложенных путей (/operator/orders/1).
// В production это делает nginx (try_files ... /<role>/index.html).
const appFallback: Plugin = {
  name: "fpv-app-fallback",
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      const m = req.url && req.url.match(/^\/(customer|operator|coordinator|technician|recruiter|admin)(\/|$)/);
      if (m && req.headers.accept?.includes("text/html") && !/\.[a-z0-9]+(\?|$)/i.test(req.url!)) {
        req.url = `/${m[1]}/index.html`;
      }
      next();
    });
  },
};

const backend = process.env.FPV_BACKEND_URL ?? "http://127.0.0.1:8000";
const page = (name: string, file: string): [string, string] => [name, resolve(__dirname, file)];

// Отдельный клиент (бандл) на каждую роль + общий вход; код чужих ролей в бандл не попадает.
export default defineConfig({
  plugins: [react(), appFallback],
  server: {
    port: 5173,
    proxy: {
      "/api/v1/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      "/api": backend,
      "/health": backend,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // pdfmake со шрифтами (~1,2 МБ) — отдельный фрагмент, загружается только при формировании PDF-отчёта.
    chunkSizeWarningLimit: 1300,
    rollupOptions: {
      input: Object.fromEntries([
        page("login", "index.html"),
        page("customer", "customer/index.html"),
        page("operator", "operator/index.html"),
        page("coordinator", "coordinator/index.html"),
        page("technician", "technician/index.html"),
        page("recruiter", "recruiter/index.html"),
        page("admin", "admin/index.html"),
      ]),
      // Воркер pdf.js (*.mjs) — под расширением .js: nginx отдаёт .mjs не как JavaScript,
      // и браузер отказался бы запускать модульный воркер.
      output: {
        assetFileNames: (a) => ((a.names?.[0] ?? a.name ?? "").endsWith(".mjs")
          ? "assets/[name]-[hash].js" : "assets/[name]-[hash][extname]"),
      },
    },
  },
  test: { environment: "node" },
});
