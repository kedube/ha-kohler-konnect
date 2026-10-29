import { locale, t } from "../i18n";

function numberFormat(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  return new Intl.NumberFormat(locale(), options);
}

export function formatNumber(value: number, digits = 0): string {
  return numberFormat({ maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(
    value,
  );
}

export function formatTemperature(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${formatNumber(value, unit === "°C" ? 1 : 0)}${unit}`;
}

/** Volume with two decimals under 10, one under 100, none above: `1.74 gal`, `48.8 gal`. */
export function formatVolume(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const digits = Math.abs(value) < 10 ? 2 : Math.abs(value) < 100 ? 1 : 0;
  return `${formatNumber(value, digits)} ${unit}`;
}

export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return "—";
  const rounded = Math.round(minutes);
  if (rounded < 60) return t("unit.minutes_short", { count: rounded });
  return t("unit.hours_minutes", { hours: Math.floor(rounded / 60), minutes: rounded % 60 });
}

/** `754` → `12:34`; an hour or more → `1:02:03`. */
export function formatCountdown(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  const whole = Math.round(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/**
 * A date as written. A bare `2025-05-06` is a calendar date, not UTC midnight: parsed as
 * the latter it would read as May 5 anywhere west of Greenwich.
 */
export function parseDate(iso: string): Date {
  const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return bare ? new Date(Number(bare[1]), Number(bare[2]) - 1, Number(bare[3])) : new Date(iso);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(locale(), { dateStyle: "medium" }).format(date);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(
    date,
  );
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((then - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

/** A wall-clock time given as `HH:MM`, as the locale writes one ("7:05 PM"). */
export function formatClock(value: string): string {
  const [hours, minutes] = value.split(":").map(Number);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return value;
  return new Intl.DateTimeFormat(locale(), { timeStyle: "short" }).format(
    new Date(2024, 0, 1, hours, minutes),
  );
}

const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

/** Weekdays by their keys (`mon` … `sun`), as the locale abbreviates them, in week order. */
export function formatWeekdays(days: readonly string[]): string {
  const format = new Intl.DateTimeFormat(locale(), { weekday: "short" });
  // 1 January 2024 was a Monday.
  return WEEKDAY_KEYS.flatMap((key, index) =>
    days.includes(key) ? [format.format(new Date(2024, 0, 1 + index))] : [],
  ).join(", ");
}
