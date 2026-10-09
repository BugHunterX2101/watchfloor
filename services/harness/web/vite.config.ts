import { defineConfig } from "vite";

// The dev server (:5173) proxies the harness API (:8082), so the session cookie stays first-party.
// Set WF_HARNESS_WEB_URL=http://localhost:5173/ so sign-in lands back here.
const api = "http://127.0.0.1:8082";

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": api, "/converse": api, "/oauth": api, "/healthz": api },
  },
  build: { outDir: "dist", emptyOutDir: true, target: "es2022" },
});
