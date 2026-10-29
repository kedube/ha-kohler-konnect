// Sample installations for the design harness. Entity ids, states and attributes follow
// what the integration's entities publish, so the panel meets the same data here as in
// Home Assistant.

import type {
  DeviceDescriptor,
  DeviceDetails,
  EntryInfo,
  HassEntity,
  QuickAmount,
} from "../src/types";

export interface SimValve {
  kind: "valve";
  descriptor: DeviceDescriptor;
  slug: string;
  defaultOutlets: { zone: number; outlets: number[] }[];
  defaultTemperature: number;
  scald: number;
}

export interface SimFaucet {
  kind: "faucet";
  descriptor: DeviceDescriptor;
  slug: string;
  metric: boolean;
}

export interface SimController {
  kind: "controller";
  descriptor: DeviceDescriptor;
  slug: string;
}

export interface SimSmartFan {
  kind: "smart_fan";
  descriptor: DeviceDescriptor;
  slug: string;
}

/** A product the panel shows in its generic view. */
export interface SimProduct {
  kind: "toilet" | "evocycle" | "ice_bath";
  descriptor: DeviceDescriptor;
  slug: string;
}

export type SimDevice = SimValve | SimFaucet | SimController | SimSmartFan | SimProduct;

export interface Scenario {
  key: string;
  label: string;
  user: string;
  entries: EntryInfo[];
  devices: SimDevice[];
  states: Record<string, HassEntity>;
  details: Record<string, DeviceDetails>;
  options: Record<string, Record<string, unknown>>;
}

const NOW = new Date().toISOString();

function entity(entity_id: string, state: string, attributes: Record<string, unknown> = {}): HassEntity {
  return { entity_id, state, attributes, last_changed: NOW, last_updated: NOW };
}

function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

function seeded(seed: string) {
  let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

/** A day's water for a device, the same every time it's asked for. */
export function dailyUsage(deviceId: string, date: Date, faucet: boolean): { volume: number; minutes: number; uses: number } {
  const key = `${deviceId}:${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
  const random = seeded(key);
  const skip = random() < (faucet ? 0.2 : 0.38);
  if (skip) return { volume: 0, minutes: 0, uses: 0 };
  const uses = faucet ? 1 + Math.floor(random() * 9) : 1 + Math.floor(random() * 2);
  const minutes = faucet ? uses * (0.3 + random() * 0.8) : uses * (6 + random() * 9);
  const volume = faucet ? minutes * 0.42 : minutes * (1.1 + random() * 0.6);
  return { volume: Math.round(volume * 100) / 100, minutes: Math.round(minutes * 10) / 10, uses };
}

function waterSensors(slug: string, deviceId: string, unit: string, faucet: boolean, states: Record<string, HassEntity>, roles: Record<string, string>) {
  const today = new Date();
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() - (6 - i));
    return d;
  });
  const perDay: Record<string, number> = {};
  let week = 0;
  for (const d of days) {
    const v = dailyUsage(deviceId, d, faucet).volume;
    perDay[d.toISOString().slice(0, 10)] = v;
    week += v;
  }
  let month = 0;
  for (let day = 1; day <= today.getDate(); day++) {
    month += dailyUsage(deviceId, new Date(today.getFullYear(), today.getMonth(), day), faucet).volume;
  }
  const todayValue = perDay[today.toISOString().slice(0, 10)] ?? 0;
  const add = (role: string, suffix: string, state: number, attributes: Record<string, unknown> = {}) => {
    const id = `sensor.${slug}_${suffix}`;
    states[id] = entity(id, state.toFixed(2), { unit_of_measurement: unit, state_class: "total", ...attributes });
    roles[role] = id;
  };
  add("water_today", "water_used_today", todayValue);
  add("water_week", "water_used_this_week", week, { per_day: perDay });
  add("water_month", "water_used_this_month", month);
  add("water_year", "water_used_this_year", month * 7.4);
}

interface ValveSpec {
  id: string;
  name: string;
  area: string;
  model: string;
  modelName: string;
  unit: "°F" | "°C";
  zones: { zone: number; outlets: { code: number; name: string; variant?: string }[] }[];
  favorites: string[];
  experiences: string[];
  warmup: string;
  scald: number;
  defaultTemperature: number;
  duration: number;
  flow: boolean;
  autoRestore?: boolean;
  defaultOutlets: { zone: number; outlets: number[] }[];
}

function buildValve(spec: ValveSpec, states: Record<string, HassEntity>): SimValve {
  const slug = slugify(`anthem valve ${spec.name}`);
  const roles: Record<string, string> = {};
  const add = (role: string, id: string, state: string, attributes: Record<string, unknown> = {}) => {
    states[id] = entity(id, state, { friendly_name: id, ...attributes });
    roles[role] = id;
  };
  const multi = spec.zones.length > 1;
  const cold = spec.unit === "°F" ? 58 : 14;
  add("shower", `switch.${slug}_shower_on`, "off", { paused: false });
  for (const z of spec.zones) {
    const suffix = multi ? `_${z.zone}` : "";
    z.outlets.forEach((o, i) => {
      add(`outlet:${z.zone}:${i + 1}`, `switch.${slug}_${slugify(o.name)}${suffix}`, "off", {
        outlet_type: o.code,
        outlet_type_name: o.name,
        outlet_variant: o.variant,
      });
    });
    add(`temperature:${z.zone}`, `number.${slug}_temperature${suffix}`, String(spec.defaultTemperature), {
      min: cold,
      max: spec.scald,
      step: 1,
      unit_of_measurement: spec.unit,
      cold: false,
    });
    if (spec.flow) {
      add(`flow:${z.zone}`, `number.${slug}_flow${suffix}`, "100", {
        min: 25,
        max: 100,
        step: 1,
        unit_of_measurement: "%",
        flow_control_available: true,
      });
    }
  }
  add("favorite", `select.${slug}_favorite`, "Off", { options: ["Off", ...spec.favorites] });
  add("experience", `select.${slug}_experience`, "Off", { options: ["Off", ...spec.experiences] });
  add("warmup", `select.${slug}_warmup`, spec.warmup, { options: ["Off", "All Outlets", "Started Outlets"] });
  add("max_temperature", `number.${slug}_max_temperature`, String(spec.scald), {
    min: spec.unit === "°F" ? 92 : 33,
    max: spec.unit === "°F" ? 118 : 47,
    step: 1,
    unit_of_measurement: spec.unit,
  });
  add("default_temperature", `number.${slug}_default_temperature`, String(spec.defaultTemperature), {
    min: spec.unit === "°F" ? 59 : 15,
    max: spec.scald,
    step: 1,
    unit_of_measurement: spec.unit,
  });
  add("max_duration", `select.${slug}_max_shower_duration`, `${spec.duration} minutes`, {
    options: [15, 20, 25, 30, 45, 60].map((m) => `${m} minutes`),
  });
  add("status", `sensor.${slug}_system_status`, "Idle", { seconds_remaining: null, flowing_for_seconds: null });
  add("at_temperature", `binary_sensor.${slug}_at_temperature`, "off");
  add("problem", `binary_sensor.${slug}_problem`, "off", { active_faults: [] });
  add("cloud", `binary_sensor.${slug}_cloud_connection`, "on");
  add("firmware", `update.${slug}_firmware_status`, "off", { installed_version: "2.11", latest_version: "2.11" });
  add("report_log", `switch.${slug}_report_log`, "off", { file: null });
  add("clear_errors", `button.${slug}_clear_error_log`, "unknown");
  if (spec.autoRestore) add("warmup_auto_restore", `switch.${slug}_warmup_auto_restore`, "off");
  waterSensors(slug, spec.id, spec.unit === "°F" ? "gal" : "L", false, states, roles);

  const descriptor: DeviceDescriptor = {
    id: spec.id,
    entry_id: "entry_main",
    kind: "valve",
    name: spec.name,
    area: spec.area,
    model: spec.model,
    model_name: spec.modelName,
    units: { temperature: spec.unit, volume: spec.unit === "°F" ? "gal" : "L" },
    zones: spec.zones.map((z) => ({
      zone: z.zone,
      outlets: z.outlets.map((o, i) => ({ position: i + 1, code: o.code, name: o.name, variant: o.variant ?? null })),
    })),
    capabilities: {
      usage: true,
      flow_control: spec.flow,
      ...(spec.autoRestore ? { warmup_auto_restore: false } : {}),
    },
    roles,
    disabled_roles: { restart: `button.${slug}_restart`, hex: `sensor.${slug}_hex` },
    untested: [],
  };
  return {
    kind: "valve",
    descriptor,
    slug,
    defaultOutlets: spec.defaultOutlets,
    defaultTemperature: spec.defaultTemperature,
    scald: spec.scald,
  };
}

const US_QUICK: QuickAmount[] = [
  { key: "quarter_cup", amount: 2, unit: "fl oz", label: "¼ cup" },
  { key: "half_cup", amount: 4, unit: "fl oz", label: "½ cup" },
  { key: "one_cup", amount: 8, unit: "fl oz", label: "1 cup" },
  { key: "two_cups", amount: 16, unit: "fl oz", label: "2 cups" },
  { key: "one_quart", amount: 32, unit: "fl oz", label: "1 quart" },
  { key: "half_gallon", amount: 64, unit: "fl oz", label: "½ gallon" },
  { key: "one_gallon", amount: 128, unit: "fl oz", label: "1 gallon" },
];

const METRIC_QUICK: QuickAmount[] = [50, 250, 500, 750, 1000, 2000, 3000].map((ml) => ({
  key: `${ml}ml`,
  amount: ml,
  unit: "mL",
  label: ml >= 1000 ? `${ml / 1000} L` : `${ml} mL`,
}));

function buildFaucet(
  spec: { id: string; name: string; area: string; metric: boolean; presets: { name: string; amount: number }[] },
  states: Record<string, HassEntity>,
): SimFaucet {
  const slug = slugify(spec.name);
  const roles: Record<string, string> = {};
  const add = (role: string, id: string, state: string, attributes: Record<string, unknown> = {}) => {
    states[id] = entity(id, state, { friendly_name: id, ...attributes });
    roles[role] = id;
  };
  const unit = spec.metric ? "mL" : "fl oz";
  add("water", `switch.${slug}_water`, "off");
  add("status", `sensor.${slug}_status`, "off");
  add("dispensing", `binary_sensor.${slug}_dispensing`, "off");
  add("leak", `binary_sensor.${slug}_leak`, "off", { events: 1, uncleared_events: 0 });
  add("clear_leak", `button.${slug}_clear_leak_alert`, "unknown");
  add("last_dispensed", `sensor.${slug}_last_dispensed`, spec.metric ? "250" : "2.0", { unit_of_measurement: unit });
  add("preset", `select.${slug}_preset`, spec.presets[0]?.name ?? "", { options: spec.presets.map((p) => p.name) });
  add("dispense_amount", `number.${slug}_dispense_amount`, spec.metric ? "500" : "16", {
    min: spec.metric ? 10 : 0.5,
    max: spec.metric ? 11360 : 384,
    step: spec.metric ? 10 : 0.5,
    unit_of_measurement: unit,
  });
  add("handle", `sensor.${slug}_handle`, "open");
  add("cloud", `binary_sensor.${slug}_cloud_connection`, "on");
  add("firmware", `update.${slug}_firmware_status`, "off", { installed_version: "16.0", latest_version: "16.0", in_progress: false });
  waterSensors(slug, spec.id, spec.metric ? "L" : "gal", true, states, roles);
  return {
    kind: "faucet",
    slug,
    metric: spec.metric,
    descriptor: {
      id: spec.id,
      entry_id: "entry_main",
      kind: "faucet",
      name: spec.name,
      area: spec.area,
      model: "SEN",
      model_name: "Sensate",
      units: { temperature: spec.metric ? "°C" : "°F", volume: spec.metric ? "L" : "gal", dispense: unit },
      zones: [],
      capabilities: { usage: true, presets: spec.presets.map((p) => ({ ...p, unit })) },
      roles,
      disabled_roles: {},
      untested: [],
      quick_amounts: spec.metric ? METRIC_QUICK : US_QUICK,
    },
  };
}

function buildController(
  spec: { id: string; name: string; area: string; favorites: string[]; experiences: string[]; zones: number[] },
  states: Record<string, HassEntity>,
): SimController {
  const slug = slugify(`anthem plus ${spec.name}`);
  const roles: Record<string, string> = {};
  const add = (role: string, id: string, state: string, attributes: Record<string, unknown> = {}) => {
    states[id] = entity(id, state, { friendly_name: id, ...attributes });
    roles[role] = id;
  };
  add("shower", `switch.${slug}_shower`, "off");
  add("system", `switch.${slug}_system`, "on");
  add("favorite", `select.${slug}_favorite`, "Off", { options: ["Off", ...spec.favorites] });
  add("experience", `select.${slug}_experience`, "Off", { options: ["Off", ...spec.experiences] });
  add("status", `sensor.${slug}_system_status`, "Idle");
  for (const z of spec.zones) {
    add(`temperature:${z}`, `sensor.${slug}_temperature_${z}`, "104", { unit_of_measurement: "°F" });
    for (let o = 1; o <= 3; o++) add(`outlet:${z}:${o}`, `binary_sensor.${slug}_zone_${z}_outlet_${o}`, "off");
  }
  add("steam", `switch.${slug}_steam`, "off", { default_temperature: 110, default_time: 15 });
  add("steam_state", `binary_sensor.${slug}_steam`, "off", { temperature: 110 });
  add("music", `binary_sensor.${slug}_music`, "off");
  add("light", `binary_sensor.${slug}_light`, "on");
  add("problem", `binary_sensor.${slug}_problem`, "off");
  add("max_duration", `sensor.${slug}_max_shower_duration`, "30");
  add("firmware", `update.${slug}_firmware_status`, "on", { installed_version: "2.88", latest_version: "2.91" });
  return {
    kind: "controller",
    slug,
    descriptor: {
      id: spec.id,
      entry_id: "entry_main",
      kind: "controller",
      name: spec.name,
      area: spec.area,
      model: "K-27756",
      model_name: "Anthem+ System Controller",
      units: { temperature: "°F", volume: "gal" },
      zones: spec.zones.map((zone) => ({
        zone,
        outlets: [1, 2, 3].map((position) => ({ position, code: null, name: `Outlet ${position}`, variant: null })),
      })),
      capabilities: { steam: true, web_url: "http://192.0.2.40/" },
      roles,
      disabled_roles: {},
      untested: [],
    },
  };
}

/** A Smart Fan with its light, its controls enabled to show the whole screen. */
function buildSmartFan(
  spec: { id: string; name: string; area: string; fanFavorites: string[]; lightFavorites: string[] },
  states: Record<string, HassEntity>,
): SimSmartFan {
  const slug = slugify(spec.name);
  const roles: Record<string, string> = {};
  const add = (role: string, id: string, state: string, attributes: Record<string, unknown> = {}) => {
    states[id] = entity(id, state, { friendly_name: id, ...attributes });
    roles[role] = id;
  };
  const untested = { untested: true };
  add("fan_state", `sensor.${slug}_fan`, "off", { options: ["off", "low", "medium", "high", "on"], favorite: null });
  add("light_state", `binary_sensor.${slug}_light`, "off", { brightness: null, color: null, favorite: null });
  add("connected", `binary_sensor.${slug}_cloud_connection`, "on");
  add("gateway_firmware", `sensor.${slug}_gateway_firmware`, "1.4");
  add("controller_firmware", `sensor.${slug}_controller_firmware`, "2.0");
  add("firmware_status", `update.${slug}_firmware_status`, "off", { installed_version: "3.1", latest_version: "3.1" });
  add("fan", `fan.${slug}_fan`, "off", { ...untested, preset_modes: spec.fanFavorites, preset_mode: null });
  add("light", `light.${slug}_light`, "off", { ...untested, effect_list: spec.lightFavorites, effect: null });
  add("stop_all", `button.${slug}_stop_all`, "unknown", untested);
  add("default_fan_speed", `select.${slug}_default_fan_speed`, "medium", { ...untested, options: ["low", "medium", "high"] });
  add("default_fan_timer", `switch.${slug}_default_fan_timer`, "on", untested);
  add("default_fan_run_time", `number.${slug}_default_fan_run_time`, "30", {
    ...untested, min: 10, max: 60, step: 10, unit_of_measurement: "min",
  });
  add("default_light_color", `select.${slug}_default_light_color`, "warmwhite", {
    ...untested, options: ["warm", "warmwhite", "cool", "coolwhite", "daylight"],
  });
  add("default_light_brightness", `number.${slug}_default_light_brightness`, "50", {
    ...untested, min: 10, max: 100, step: 10, unit_of_measurement: "%",
  });
  add("circadian_lighting", `switch.${slug}_circadian_lighting`, "off", untested);
  add("audible_feedback", `switch.${slug}_audible_feedback`, "on", untested);
  add("humidity_sensing", `switch.${slug}_humidity_sensing`, "on", untested);
  add("humidity_sensitivity", `select.${slug}_humidity_sensitivity`, "medium", { ...untested, options: ["low", "medium", "high"] });
  add("fan_schedule", `switch.${slug}_fan_schedule`, "unavailable", untested);
  return {
    kind: "smart_fan",
    slug,
    descriptor: {
      id: spec.id,
      entry_id: "entry_main",
      kind: "smart_fan",
      name: spec.name,
      area: spec.area,
      model: "Smart Fan",
      model_name: null,
      units: { temperature: "°F", volume: "gal" },
      zones: [],
      capabilities: {},
      roles,
      disabled_roles: {},
      untested: Object.keys(roles).filter((role) => states[roles[role]].attributes.untested),
    },
  };
}

type SimEntity = [role: string, suffix: string, state: string, attributes?: Record<string, unknown>];

/**
 * A product read whole by the integration. Its untested controls are on, as they ship, and
 * flagged; one marked `disabled` is as an owner leaves it after turning it off.
 */
function buildProduct(
  spec: { id: string; kind: SimProduct["kind"]; name: string; area: string; model: string },
  entities: SimEntity[],
  states: Record<string, HassEntity>,
): SimProduct {
  const slug = slugify(spec.name);
  const roles: Record<string, string> = {};
  const disabled: Record<string, string> = {};
  const untested: string[] = [];
  for (const [role, suffix, state, { disabled: off, ...attributes } = {}] of entities) {
    const id = suffix.replace(".", `.${slug}_`);
    if (attributes.untested) untested.push(role);
    if (off) {
      disabled[role] = id;
      continue;
    }
    const label = suffix.split(".")[1].replace(/_/g, " ");
    states[id] = entity(id, state, {
      friendly_name: `${spec.name} ${label.charAt(0).toUpperCase()}${label.slice(1)}`,
      ...attributes,
    });
    roles[role] = id;
  }
  return {
    kind: spec.kind,
    slug,
    descriptor: {
      id: spec.id,
      entry_id: "entry_main",
      kind: spec.kind,
      name: spec.name,
      area: spec.area,
      model: spec.model,
      model_name: null,
      units: { temperature: "°F", volume: "gal" },
      zones: [],
      capabilities: {},
      roles,
      disabled_roles: disabled,
      untested,
    },
  };
}

function productDetails(device: SimProduct): DeviceDetails {
  return {
    device_id: device.descriptor.id,
    model: device.descriptor.model,
    model_name: null,
    serial: null,
    install_date: null,
    registered: null,
    hardware: null,
    firmware: [],
    parts: [],
    faults: [],
    leak_history: [],
    web_url: null,
    diagnostics_path: `/api/diagnostics/config_entry/entry_main/device/${device.descriptor.id}`,
  };
}

function smartFanDetails(device: SimSmartFan): DeviceDetails {
  return {
    device_id: device.descriptor.id,
    model: "Smart Fan",
    model_name: null,
    serial: "0000ab12",
    install_date: "2025-02-03",
    registered: null,
    hardware: null,
    firmware: [
      { key: "gateway", version: "1.4" },
      { key: "controller", version: "2.0" },
    ],
    parts: [],
    faults: [],
    leak_history: [],
    web_url: null,
    diagnostics_path: `/api/diagnostics/config_entry/entry_main/device/${device.descriptor.id}`,
  };
}

function valveDetails(device: SimValve, serial: string, installed: string): DeviceDetails {
  const two = device.descriptor.zones.length > 1;
  return {
    device_id: device.descriptor.id,
    model: device.descriptor.model,
    model_name: device.descriptor.model_name,
    serial,
    install_date: installed,
    registered: `${installed}T14:22:00Z`,
    hardware: null,
    firmware: [
      { key: "interface", version: "2.11" },
      { key: "valve", version: "1.28" },
      ...(two ? [{ key: "second_valve", version: "1.28" }] : []),
      { key: "gateway", version: "3.4.2" },
    ],
    parts: [
      { kind: "gateway", name: "Gateway", model: "GCS-GW1", serial: `GW-${serial.slice(-5)}`, firmware: "3.4.2", status: "Connected", install_date: installed },
      { kind: "valve", name: "Primary valve", model: device.descriptor.model, serial: `V1-${serial.slice(-5)}`, firmware: "1.28", status: "Connected", install_date: null },
      ...(two
        ? [{ kind: "valve", name: "Secondary valve", model: device.descriptor.model, serial: `V2-${serial.slice(-5)}`, firmware: "1.28", status: "Connected", install_date: null }]
        : []),
      { kind: "interface", name: "Anthem interface", model: "K-28214", serial: null, firmware: "2.11", status: "Connected", install_date: null },
    ],
    faults: [],
    leak_history: [],
    web_url: null,
    diagnostics_path: `/api/diagnostics/config_entry/entry_main/device/${device.descriptor.id}`,
  };
}

function faucetDetails(device: SimFaucet): DeviceDetails {
  return {
    device_id: device.descriptor.id,
    model: "SEN",
    model_name: "Sensate touchless kitchen faucet",
    serial: null,
    install_date: "2024-11-30",
    registered: null,
    hardware: "CC3235SF",
    firmware: [
      { key: "gateway", version: "16.0" },
      { key: "application", version: "4.0" },
      { key: "certificate", version: "3.0" },
    ],
    parts: [],
    faults: [],
    leak_history: ["2026-03-02T07:14:00Z", "2025-12-19T21:40:00Z"],
    web_url: null,
    diagnostics_path: `/api/diagnostics/config_entry/entry_main/device/${device.descriptor.id}`,
  };
}

function controllerDetails(device: SimController): DeviceDetails {
  return {
    device_id: device.descriptor.id,
    model: device.descriptor.model,
    model_name: device.descriptor.model_name,
    serial: "HUB-55821",
    install_date: "2024-08-17",
    registered: "2024-08-17T18:03:00Z",
    hardware: null,
    firmware: [{ key: "controller", version: "2.88" }],
    parts: [],
    faults: [],
    leak_history: [],
    web_url: "http://192.0.2.40/",
    diagnostics_path: `/api/diagnostics/config_entry/entry_main/device/${device.descriptor.id}`,
  };
}

function entries(title: string): EntryInfo[] {
  return [{ entry_id: "entry_main", title, sidebar_panel: true, stream_connected: true }];
}

export function yourSetup(): Scenario {
  const states: Record<string, HassEntity> = {};
  const zone = [{ code: 31, name: "Rainhead", variant: "Katalyst" }, { code: 11, name: "Showerhead" }, { code: 1, name: "Handshower" }];
  const left = buildValve(
    {
      id: "dev_shower_left", name: "Shower Left", area: "Master Bathroom", model: "K-28210", modelName: "Anthem 3-outlet valve",
      unit: "°F", zones: [{ zone: 1, outlets: zone }], favorites: ["Morning", "Relax", "Quick Rinse"],
      experiences: ["Wake Up", "Cool Down"], warmup: "Off", scald: 113, defaultTemperature: 106, duration: 30,
      flow: false, defaultOutlets: [{ zone: 1, outlets: [1] }],
    },
    states,
  );
  const right = buildValve(
    {
      id: "dev_shower_right", name: "Shower Right", area: "Master Bathroom", model: "K-28210", modelName: "Anthem 3-outlet valve",
      unit: "°F", zones: [{ zone: 1, outlets: zone }], favorites: ["Evening"], experiences: [],
      warmup: "All Outlets", scald: 113, defaultTemperature: 104, duration: 30, flow: false,
      defaultOutlets: [{ zone: 1, outlets: [2] }],
    },
    states,
  );
  const faucet = buildFaucet(
    {
      id: "dev_island_faucet", name: "Island Faucet", area: "Kitchen", metric: false,
      presets: [{ name: "A Glass of Water", amount: 8.45 }, { name: "One Cup", amount: 8 }],
    },
    states,
  );
  return {
    key: "yours",
    label: "Your setup: 2 × K-28210 + Sensate",
    user: "Katherine",
    entries: entries("Kohler Konnect (you@example.com)"),
    devices: [left, right, faucet],
    states,
    details: {
      [left.descriptor.id]: valveDetails(left, "sio32298jc", "2025-05-06"),
      [right.descriptor.id]: valveDetails(right, "sio32301kd", "2025-05-06"),
      [faucet.descriptor.id]: faucetDetails(faucet),
    },
    options: { entry_main: { max_run_minutes: 10, sidebar_panel: true } },
  };
}

export function bigSystem(): Scenario {
  const states: Record<string, HassEntity> = {};
  const valve = buildValve(
    {
      id: "dev_valve_primary", name: "Primary Bath", area: "Primary Bath", model: "K-28212", modelName: "Anthem 6-outlet valve",
      unit: "°F",
      zones: [
        { zone: 1, outlets: [{ code: 11, name: "Showerhead" }, { code: 52, name: "Body Sprays", variant: "Multiple" }, { code: 1, name: "Handshower" }] },
        { zone: 2, outlets: [{ code: 39, name: "Rainhead", variant: "Real Rain" }, { code: 11, name: "Showerhead" }, { code: 21, name: "Tub Filler" }] },
      ],
      favorites: ["Morning", "Steam & Rain", "Kids"], experiences: ["Wake Up", "Cool Down", "Beginner Ice Shower"],
      warmup: "Started Outlets", scald: 116, defaultTemperature: 104, duration: 45, flow: true, autoRestore: true,
      defaultOutlets: [{ zone: 1, outlets: [1] }, { zone: 2, outlets: [1] }],
    },
    states,
  );
  const controller = buildController(
    {
      id: "dev_anthem_plus", name: "Primary Bath", area: "Primary Bath",
      favorites: ["Morning", "Spa Evening", "Steam Only"], experiences: ["Breathe", "Focus", "Detox"], zones: [1, 2],
    },
    states,
  );
  controller.descriptor.name = "Anthem Plus";
  valve.descriptor.name = "Anthem Valve";
  const fan = buildSmartFan(
    {
      id: "dev_smart_fan", name: "Primary Bath Fan", area: "Primary Bath",
      fanFavorites: ["Steamy", "Quiet"], lightFavorites: ["Morning", "Night"],
    },
    states,
  );
  const untested = { untested: true };
  const toilet = buildProduct(
    { id: "dev_numi", kind: "toilet", name: "Numi", area: "Primary Bath", model: "Numi 2.0" },
    [
      ["activity", "sensor.activity", "idle", { options: ["idle", "rear_wash", "front_wash", "dryer"] }],
      ["occupied", "binary_sensor.occupied", "off"],
      ["cleaning", "binary_sensor.uv_cleaning", "off"],
      ["last_error", "sensor.last_error", "unknown"],
      ["connected", "binary_sensor.cloud_connection", "on"],
      ["flush", "button.flush", "unknown", untested],
      ["seat_heat", "select.seat_heat", "medium", untested],
      ["ambient_light", "light.ambient_light", "on", {
        ...untested,
        supported_color_modes: ["brightness"],
        brightness: 153,
        effect_list: ["sunrise", "forest_breeze", "deep_ocean", "bokeh"],
        effect: "deep_ocean",
      }],
    ],
    states,
  );
  const evo = buildProduct(
    { id: "dev_evocycle", kind: "evocycle", name: "Guest Shower", area: "Guest Bath", model: "EvoCycle" },
    [
      ["evo_activity", "sensor.activity", "idle", { options: ["idle", "standard", "cycle", "rapid_clean"] }],
      ["evo_problem", "binary_sensor.problem", "off"],
      ["water_saved", "sensor.water_saved", "326", { unit_of_measurement: "gal" }],
      ["last_rapid_clean", "sensor.last_rapid_clean", "2026-10-21T14:12:00+00:00", { device_class: "timestamp" }],
      ["connected", "binary_sensor.cloud_connection", "on"],
      ["evo_shower", "select.shower", "off", { ...untested, options: ["off", "standard", "cycle"] }],
      ["start_rapid_clean", "button.start_rapid_clean", "unknown", untested],
      ["default_temperature", "number.default_temperature", "100", {
        ...untested, min: 86, max: 118, step: 1, unit_of_measurement: "°F", device_class: "temperature",
      }],
    ],
    states,
  );
  const ice = buildProduct(
    { id: "dev_ice_bath", kind: "ice_bath", name: "Sam", area: "Gym", model: "Ice Bath" },
    [
      ["last_session", "sensor.last_session", "2026-10-27T14:00:00+00:00", { device_class: "timestamp" }],
      ["last_session_duration", "sensor.last_session_duration", "185", { device_class: "duration", unit_of_measurement: "s" }],
      ["last_session_temperature", "sensor.last_session_temperature", "39", { unit_of_measurement: "°F" }],
      ["sessions_this_week", "sensor.sessions_this_week", "2", { target: 3 }],
      ["routine", "sensor.routine", "Mon, Wed, Fri", { days: ["mon", "wed", "fri"], reminder_time: "07:30" }],
    ],
    states,
  );
  return {
    key: "big",
    label: "K-28212 + Anthem+ controller + Smart Fan, Numi, EvoCycle and Ice Bath",
    user: "Alex",
    entries: entries("Kohler Konnect (owner@example.com)"),
    devices: [valve, controller, fan, toilet, evo, ice],
    states,
    details: {
      [valve.descriptor.id]: valveDetails(valve, "kk28212x91", "2024-08-17"),
      [controller.descriptor.id]: controllerDetails(controller),
      [fan.descriptor.id]: smartFanDetails(fan),
      [toilet.descriptor.id]: productDetails(toilet),
      [evo.descriptor.id]: productDetails(evo),
      [ice.descriptor.id]: productDetails(ice),
    },
    options: { entry_main: { zone_grouping: "numbered", sidebar_panel: true } },
  };
}

export function metricAccount(): Scenario {
  const states: Record<string, HassEntity> = {};
  const valve = buildValve(
    {
      id: "dev_dusche", name: "Dusche", area: "Badezimmer", model: "K-28209", modelName: "Anthem 2-outlet valve",
      unit: "°C", zones: [{ zone: 1, outlets: [{ code: 31, name: "Rainhead", variant: "Silk" }, { code: 1, name: "Handshower" }] }],
      favorites: ["Morgen"], experiences: [], warmup: "Off", scald: 45, defaultTemperature: 39, duration: 20,
      flow: true, defaultOutlets: [{ zone: 1, outlets: [1] }],
    },
    states,
  );
  const faucet = buildFaucet(
    { id: "dev_kuche", name: "Kitchen", area: "Küche", metric: true, presets: [{ name: "Pasta Pot", amount: 3000 }] },
    states,
  );
  return {
    key: "metric",
    label: "Metric account (°C, L, mL)",
    user: "Jo",
    entries: entries("Kohler Konnect (jo@example.com)"),
    devices: [valve, faucet],
    states,
    details: {
      [valve.descriptor.id]: valveDetails(valve, "de88213kq", "2025-02-11"),
      [faucet.descriptor.id]: faucetDetails(faucet),
    },
    options: { entry_main: { max_run_minutes: 5, sidebar_panel: true } },
  };
}

export const SCENARIOS = [yourSetup, bigSystem, metricAccount];
