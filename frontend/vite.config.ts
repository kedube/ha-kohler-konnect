import { defineConfig } from "vitest/config";

// The panel ships as one ES module that Home Assistant loads with `panel_custom`. Lit is
// bundled in, and with no dynamic imports there is nothing to split out. The file is
// written straight into the integration.
export default defineConfig({
  build: {
    target: "es2022",
    outDir: "../custom_components/kohler_konnect/www",
    emptyOutDir: false,
    sourcemap: false,
    lib: {
      entry: "src/kohler-konnect-panel.ts",
      formats: ["es"],
      fileName: () => "kohler-konnect-panel.js",
    },
  },
  test: {
    environment: "happy-dom",
    include: ["test/**/*.test.ts"],
  },
});
