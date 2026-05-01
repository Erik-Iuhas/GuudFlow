import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api and /ws to the FastAPI backend so we can run
// `npm run dev` and `uvicorn api.main:app` side by side without CORS pain.
const API_TARGET = process.env.GUUDFLOW_API_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
  server: {
    port: 3001,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/ws": { target: API_TARGET, ws: true, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
    },
  },
});
