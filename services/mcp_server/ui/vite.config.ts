import preact from "@preact/preset-vite";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// One build per view (`vite build --mode board|detail|confirm`): vite-plugin-singlefile inlines
// cleanly with a single entry (TD §6.6). Output: ../mcp_server/ui_dist/<view>.html, served as ui://.
const VIEWS = new Set(["board", "detail", "confirm"]);

export default defineConfig(({ mode }) => {
  if (!VIEWS.has(mode)) throw new Error(`build with --mode board|detail|confirm (got ${mode})`);
  return {
    plugins: [preact(), viteSingleFile()],
    build: {
      outDir: "../mcp_server/ui_dist",
      emptyOutDir: false,
      target: "es2020",
      rollupOptions: { input: `${mode}.html` },
    },
  };
});
