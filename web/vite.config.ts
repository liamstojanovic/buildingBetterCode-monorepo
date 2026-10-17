import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "execution-graph": ["@xyflow/react", "@dagrejs/dagre"],
          "contract-validation": ["ajv", "ajv-formats"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.CANOPY_API_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
