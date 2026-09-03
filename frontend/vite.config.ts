import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/stats": "http://127.0.0.1:8000",
      "/disputes": "http://127.0.0.1:8000",
      "/merchants": "http://127.0.0.1:8000",
      "/assistant": "http://127.0.0.1:8000",
      "/internal": "http://127.0.0.1:8000",
      "/dev": "http://127.0.0.1:8000",
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
