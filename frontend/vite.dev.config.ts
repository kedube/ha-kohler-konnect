import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// The design harness: the real panel against a fake Home Assistant. `npm run mockup` builds
// it into one self-contained HTML file for review outside a dev server.
export default defineConfig({
  root: "dev",
  plugins: [viteSingleFile()],
  build: {
    target: "es2022",
    outDir: "../dist-mockup",
    emptyOutDir: true,
  },
});
