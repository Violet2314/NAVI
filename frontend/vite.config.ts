import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "path";

const host = (process.env as Record<string, string | undefined>).TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react(), tailwindcss()],

  // 多入口：主窗口 + live2d 伴侣窗口
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        live2d: resolve(__dirname, "live2d.html"),
      },
    },
  },

  // pixi-live2d-display 使用 CommonJS，需要排除后让 Vite 处理
  optimizeDeps: {
    include: ["pixi.js", "pixi-live2d-display"],
    exclude: [],
  },

  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
}));