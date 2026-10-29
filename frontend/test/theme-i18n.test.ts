import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { THEME_COLOURS } from "../src/theme/tokens";

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("theme contrast (WCAG AA for text)", () => {
  for (const [name, c] of Object.entries(THEME_COLOURS)) {
    it(`${name}: text on canvas and cards`, () => {
      for (const background of [c.canvas, c.surface]) {
        expect(contrast(c.text, background)).toBeGreaterThanOrEqual(7);
        expect(contrast(c.text2, background)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(c.text3, background)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(c.accentText, background)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(c.destructive, background)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(c.danger, background)).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`${name}: text on the accent and danger fills`, () => {
      expect(contrast(c.accentOn, c.accentFill)).toBeGreaterThanOrEqual(4.5);
      expect(contrast("#ffffff", c.dangerFill)).toBeGreaterThanOrEqual(4.5);
    });
  }
});

describe("translations", () => {
  const folder = join(__dirname, "..", "src", "i18n");
  const english = JSON.parse(readFileSync(join(folder, "en.json"), "utf8")) as Record<string, string>;
  const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

  for (const file of readdirSync(folder).filter((f) => f.endsWith(".json") && f !== "en.json")) {
    it(`${file} has every key, with the same placeholders`, () => {
      const other = JSON.parse(readFileSync(join(folder, file), "utf8")) as Record<string, string>;
      expect(Object.keys(other).sort()).toEqual(Object.keys(english).sort());
      for (const key of Object.keys(english)) {
        expect(placeholders(other[key]), key).toEqual(placeholders(english[key]));
      }
    });
  }

  it("English has no empty strings", () => {
    for (const [key, text] of Object.entries(english)) expect(text.trim(), key).not.toBe("");
  });
});
