import { describe, expect, it } from "vitest";
import { niceScale } from "../src/components/bar-chart";
import { tintFraction } from "../src/components/running-hero";
import { setLanguage } from "../src/i18n";
import type { UsageHistory } from "../src/types";
import { formatCountdown, formatDate, formatVolume, parseDate } from "../src/util/format";
import { bathEquivalent, chartTicks, firstWeekday, periodLabel } from "../src/util/usage";

setLanguage("en-US");

describe("axis scale", () => {
  it("rounds the top the way the app's charts do", () => {
    expect(niceScale(12.69)).toEqual({ top: 16, step: 4 });
    expect(niceScale(45.6)).toEqual({ top: 60, step: 15 });
    expect(niceScale(0)).toEqual({ top: 4, step: 1 });
  });
});

describe("dates", () => {
  it("reads a bare date as that calendar day in every time zone", () => {
    const date = parseDate("2025-05-06");
    expect([date.getFullYear(), date.getMonth(), date.getDate()]).toEqual([2025, 4, 6]);
    expect(formatDate("2025-05-06")).toBe("May 6, 2025");
  });
});

describe("figures", () => {
  it("formats volumes and countdowns", () => {
    expect(formatVolume(1.74, "gal")).toBe("1.74 gal");
    expect(formatVolume(48.84, "gal")).toBe("48.8 gal");
    expect(formatVolume(452.3, "gal")).toBe("452 gal");
    expect(formatCountdown(754)).toBe("12:34");
    expect(formatCountdown(3723)).toBe("1:02:03");
  });

  it("puts everyday showers in the violet, hot ones in the red", () => {
    expect(tintFraction(104, "°F")).toBeCloseTo(0.56, 2);
    expect(tintFraction(118, "°F")).toBe(1);
    expect(tintFraction(39, "°C")).toBeCloseTo(0.5, 2);
  });

  it("only mentions baths when there's at least one", () => {
    expect(bathEquivalent(452, "gal")).toBe(11);
    expect(bathEquivalent(20, "gal")).toBeNull();
    expect(bathEquivalent(300, "L")).toBe(2);
  });
});

const history = (period: UsageHistory["period"], start: string, end: string, keys: string[]): UsageHistory => ({
  period,
  anchor: start,
  start,
  end,
  unit: "gal",
  buckets: keys.map((key) => ({ key, volume: 1, minutes: 1, uses: 1 })),
  total: { volume: keys.length, minutes: keys.length },
  average: { volume: 1, minutes: 1 },
  high: { volume: 1, minutes: 1 },
  previous: null,
  next: null,
});

describe("usage periods", () => {
  it("labels a month, a year and a calendar week", () => {
    expect(periodLabel(history("month", "2026-10-01", "2026-10-31", []))).toBe("Oct 2026");
    expect(periodLabel(history("year", "2026-01-01", "2026-12-31", []))).toBe("2026");
    // `formatRange` sets the dash between thin spaces.
    const week = periodLabel(history("week", "2026-10-25", "2026-10-31", [])).replace(/\s/g, " ");
    expect(week).toBe("Oct 25 – 31, 2026");
  });

  it("ticks a month at its first, middle and last day", () => {
    const keys = Array.from({ length: 31 }, (_, i) => `2026-10-${String(i + 1).padStart(2, "0")}`);
    expect(chartTicks(history("month", "2026-10-01", "2026-10-31", keys))).toEqual([
      { index: 0, label: "10/1" },
      { index: 15, label: "10/16" },
      { index: 30, label: "10/31" },
    ]);
  });

  it("starts a week where the user's profile says, else where their language does", () => {
    expect(firstWeekday({ language: "en-GB", first_weekday: "sunday" })).toBe(6);
    expect(firstWeekday({ language: "en-US", first_weekday: "monday" })).toBe(0);
    expect(firstWeekday({ language: "en-US", first_weekday: "language" })).toBe(6);
    expect(firstWeekday({ language: "en-GB", first_weekday: "language" })).toBe(0);
    expect(firstWeekday({ language: "ar-EG" })).toBe(5);
    expect(firstWeekday(undefined)).toBe(6);
  });

  it("ticks a year with month initials", () => {
    const keys = Array.from({ length: 12 }, (_, i) => `2026-${String(i + 1).padStart(2, "0")}`);
    expect(chartTicks(history("year", "2026-01-01", "2026-12-31", keys)).map((t) => t.label).join("")).toBe(
      "JFMAMJJASOND",
    );
  });
});
