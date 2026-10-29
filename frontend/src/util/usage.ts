import type { ChartBar, ChartTick } from "../components/bar-chart";
import { locale } from "../i18n";
import type { UsageBucket, UsageHistory } from "../types";
import { formatMinutes, formatVolume } from "./format";

export type UsageMetric = "volume" | "minutes";

/** `2026-10-25` or `2026-10` as a local date, never shifted by a UTC parse. */
export function bucketDate(key: string): Date {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, (month || 1) - 1, day || 1);
}

function value(bucket: UsageBucket, metric: UsageMetric): number | null {
  return metric === "volume" ? bucket.volume : bucket.minutes;
}

export function formatFigure(amount: number | null, metric: UsageMetric, unit: string): string {
  return metric === "volume" ? formatVolume(amount, unit) : formatMinutes(amount);
}

export function chartBars(history: UsageHistory, metric: UsageMetric): ChartBar[] {
  const yearly = history.period === "year";
  const label = new Intl.DateTimeFormat(
    locale(),
    yearly ? { month: "long" } : { weekday: "short", month: "short", day: "numeric" },
  );
  return history.buckets.map((bucket) => {
    const text = label.format(bucketDate(bucket.key));
    const amount = value(bucket, metric);
    return {
      key: bucket.key,
      label: text,
      value: amount,
      tooltip: `${text} · ${formatFigure(amount ?? 0, metric, history.unit)}`,
    };
  });
}

export function chartTicks(history: UsageHistory): ChartTick[] {
  const buckets = history.buckets;
  if (history.period === "week") {
    const weekday = new Intl.DateTimeFormat(locale(), { weekday: "narrow" });
    return buckets.map((b, index) => ({ index, label: weekday.format(bucketDate(b.key)) }));
  }
  if (history.period === "year") {
    const month = new Intl.DateTimeFormat(locale(), { month: "narrow" });
    return buckets.map((b, index) => ({ index, label: month.format(bucketDate(b.key)) }));
  }
  const day = new Intl.DateTimeFormat(locale(), { month: "numeric", day: "numeric" });
  const last = buckets.length - 1;
  return [0, Math.floor(last / 2), last]
    .filter((index, i, all) => index >= 0 && all.indexOf(index) === i)
    .map((index) => ({ index, label: day.format(bucketDate(buckets[index].key)) }));
}

export function periodLabel(history: UsageHistory): string {
  const start = bucketDate(history.start);
  const end = bucketDate(history.end);
  if (history.period === "year") return String(start.getFullYear());
  if (history.period === "month") {
    return new Intl.DateTimeFormat(locale(), { month: "short", year: "numeric" }).format(start);
  }
  const format = new Intl.DateTimeFormat(locale(), { month: "short", day: "numeric", year: "numeric" });
  return format.formatRange ? format.formatRange(start, end) : `${format.format(start)} – ${format.format(end)}`;
}

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
// Where weeks start on Sunday, for a browser whose `Intl` can't say.
const SUNDAY_REGIONS = new Set(["US", "CA", "MX", "BR", "JP", "IL"]);

type WeekInfoLocale = Intl.Locale & {
  getWeekInfo?: () => { firstDay: number };
  weekInfo?: { firstDay: number };
};

/**
 * The first day of the week, numbered as Python numbers weekdays (Monday 0 … Sunday 6):
 * the one chosen in the user's Home Assistant profile, else their language's custom.
 */
export function firstWeekday(settings?: { language: string; first_weekday?: string }): number {
  const chosen = WEEKDAYS.indexOf(settings?.first_weekday ?? "");
  if (chosen >= 0) return chosen;
  try {
    const tag = new Intl.Locale(settings?.language || "en") as WeekInfoLocale;
    // `Intl` numbers Monday 1 … Sunday 7.
    const firstDay = tag.getWeekInfo?.().firstDay ?? tag.weekInfo?.firstDay;
    if (firstDay) return firstDay - 1;
    return SUNDAY_REGIONS.has(tag.maximize().region ?? "") ? 6 : 0;
  } catch {
    return 0;
  }
}

/** A full bath is about 40 US gallons or 150 litres; under one, say nothing. */
export function bathEquivalent(total: number | null, unit: string): number | null {
  if (!total) return null;
  const bath = unit.toLowerCase().startsWith("gal") ? 40 : 150;
  const baths = total / bath;
  return baths >= 1 ? Math.round(baths) : null;
}
