import { describe, expect, it } from "vitest";
import type { DeviceDescriptor, DeviceLive, HomeAssistant } from "../src/types";
import {
  canStart,
  clampTemperature,
  customShowerData,
  defaultStage,
  setStagedTemperature,
  toggleStagedOutlet,
} from "../src/util/staging";

const device = (zones: number[]): DeviceDescriptor => ({
  id: "dev1",
  entry_id: "e",
  kind: "valve",
  name: "Shower",
  area: null,
  model: "K-28212",
  model_name: null,
  units: { temperature: "°F", volume: "gal" },
  zones: zones.map((zone) => ({
    zone,
    outlets: [1, 2, 3].map((position) => ({ position, code: 11, name: `O${position}`, variant: null })),
  })),
  capabilities: {},
  roles: { "temperature:1": "number.t1", "temperature:2": "number.t2" },
  disabled_roles: {},
  untested: [],
  untested_device: false,
});

const fahrenheit = { unit_of_measurement: "°F" };
const hass = {
  states: {
    "number.t1": { state: "100", attributes: fahrenheit },
    "number.t2": { state: "98", attributes: fahrenheit },
  },
} as unknown as HomeAssistant;

const live: DeviceLive = {
  device_id: "dev1",
  stream_connected: true,
  cloud_connected: true,
  last_update: null,
  default_shower: { zones: [{ zone: 1, outlets: [1], temperature: 106 }], refused_because: null },
  staging_bounds: [
    { zone: 1, min: 59, max: 113 },
    { zone: 2, min: 59, max: 113 },
  ],
};

describe("staging a shower", () => {
  it("starts from the valve's default shower", () => {
    const stage = defaultStage(device([1, 2]), live, hass);
    expect(stage).toEqual([
      { zone: 1, outlets: [1], temperature: 106 },
      { zone: 2, outlets: [], temperature: 98 },
    ]);
  });

  it("never borrows a temperature Home Assistant shows in another unit", () => {
    const metric = {
      states: { "number.t1": { state: "37", attributes: { unit_of_measurement: "°C" } } },
    } as unknown as HomeAssistant;
    const stage = defaultStage(device([1]), { ...live, default_shower: undefined }, metric);
    expect(stage[0].temperature).toBeNull();
  });

  it("toggles outlets per zone, kept in order", () => {
    let stage = defaultStage(device([1, 2]), live, hass);
    stage = toggleStagedOutlet(stage, 1, 3);
    stage = toggleStagedOutlet(stage, 2, 2);
    stage = toggleStagedOutlet(stage, 1, 1);
    expect(stage.map((z) => z.outlets)).toEqual([[3], [2]]);
  });

  it("clamps a staged temperature to the scald limit and never offers Cold", () => {
    expect(clampTemperature(120, { min: 59, max: 113 })).toBe(113);
    expect(clampTemperature(58, { min: 59, max: 113 })).toBe(59);
    const stage = setStagedTemperature(defaultStage(device([1]), live, hass), 1, 130, live);
    expect(stage[0].temperature).toBe(113);
  });

  it("builds one custom_shower call with the device and every staged outlet", () => {
    let stage = defaultStage(device([1, 2]), live, hass);
    stage = toggleStagedOutlet(stage, 2, 3);
    expect(customShowerData("dev1", stage, { zone1: 80, zone2: 60 })).toEqual({
      device_id: "dev1",
      zone1_temperature: 106,
      zone1_outlet_1: true,
      zone2_temperature: 98,
      zone2_outlet_3: true,
      flow: 80,
      zone2_flow: 60,
    });
  });

  it("leaves flow out when no Flow control is enabled", () => {
    const data = customShowerData("dev1", defaultStage(device([1]), live, hass), { zone1: null });
    expect(data).not.toHaveProperty("flow");
    expect(data).not.toHaveProperty("zone2_flow");
  });

  it("needs an outlet and zone 1's temperature before Start", () => {
    const empty = toggleStagedOutlet(defaultStage(device([1]), live, hass), 1, 1);
    expect(canStart(empty)).toBe(false);
    expect(canStart(defaultStage(device([1]), live, hass))).toBe(true);
    expect(canStart([{ zone: 1, outlets: [1], temperature: null }])).toBe(false);
  });
});
