import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During dev, the frontend runs on localhost:5173 and proxies /api
// requests to the FastAPI app on localhost:8000 so cookies stay on
// the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
      "/healthz": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
