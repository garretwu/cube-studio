import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 8080,
      strictPort: true,
      hmr: {
        host: "localhost",
        port: 5174,
      },
      watch: {
        usePolling: true,
      },
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
          ws: false,
        },
        "/ws": {
          target: proxyTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      css: true,
    },
  };
});
