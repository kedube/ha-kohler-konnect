import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KkpSlider } from "../src/components/slider";
import type { PanelContext } from "../src/context";
import { setLanguage } from "../src/i18n";
import type { DeviceDescriptor, HassEntity, HomeAssistant } from "../src/types";
import type { KkpGeneric } from "../src/views/generic";
import "../src/views/generic";

setLanguage("en");

function entity(entity_id: string, state: string, attributes: Record<string, unknown>): HassEntity {
  return { entity_id, state, attributes, last_changed: "", last_updated: "" };
}

const SHOWER = "select.guest_shower_shower";
const DEFAULT_TEMPERATURE = "number.guest_shower_default_temperature";

/** An EvoCycle in the generic view, with two controls an owner has turned on. */
async function evocycle() {
  const calls: [string, string, Record<string, unknown>][] = [];
  const api = {
    service: vi.fn(async (domain: string, service: string, data: Record<string, unknown>) => {
      calls.push([domain, service, data]);
    }),
    queue: () => ({ enqueue: (_key: string, run: () => Promise<unknown>) => run() }),
  };
  const view = document.createElement("kkp-generic") as KkpGeneric;
  view.hass = {
    states: {
      [SHOWER]: entity(SHOWER, "off", {
        friendly_name: "Guest Shower Shower",
        options: ["off", "standard", "cycle"],
        untested: true,
      }),
      [DEFAULT_TEMPERATURE]: entity(DEFAULT_TEMPERATURE, "100", {
        friendly_name: "Guest Shower Default temperature",
        min: 86,
        max: 118,
        step: 1,
        unit_of_measurement: "°F",
        device_class: "temperature",
        untested: true,
      }),
    },
  } as unknown as HomeAssistant;
  view.device = {
    id: "dev_evocycle",
    entry_id: "entry",
    kind: "evocycle",
    name: "Guest Shower",
    area: null,
    model: "EvoCycle",
    model_name: null,
    units: { temperature: "°F", volume: "gal" },
    zones: [],
    capabilities: {},
    roles: { evo_shower: SHOWER, default_temperature: DEFAULT_TEMPERATURE },
    disabled_roles: {},
    untested: ["evo_shower", "default_temperature"],
  } as unknown as DeviceDescriptor;
  view.ctx = { api, admin: true, wide: false, navigate: () => {}, toast: vi.fn(), confirm: async () => true } as unknown as PanelContext;
  document.body.append(view);
  await view.updateComplete;
  return { view, calls, root: view.shadowRoot! };
}

async function settle(view: KkpGeneric) {
  await view.updateComplete;
  await new Promise((resolve) => setTimeout(resolve));
  await view.updateComplete;
}

beforeEach(() => document.body.replaceChildren());

describe("the generic view's controls", () => {
  it("opens a select to its choices and picks one", async () => {
    const { view, calls, root } = await evocycle();
    root.querySelector<HTMLElement>('[data-key="evo_shower"]')!.click();
    await view.updateComplete;
    const radios = [...root.querySelectorAll<HTMLElement>('[role="radio"]')];
    expect(radios.map((radio) => radio.textContent?.trim())).toEqual(["off", "standard", "cycle"]);
    expect(radios.map((radio) => radio.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);

    radios[1].click();
    await settle(view);
    expect(calls).toEqual([["select", "select_option", { entity_id: SHOWER, option: "standard" }]]);
    expect(root.querySelector('[role="radiogroup"]')).toBeNull();
  });

  it("sends a number only on Save", async () => {
    const { view, calls, root } = await evocycle();
    root.querySelector<HTMLElement>('[data-key="default_temperature"]')!.click();
    await view.updateComplete;
    const save = () => root.querySelector<HTMLButtonElement>(".editor-actions .filled")!;
    expect(save().disabled).toBe(true);

    root.querySelector("kkp-slider")!.dispatchEvent(new CustomEvent("change", { detail: 104 }));
    await view.updateComplete;
    expect(calls).toEqual([]);
    save().click();
    await settle(view);
    expect(calls).toEqual([["number", "set_value", { entity_id: DEFAULT_TEMPERATURE, value: 104 }]]);
    expect(root.querySelector("kkp-slider")).toBeNull();
  });
});

describe("a light in the generic view", () => {
  const LIGHT = "light.numi_ambient_light";

  async function numi() {
    const calls: [string, string, Record<string, unknown>][] = [];
    const api = {
      service: async (domain: string, service: string, data: Record<string, unknown>) => {
        calls.push([domain, service, data]);
      },
      queue: () => ({ enqueue: (_key: string, run: () => Promise<unknown>) => run() }),
    };
    const view = document.createElement("kkp-generic") as KkpGeneric;
    view.hass = {
      states: {
        [LIGHT]: entity(LIGHT, "on", {
          friendly_name: "Numi Ambient light",
          supported_color_modes: ["brightness"],
          brightness: 153,
          effect_list: ["sunrise", "deep_ocean"],
          effect: "deep_ocean",
        }),
      },
      formatEntityAttributeValue: (_entity: HassEntity, _attribute: string, value: unknown) =>
        value === "deep_ocean" ? "Ocean deep" : String(value),
    } as unknown as HomeAssistant;
    view.device = {
      id: "dev_numi",
      entry_id: "entry",
      kind: "toilet",
      name: "Numi",
      area: null,
      model: "Numi 2.0",
      model_name: null,
      units: { temperature: "°F", volume: "gal" },
      zones: [],
      capabilities: {},
      roles: { ambient_light: LIGHT },
      disabled_roles: {},
      untested: ["ambient_light"],
    } as unknown as DeviceDescriptor;
    view.ctx = { api, admin: true, wide: false, navigate: () => {}, toast: vi.fn(), confirm: async () => true } as unknown as PanelContext;
    document.body.append(view);
    await view.updateComplete;
    return { view, calls, root: view.shadowRoot! };
  }

  it("shows its brightness and its effect by name", async () => {
    const { root } = await numi();
    const values = [...root.querySelectorAll(".row .value")].map((value) => value.textContent?.trim());
    expect(values).toEqual(["60%", "Ocean deep"]);
  });

  it("sets a brightness on Save and an effect when picked", async () => {
    const { view, calls, root } = await numi();
    root.querySelector<HTMLElement>('[data-key="ambient_light:brightness"]')!.click();
    await view.updateComplete;
    root.querySelector("kkp-slider")!.dispatchEvent(new CustomEvent("change", { detail: 30 }));
    await view.updateComplete;
    root.querySelector<HTMLElement>(".editor-actions .filled")!.click();
    await settle(view);

    root.querySelector<HTMLElement>('[data-key="ambient_light:effect"]')!.click();
    await view.updateComplete;
    root.querySelector<HTMLElement>('[role="radio"]')!.click();
    await settle(view);
    expect(calls).toEqual([
      ["light", "turn_on", { entity_id: LIGHT, brightness_pct: 30 }],
      ["light", "turn_on", { entity_id: LIGHT, effect: "sunrise" }],
    ]);
  });
});

describe("the slider's value", () => {
  async function shown(attributes: Partial<KkpSlider>) {
    const slider = document.createElement("kkp-slider") as KkpSlider;
    Object.assign(slider, { label: "Amount", min: 0, max: 1000, step: 1, ...attributes });
    document.body.append(slider);
    await slider.updateComplete;
    return slider.shadowRoot!.querySelector(".value")!.textContent?.trim();
  }

  it("shows a number in its own unit, and only a percentage as one", async () => {
    expect(await shown({ variant: "flow", value: 250, unit: "mL" })).toBe("250 mL");
    expect(await shown({ variant: "flow", value: 30, unit: "min" })).toBe("30 min");
    expect(await shown({ variant: "flow", value: 80, unit: "" })).toBe("80%");
    expect(await shown({ variant: "flow", value: 50, unit: "%" })).toBe("50%");
  });
});

describe("an ozone clean in the generic view", () => {
  const RAPID = "button.guest_shower_start_rapid_clean";
  const HIDDEN = "button.guest_shower_stop_clean";

  async function cleans(answer: boolean) {
    const calls: [string, string, Record<string, unknown>][] = [];
    const confirm = vi.fn(async () => answer);
    const api = {
      service: async (domain: string, service: string, data: Record<string, unknown>) => {
        calls.push([domain, service, data]);
      },
      queue: () => ({ enqueue: (_key: string, run: () => Promise<unknown>) => run() }),
    };
    const view = document.createElement("kkp-generic") as KkpGeneric;
    view.hass = {
      states: {
        [RAPID]: entity(RAPID, "unknown", { friendly_name: "Guest Shower Start rapid clean" }),
        [HIDDEN]: entity(HIDDEN, "unknown", { friendly_name: "Guest Shower Stop clean" }),
      },
      entities: { [HIDDEN]: { hidden: true } },
    } as unknown as HomeAssistant;
    view.device = {
      id: "dev_evocycle",
      entry_id: "entry",
      kind: "evocycle",
      name: "Guest Shower",
      area: null,
      model: "EvoCycle",
      model_name: null,
      units: { temperature: "°F", volume: "gal" },
      zones: [],
      capabilities: {},
      roles: { start_rapid_clean: RAPID, stop_clean: HIDDEN },
      disabled_roles: {},
      untested: ["start_rapid_clean", "stop_clean"],
    } as unknown as DeviceDescriptor;
    view.ctx = { api, admin: true, wide: false, navigate: () => {}, toast: vi.fn(), confirm } as unknown as PanelContext;
    document.body.append(view);
    await view.updateComplete;
    return { view, calls, confirm, root: view.shadowRoot! };
  }

  it("asks first, as the app does, and starts only when told to", async () => {
    for (const answer of [false, true]) {
      document.body.replaceChildren();
      const { view, calls, confirm, root } = await cleans(answer);
      // The control someone hid in Home Assistant stays out of the panel.
      const pills = [...root.querySelectorAll<HTMLButtonElement>("button.pill")];
      expect(pills).toHaveLength(1);
      pills[0].click();
      await settle(view);
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(calls).toEqual(answer ? [["button", "press", { entity_id: RAPID }]] : []);
    }
  });
});

describe("the Smart Fan's settings", () => {
  it("hides what the app hides and shows the saved schedule", async () => {
    await import("../src/views/settings");
    const view = document.createElement("kkp-settings") as HTMLElement & Record<string, unknown>;
    const id = (key: string) => `${key.split(":")[0]}.bath_fan_${key.split(":")[1]}`;
    const run = id("number:default_fan_run_time");
    const sensitivity = id("select:humidity_sensitivity");
    const schedule = id("switch:fan_schedule");
    const speed = id("select:default_fan_speed");
    view.hass = {
      states: {
        [run]: entity(run, "unavailable", { friendly_name: "Bath Fan Default fan run time" }),
        [sensitivity]: entity(sensitivity, "low", {
          friendly_name: "Bath Fan Humidity sensitivity",
          options: ["low", "medium", "high"],
        }),
        [schedule]: entity(schedule, "on", {
          friendly_name: "Bath Fan Fan schedule",
          start: "19:05",
          end: "06:30",
          repeat: "custom",
          days: ["mon", "wed", "fri"],
          speed: "low",
        }),
        [speed]: entity(speed, "medium", {
          friendly_name: "Bath Fan Default fan speed",
          options: ["low", "medium", "high"],
        }),
      },
      formatEntityState: (_entity: HassEntity, state?: string) => (state === "low" ? "Low" : String(state)),
    } as unknown as HomeAssistant;
    view.device = {
      id: "dev_fan",
      entry_id: "entry",
      kind: "smart_fan",
      name: "Bath Fan",
      area: null,
      model: "Smart Fan",
      model_name: null,
      units: { temperature: "°F", volume: "gal" },
      zones: [],
      capabilities: {},
      roles: {
        default_fan_run_time: run,
        humidity_sensitivity: sensitivity,
        fan_schedule: schedule,
        default_fan_speed: speed,
      },
      disabled_roles: {},
      untested: [],
    } as unknown as DeviceDescriptor;
    view.ctx = { api: {}, admin: true, wide: false, navigate: () => {}, toast: vi.fn(), confirm: async () => true } as unknown as PanelContext;
    document.body.append(view);
    await (view as unknown as { updateComplete: Promise<unknown> }).updateComplete;
    const root = view.shadowRoot!;

    // The run time waits on the timer, which is off: not shown, as in the app.
    expect(root.querySelector('[data-key="default_fan_run_time"]')).toBeNull();
    expect(root.querySelector('[data-key="humidity_sensitivity"]')).not.toBeNull();
    const text = root.textContent ?? "";
    expect(text).toContain("Mon, Wed, Fri");
    expect(text).toContain("Low");
  });
});
