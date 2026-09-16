import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// FewType 前端（Tauri 迁移阶段二）
// 开发时后端 FastAPI 跑在 5010，代理让 /api 与 /ws 直通，避免 CORS 干扰
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:5010", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:5010", ws: true },
      "/health": { target: "http://127.0.0.1:5010", changeOrigin: true },
    },
  },
  // Tauri 桌面壳需要相对路径资源（custom protocol）
  base: "./",
  build: {
    outDir: "dist",
    target: "es2021",
  },
  clearScreen: false,
});
