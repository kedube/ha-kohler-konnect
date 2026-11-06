import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KkpWheel } from "../src/components/wheel";
import type { PanelContext } from "../src/context";
import { setLanguage } from "../src/i18n";
import type { DeviceDescriptor, HassEntity, HomeAssistant } from "../src/types";
import { DISPENSE_UNITS, dispenseAmounts, formatDispensed, formatDispenseAmount } from "../src/util/dispense";
import type { KkpFaucet } from "../src/views/faucet";
import "../src/views/faucet";

setLanguage("en");

const WATER = "switch.kitchen_water";

async function settle(element: { updateComplete: Promise<unknown> }) {
  await element.updateComplete;
  await new Promise((resolve) => setTimeout(resolve));
  await element.updateComplete;
}

/** A faucet's view on the Dispense Amount mode, for an account in the given units. */
async function dispenseView(volume: "L" | "gal") {
  const calls: Record<string, unknown>[] = [];
  const view = document.createElement("kkp-faucet") as KkpFaucet;
  view.hass = {
    states: {
      [WATER]: { entity_id: WATER, state: "off", attributes: {}, last_changed: "", last_updated: "" } as HassEntity,
    },
  } as unknown as HomeAssistant;
  view.device = {
    id: "dev_faucet",
    entry_id: "entry",
    kind: "faucet",
    name: "Kitchen",
    units: { temperature: "°F", volume, dispense: volume === "L" ? "mL" : "fl oz" },
    zones: [],
    capabilities: {},
    roles: { water: WATER },
    disabled_roles: {},
    untested: [],
  } as unknown as DeviceDescriptor;
  view.ctx = {
    api: {
      service: vi.fn(async (_domain: string, _service: string, data: Record<string, unknown>) => {
        calls.push(data);
      }),
      queue: () => ({ enqueue: (_key: string, run: () => Promise<unknown>) => run() }),
    },
    admin: true,
    wide: false,
    navigate: () => {},
    toast: vi.fn(),
    confirm: async () => true,
  } as unknown as PanelContext;
  document.body.append(view);
  await view.updateComplete;
  const root = view.shadowRoot!;
  root.querySelectorAll<HTMLElement>(".mode")[1].click();
  await settle(view);

  const wheels = () => [...root.querySelectorAll<KkpWheel>("kkp-wheel")];
  const options = (wheel: KkpWheel) =>
    [...wheel.shadowRoot!.querySelectorAll<HTMLElement>('[role="option"]')];
  const selected = (wheel: KkpWheel) =>
    options(wheel).find((option) => option.getAttribute("aria-selected") === "true")?.textContent?.trim();
  const pick = async (wheel: KkpWheel, text: string) => {
    options(wheel).find((option) => option.textContent?.trim() === text)!.click();
    await settle(view);
    await settle(wheel);
  };
  const start = async () => {
    root.querySelector<HTMLElement>(".start")!.click();
    await settle(view);
    return calls.at(-1);
  };
  return { view, wheels, options, selected, pick, start };
}

beforeEach(() => document.body.replaceChildren());

describe("the dispense amounts", () => {
  it.each([
    ["ml", 250, 250, 11000, 44],
    ["l", 0.25, 0.25, 11, 44],
    ["cup", 1, 1, 48, 48],
    ["qt", 0.25, 0.25, 12, 48],
    ["gal", 0.25, 0.25, 3, 12],
  ] as const)("%s runs from the app's first amount to its last", (unit, first, step, last, count) => {
    const amounts = dispenseAmounts(unit);
    expect(amounts[0]).toBe(first);
    expect(amounts.at(-1)).toBe(last);
    expect(amounts).toHaveLength(count);
    expect(amounts[1] - amounts[0]).toBe(step);
  });

  it("offers the app's units, in its order, and no fl oz", () => {
    expect(DISPENSE_UNITS).toEqual(["ml", "l", "cup", "qt", "gal"]);
  });

  it.each([
    [0.25, "¼"],
    [0.5, "½"],
    [0.75, "¾"],
    [1, "1"],
    [1.75, "1¾"],
    [11000, "11,000"],
    [1.3, "1.3"],
    [0.1, "0.1"],
  ])("writes %s as %s", (amount, text) => {
    expect(formatDispenseAmount(amount)).toBe(text);
  });

  it.each([
    [1, "cup", "1 cup"],
    [0.25, "cup", "¼ cup"],
    [3, "cup", "3 cups"],
    [750, "ml", "750 mL"],
    [0.75, "l", "¾ L"],
    [1.75, "qt", "1¾ qt"],
    [3, "gal", "3 gal"],
  ] as const)("writes %s %s as %s", (amount, unit, text) => {
    expect(formatDispensed(amount, unit)).toBe(text);
  });
});

describe("the presets", () => {
  async function rows(presets: Record<string, unknown>[]) {
    const { view } = await dispenseView("gal");
    view.device = { ...view.device, capabilities: { presets } };
    view.wide = true;
    await settle(view);
    return [...view.shadowRoot!.querySelectorAll(".preset-amount")].map((row) => row.textContent!.trim());
  }

  it("read as they were saved in the app", async () => {
    expect(
      await rows([
        { name: "One Cup", amount: 8, unit: "fl oz", asked_amount: 1, asked_unit: "cup" },
        { name: "Kettle", amount: 56, unit: "fl oz", asked_amount: 1.75, asked_unit: "qt" },
        { name: "Glass", amount: 8.45, unit: "fl oz", asked_amount: 250, asked_unit: "ml" },
      ]),
    ).toEqual(["1 cup", "1¾ qt", "250 mL"]);
  });

  it("read as a volume when the wording isn't known", async () => {
    expect(await rows([{ name: "Old", amount: 8.45, unit: "fl oz" }])).toEqual(["8.45 fl oz"]);
  });
});

describe("the last dispense", () => {
  const LAST = "sensor.kitchen_last_dispensed";

  async function status(state: string, attributes: Record<string, unknown>) {
    const { view } = await dispenseView("gal");
    view.device = { ...view.device, roles: { ...view.device.roles, last_dispensed: LAST } };
    view.hass = {
      ...view.hass,
      states: {
        ...view.hass.states,
        [LAST]: { entity_id: LAST, state, attributes, last_changed: "", last_updated: "" } as HassEntity,
      },
    } as HomeAssistant;
    await settle(view);
    return view.shadowRoot!.querySelector(".status")!.textContent!.trim();
  }

  it("reads in the unit it was asked for", async () => {
    expect(await status("24.0", { asked_amount: 3, asked_unit: "cup", unit_of_measurement: "fl oz" })).toBe(
      "Last dispensed 3 cups",
    );
  });

  it("reads in the account's unit when only the volume is known", async () => {
    expect(await status("8.45", { unit_of_measurement: "fl oz" })).toBe("Last dispensed 8.45 fl oz");
  });

  it("reads in the account's unit for a unit the wheels don't offer", async () => {
    expect(await status("16.0", { asked_amount: 16, asked_unit: "fl_oz", unit_of_measurement: "fl oz" })).toBe(
      "Last dispensed 16.0 fl oz",
    );
  });

  it("says nothing before the first dispense", async () => {
    expect(await status("unknown", {})).toBe("");
  });
});

describe("dispensing from the panel", () => {
  it("opens on cups on a US account, and pours them as cups", async () => {
    const { wheels, selected, start } = await dispenseView("gal");
    const [amount, unit] = wheels();
    expect(selected(amount)).toBe("3");
    expect(selected(unit)).toBe("Cups");
    expect(await start()).toEqual({ device_id: "dev_faucet", amount: 3, unit: "cup" });
  });

  it("opens on millilitres on a metric account", async () => {
    const { wheels, selected, start } = await dispenseView("L");
    const [amount, unit] = wheels();
    expect(selected(amount)).toBe("750");
    expect(selected(unit)).toBe("Milliliters");
    expect(await start()).toEqual({ device_id: "dev_faucet", amount: 750, unit: "ml" });
  });

  it("pours what the wheels show, in every unit", async () => {
    const { wheels, pick, start } = await dispenseView("gal");
    const [amount, unit] = wheels();
    for (const [name, text, sent, key] of [
      ["Milliliters", "11,000", 11000, "ml"],
      ["Liters", "1¼", 1.25, "l"],
      ["Cups", "48", 48, "cup"],
      ["Quarts", "¼", 0.25, "qt"],
      ["Gallons", "3", 3, "gal"],
    ] as const) {
      await pick(unit, name);
      await pick(amount, text);
      expect(await start()).toEqual({ device_id: "dev_faucet", amount: sent, unit: key });
    }
  });

  it("swaps the amounts with the unit, and each unit keeps its own place", async () => {
    const { wheels, options, selected, pick } = await dispenseView("gal");
    const [amount, unit] = wheels();
    await pick(amount, "2");

    await pick(unit, "Gallons");
    expect(options(amount).map((option) => option.textContent?.trim()).slice(0, 5)).toEqual(["¼", "½", "¾", "1", "1¼"]);
    expect(options(amount)).toHaveLength(12);
    expect(selected(amount)).toBe("¾");

    await pick(unit, "Cups");
    expect(selected(amount)).toBe("2");
  });
});

describe("the wheel", () => {
  async function wheel() {
    const { wheels, view, start } = await dispenseView("gal");
    const [amount] = wheels();
    const column = amount.shadowRoot!.querySelector<HTMLElement>(".column")!;
    const changes = vi.fn();
    amount.addEventListener("change", (event) => changes((event as CustomEvent<number>).detail));
    return { amount, column, changes, view, start };
  }

  const press = (column: HTMLElement, key: string) =>
    column.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));

  it("moves with the keys, and stops at its ends", async () => {
    const { amount, column } = await wheel();
    press(column, "ArrowDown");
    expect(amount.index).toBe(3);
    press(column, "ArrowUp");
    press(column, "ArrowUp");
    expect(amount.index).toBe(1);
    press(column, "PageDown");
    expect(amount.index).toBe(5);
    press(column, "End");
    expect(amount.index).toBe(47);
    press(column, "ArrowDown");
    expect(amount.index).toBe(47);
    press(column, "Home");
    expect(amount.index).toBe(0);
    press(column, "ArrowUp");
    expect(amount.index).toBe(0);
  });

  it("takes the row at the centre once a scroll has come to rest, as one change", async () => {
    const { amount, column, changes, start } = await wheel();
    Object.defineProperty(column, "clientHeight", { value: 220, configurable: true });
    for (const top of [150, 300, 440]) {
      column.scrollTop = top;
      column.dispatchEvent(new Event("scroll"));
    }
    expect(changes).not.toHaveBeenCalled();
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(changes.mock.calls).toEqual([[10]]);
    expect(amount.index).toBe(10);
    expect(await start()).toMatchObject({ amount: 11, unit: "cup" });
  });

  it("keeps its value when a scroll comes to rest while hidden", async () => {
    const { amount, column, changes } = await wheel();
    Object.defineProperty(column, "clientHeight", { value: 0, configurable: true });
    column.scrollTop = 0;
    column.dispatchEvent(new Event("scroll"));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(changes).not.toHaveBeenCalled();
    expect(amount.index).toBe(2);
  });

  it("drops a scroll still settling when it leaves the page", async () => {
    const { amount, column, changes } = await wheel();
    Object.defineProperty(column, "clientHeight", { value: 220, configurable: true });
    column.scrollTop = 440;
    column.dispatchEvent(new Event("scroll"));
    amount.remove();
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(changes).not.toHaveBeenCalled();
  });

  it("doesn't take a fresh list with the same rows for a change", async () => {
    const { amount, column } = await wheel();
    const scrollTo = vi.spyOn(column, "scrollTo");
    amount.options = [...amount.options];
    await amount.updateComplete;
    expect(amount.isUpdatePending).toBe(false);
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
