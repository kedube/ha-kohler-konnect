// Fails the build when the panel bundle outgrows its budget. Home Assistant loads it once per
// version (it is cached until the next), often over a phone connection. About 28 KB of it is
// the nine translations; if the panel itself pushes past this, load languages on demand
// rather than raising it again.
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUNDLE = "../../custom_components/kohler_konnect/www/kohler-konnect-panel.js";
const BUDGET_KB = 100;

const size = gzipSync(readFileSync(new URL(BUNDLE, import.meta.url))).length / 1024;
const line = `kohler-konnect-panel.js: ${size.toFixed(1)} KB gzipped (budget ${BUDGET_KB} KB)`;
if (size > BUDGET_KB) {
  console.error(`${line} — over budget`);
  process.exit(1);
}
console.log(line);
