import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` serves on :5173 and proxies /api to the FastAPI backend.
// Build: emits web/dist, which FastAPI serves at "/" (see api/app.py).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8090", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    manifest: true, // read by scripts/check-bundle.mjs
    chunkSizeWarningLimit: 5000, // plotly is ~4.5 MB raw; it is lazily loaded.
    rollupOptions: {
      output: {
        // Plotly is only ever reached through a dynamic import(): from the 3-D surface view
        // (src/pages/volatility/SurfaceView.tsx, lazy) and, until Lane P2 migrates the last raw
        // <Chart>, from src/components/plotlyLegacy.ts (scripts/check-bundle.mjs names both).
        manualChunks(id: string) {
          if (id.includes("plotly.js-dist-min")) return "plotly";
          if (id.includes("katex")) return "katex";
          if (/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@tanstack)\//.test(id)) return "vendor";
          return undefined;
        },
      },
    },
  },
});
