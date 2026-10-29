import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the UI on :5173 and proxies the API to the Flask server on :8000.
// `npm run build` writes the production bundle to ../dist, which Flask serves.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../dist", emptyOutDir: true, sourcemap: false },
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } } },
});
