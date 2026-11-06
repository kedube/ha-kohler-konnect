import { t, type MessageKey } from "../i18n";
import { formatNumber } from "./format";

/** The `dispense` action's names for the units the Konnect app dispenses in. */
export type DispenseUnit = "ml" | "l" | "cup" | "qt" | "gal";

// The app's dispense wheels, in its order: `docs/protocol/sensate_faucet.md` §4.1 and §4.2.
const WHEELS: Record<DispenseUnit, { step: number; max: number; name: MessageKey }> = {
  ml: { step: 250, max: 11000, name: "faucet.unit_ml" },
  l: { step: 0.25, max: 11, name: "faucet.unit_l" },
  cup: { step: 1, max: 48, name: "faucet.unit_cup" },
  qt: { step: 0.25, max: 12, name: "faucet.unit_qt" },
  gal: { step: 0.25, max: 3, name: "faucet.unit_gal" },
};

export const DISPENSE_UNITS = Object.keys(WHEELS) as DispenseUnit[];

/** Where the app opens every wheel: 750 mL, ¾ L, 3 cups, ¾ qt, ¾ gal. */
export const DEFAULT_AMOUNT_INDEX = 2;

export function dispenseAmounts(unit: DispenseUnit): number[] {
  const { step, max } = WHEELS[unit];
  return Array.from({ length: max / step }, (_, index) => (index + 1) * step);
}

export function dispenseUnitName(unit: DispenseUnit): string {
  return t(WHEELS[unit].name);
}

const FRACTIONS: Record<number, string> = { 0.25: "¼", 0.5: "½", 0.75: "¾" };

/** An amount as the app writes it: `750`, `1¾`, `¼`. An automation may ask for any other: `1.3`. */
export function formatDispenseAmount(amount: number): string {
  const whole = Math.floor(amount);
  const fraction = amount - whole;
  if (!fraction) return formatNumber(whole);
  const glyph = FRACTIONS[fraction];
  return glyph ? `${whole ? formatNumber(whole) : ""}${glyph}` : formatNumber(amount, 2);
}

const SHORT_NAMES = { ml: "mL", l: "L", qt: "qt", gal: "gal" };

/** An amount with its unit, for a sentence: `750 mL`, `¾ gal`, `1 cup`, `3 cups`. */
export function formatDispensed(amount: number, unit: DispenseUnit): string {
  const name = unit === "cup" ? t(amount > 1 ? "faucet.cups" : "faucet.cup") : SHORT_NAMES[unit];
  return `${formatDispenseAmount(amount)} ${name}`;
}

/** An amount as it was asked for, if that is known and in one of the app's units. */
export function formatAsked(source: { asked_amount?: unknown; asked_unit?: unknown }): string | null {
  const { asked_amount: amount, asked_unit: unit } = source;
  return typeof amount === "number" && DISPENSE_UNITS.includes(unit as DispenseUnit)
    ? formatDispensed(amount, unit as DispenseUnit)
    : null;
}
