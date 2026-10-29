// A stand-in for Home Assistant: entity states, the integration's websocket commands, and
// a small simulation of each device, so every screen can be exercised without hardware.

import type {
  AccountOptions,
  DeviceLive,
  DevicesMessage,
  HassEntity,
  HomeAssistant,
  UsageBucket,
  UsageHistory,
  UsagePeriod,
} from "../src/types";
import { dailyUsage, type Scenario, type SimDevice, type SimFaucet, type SimValve } from "./scenarios";

type Listener = (message: unknown) => void;

const iso = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

export class FakeHass {
  darkMode = true;
  admin = true;
  language = "en";
  onChange: (hass: HomeAssistant) => void = () => undefined;

  private states: Record<string, HassEntity>;
  private readonly deviceListeners = new Set<Listener>();
  private readonly liveListeners = new Map<string, Set<Listener>>();
  private readonly timers = new Map<string, number[]>();
  private readonly options: Record<string, Record<string, unknown>>;
  private readonly autoOff = new Map<string, string>();
  private readonly lastStage = new Map<string, Record<string, unknown>>();

  constructor(private readonly scenario: Scenario) {
    this.states = structuredClone(scenario.states);
    this.options = structuredClone(scenario.options);
  }

  get hass(): HomeAssistant {
    return {
      states: this.states,
      user: { id: "user", name: this.scenario.user, is_admin: this.admin },
      language: this.language,
      locale: { language: this.language, first_weekday: "language" },
      themes: { darkMode: this.darkMode },
      connection: { subscribeMessage: (cb, msg) => this.subscribe(cb as Listener, msg) },
      callWS: <T>(msg: Record<string, unknown>) => this.callWS(msg) as Promise<T>,
      callService: (domain, service, data) => this.callService(domain, service, data ?? {}),
      formatEntityState: (stateObj, state) => formatState(stateObj, state ?? stateObj.state),
      formatEntityAttributeValue: (_stateObj, _attribute, value) => {
        const text = String(value ?? "").replace(/_/g, " ");
        return text.charAt(0).toUpperCase() + text.slice(1);
      },
    };
  }

  dispose() {
    for (const ids of this.timers.values()) ids.forEach((id) => window.clearTimeout(id));
  }

  // ---------------------------------------------------------------- state helpers

  private emit() {
    this.onChange(this.hass);
  }

  private set(entityId: string | undefined, state: string, attributes: Record<string, unknown> = {}) {
    if (!entityId) return;
    const current = this.states[entityId];
    const now = new Date().toISOString();
    this.states = {
      ...this.states,
      [entityId]: {
        entity_id: entityId,
        state,
        attributes: { ...(current?.attributes ?? {}), ...attributes },
        last_changed: current?.state === state ? current.last_changed : now,
        last_updated: now,
      },
    };
  }

  private later(deviceId: string, ms: number, run: () => void) {
    const id = window.setTimeout(() => {
      run();
      this.emit();
    }, ms);
    this.timers.set(deviceId, [...(this.timers.get(deviceId) ?? []), id]);
  }

  private cancel(deviceId: string) {
    for (const id of this.timers.get(deviceId) ?? []) window.clearTimeout(id);
    this.timers.delete(deviceId);
  }

  private device(id: string): SimDevice | undefined {
    return this.scenario.devices.find((d) => d.descriptor.id === id);
  }

  private owner(entityId: string): [SimDevice, string] | undefined {
    for (const device of this.scenario.devices) {
      const role = Object.entries(device.descriptor.roles).find(([, id]) => id === entityId)?.[0];
      if (role) return [device, role];
    }
    return undefined;
  }

  private role(device: SimDevice, role: string) {
    return device.descriptor.roles[role];
  }

  // ---------------------------------------------------------------- websocket

  private async subscribe(callback: Listener, message: Record<string, unknown>) {
    if (message.type === "kohler_konnect/subscribe_devices") {
      this.deviceListeners.add(callback);
      queueMicrotask(() => callback(this.devicesMessage()));
      return async () => void this.deviceListeners.delete(callback);
    }
    if (message.type === "kohler_konnect/subscribe_device") {
      const id = String(message.device_id);
      const set = this.liveListeners.get(id) ?? new Set();
      set.add(callback);
      this.liveListeners.set(id, set);
      queueMicrotask(() => callback(this.live(id)));
      return async () => void set.delete(callback);
    }
    throw new Error(`Unknown subscription ${String(message.type)}`);
  }

  private devicesMessage(): DevicesMessage {
    return {
      entries: this.scenario.entries.map((e) => ({
        ...e,
        title: this.admin ? e.title : null,
        sidebar_panel: Boolean(this.options[e.entry_id]?.sidebar_panel ?? true),
      })),
      devices: this.scenario.devices.map((d) => d.descriptor),
    };
  }

  private live(id: string): DeviceLive {
    const device = this.device(id);
    const live: DeviceLive = {
      device_id: id,
      stream_connected: true,
      cloud_connected: true,
      last_update: new Date(Date.now() - 95_000).toISOString(),
    };
    if (device?.kind === "valve") {
      live.default_shower = {
        zones: device.descriptor.zones.map((z) => ({
          zone: z.zone,
          outlets: device.defaultOutlets.find((d) => d.zone === z.zone)?.outlets ?? [],
          temperature: device.defaultTemperature,
        })),
        refused_because: null,
      };
      const f = device.descriptor.units.temperature === "°F";
      live.staging_bounds = device.descriptor.zones.map((z) => ({
        zone: z.zone,
        min: f ? 59 : 15,
        max: Number(this.states[device.descriptor.roles.max_temperature]?.state ?? device.scald),
      }));
    }
    if (device?.kind === "faucet") live.auto_off_at = this.autoOff.get(id) ?? null;
    return live;
  }

  private pushLive(id: string) {
    for (const listener of this.liveListeners.get(id) ?? []) listener(this.live(id));
  }

  private async callWS(message: Record<string, unknown>): Promise<unknown> {
    await new Promise((resolve) => setTimeout(resolve, 120));
    const type = String(message.type);
    const id = String(message.device_id ?? "");
    switch (type) {
      case "kohler_konnect/usage_history":
        return this.usage(
          id,
          message.period as UsagePeriod,
          Number(message.week_start ?? 6),
          message.anchor as string | undefined,
        );
      case "kohler_konnect/device_details":
        if (!this.admin) throw new Error("Unauthorized");
        return this.scenario.details[id];
      case "kohler_konnect/restart_valve": {
        const device = this.device(id);
        if (device?.kind === "valve") this.stopValve(device);
        this.emit();
        return null;
      }
      case "kohler_konnect/set_warmup_auto_restore": {
        const device = this.device(id);
        if (device) {
          device.descriptor.capabilities.warmup_auto_restore = Boolean(message.enabled);
          this.set(device.descriptor.roles.warmup_auto_restore, message.enabled ? "on" : "off");
          this.emit();
        }
        return null;
      }
      case "kohler_konnect/get_options":
        return this.getOptions(String(message.entry_id));
      case "kohler_konnect/update_options": {
        const entry = String(message.entry_id);
        const changes = message.changes as Record<string, unknown>;
        const reloads = ["zone_grouping", "max_run_minutes"].some((k) => k in changes);
        this.options[entry] = { ...this.options[entry], ...changes };
        for (const listener of this.deviceListeners) listener(this.devicesMessage());
        return { reloads, values: this.options[entry] };
      }
      case "auth/sign_path":
        return { path: `${String(message.path)}?authSig=mockup` };
      default:
        throw new Error(`Unknown command ${type}`);
    }
  }

  private getOptions(entryId: string): AccountOptions {
    const hasShowers = this.scenario.devices.some((d) => d.kind === "valve" || d.kind === "controller");
    const hasFaucets = this.scenario.devices.some((d) => d.kind === "faucet");
    return {
      entry_id: entryId,
      title: this.scenario.entries[0].title,
      values: this.options[entryId],
      fields: [
        ...(hasShowers
          ? [{ key: "zone_grouping", kind: "select" as const, options: ["subdevices", "outlet_labels", "numbered"], reloads: true }]
          : []),
        ...(hasFaucets ? [{ key: "max_run_minutes", kind: "minutes" as const, min: 0, max: 120, reloads: true }] : []),
        { key: "sidebar_panel", kind: "toggle" as const, reloads: false },
      ],
    };
  }

  private usage(deviceId: string, period: UsagePeriod, weekStart: number, anchor?: string): UsageHistory {
    const device = this.device(deviceId)!;
    const faucet = device.kind === "faucet";
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const at = anchor ? new Date(`${anchor}T00:00:00`) : new Date(today);
    let start: Date;
    let end: Date;
    if (period === "week") {
      start = new Date(at);
      // `getDay` counts Sunday 0; `weekStart`, as Python does, Monday 0.
      start.setDate(at.getDate() - ((at.getDay() + 6 - weekStart) % 7));
      end = new Date(start);
      end.setDate(start.getDate() + 6);
    } else if (period === "month") {
      start = new Date(at.getFullYear(), at.getMonth(), 1);
      end = new Date(at.getFullYear(), at.getMonth() + 1, 0);
    } else {
      start = new Date(at.getFullYear(), 0, 1);
      end = new Date(at.getFullYear(), 11, 31);
    }
    const installed = new Date(`${this.scenario.details[deviceId]?.install_date ?? "2024-01-01"}T00:00:00`);
    const buckets: UsageBucket[] = [];
    if (period === "year") {
      for (let m = 0; m < 12; m++) {
        let volume = 0;
        let minutes = 0;
        let uses = 0;
        const days = new Date(start.getFullYear(), m + 1, 0).getDate();
        const future = new Date(start.getFullYear(), m, 1) > today;
        for (let d = 1; d <= days && !future; d++) {
          const date = new Date(start.getFullYear(), m, d);
          if (date > today || date < installed) continue;
          const u = dailyUsage(deviceId, date, faucet);
          volume += u.volume;
          minutes += u.minutes;
          uses += u.uses;
        }
        buckets.push({
          key: `${start.getFullYear()}-${String(m + 1).padStart(2, "0")}`,
          volume: future ? null : Math.round(volume * 100) / 100,
          minutes: future ? null : Math.round(minutes),
          uses: future ? null : uses,
        });
      }
    } else {
      for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
        const future = d > today;
        const u = future || d < installed ? null : dailyUsage(deviceId, d, faucet);
        buckets.push({ key: iso(d), volume: u?.volume ?? null, minutes: u?.minutes ?? null, uses: u?.uses ?? null });
      }
    }
    const values = buckets.filter((b) => b.volume !== null);
    const total = {
      volume: Math.round(values.reduce((s, b) => s + (b.volume ?? 0), 0) * 100) / 100,
      minutes: Math.round(values.reduce((s, b) => s + (b.minutes ?? 0), 0)),
    };
    const elapsedDays = Math.max(
      1,
      Math.round((Math.min(end.getTime(), today.getTime()) - Math.max(start.getTime(), installed.getTime())) / 86400000) + 1,
    );
    const previousStart = new Date(start);
    previousStart.setDate(start.getDate() - 1);
    const nextStart = new Date(end);
    nextStart.setDate(end.getDate() + 1);
    return {
      period,
      anchor: iso(at),
      start: iso(start),
      end: iso(end),
      unit: device.descriptor.units.volume,
      buckets,
      total,
      average: {
        volume: Math.round((total.volume / elapsedDays) * 100) / 100,
        minutes: Math.round((total.minutes / elapsedDays) * 10) / 10,
      },
      high: {
        volume: Math.max(0, ...values.map((b) => b.volume ?? 0)),
        minutes: Math.max(0, ...values.map((b) => b.minutes ?? 0)),
      },
      previous: previousStart < installed ? null : iso(previousStart),
      next: nextStart > today ? null : iso(nextStart),
    };
  }

  // ---------------------------------------------------------------- services

  private async callService(domain: string, service: string, data: Record<string, unknown>): Promise<unknown> {
    await new Promise((resolve) => setTimeout(resolve, 180));
    if (domain === "kohler_konnect" && service === "custom_shower") {
      const device = this.device(String(data.device_id));
      if (device?.kind !== "valve") throw new Error("Choose a valve");
      this.startValve(device, data);
    } else if (domain === "kohler_konnect" && service === "dispense") {
      const device = this.device(String(data.device_id));
      if (device?.kind !== "faucet") throw new Error("Choose a faucet");
      this.dispense(device, data);
    } else {
      const owned = this.owner(String(data.entity_id));
      if (!owned) throw new Error(`Unknown entity ${String(data.entity_id)}`);
      const [device, role] = owned;
      this.entityService(device, role, domain, service, data);
    }
    this.emit();
    return null;
  }

  private entityService(device: SimDevice, role: string, domain: string, service: string, data: Record<string, unknown>) {
    const entityId = String(data.entity_id);
    if (domain === "number") {
      this.set(entityId, String(data.value));
      if (device.kind === "valve" && role === "max_temperature") this.pushLive(device.descriptor.id);
      return;
    }
    if (domain === "button") {
      if (role === "clear_leak") this.set(this.role(device, "leak"), "off");
      if (device.kind === "smart_fan") this.smartFanService(device, role, service, data);
      return;
    }
    if (device.kind === "toilet" || device.kind === "evocycle" || device.kind === "ice_bath") {
      // Products shown in the generic view: a select takes its option, a switch its position,
      // and a light its brightness and effect too.
      if (domain === "select") return this.set(entityId, String(data.option));
      const light: Record<string, unknown> = {};
      if (typeof data.brightness_pct === "number") light.brightness = Math.round((data.brightness_pct / 100) * 255);
      if (typeof data.effect === "string") light.effect = data.effect;
      this.set(entityId, service === "turn_on" ? "on" : "off", light);
      return;
    }
    if (device.kind === "valve") return this.valveService(device, role, service, data);
    if (device.kind === "faucet") return this.faucetService(device, role, service);
    if (device.kind === "smart_fan") return this.smartFanService(device, role, service, data);
    return this.controllerService(device, role, service, data);
  }

  /** The fan and light run as their defaults or a favorite; the defaults just change. */
  private smartFanService(device: SimDevice, role: string, service: string, data: Record<string, unknown>) {
    const id = (r: string) => this.role(device, r);
    const fan = (on: boolean, favorite: string | null = null) => {
      const speed = favorite === "Steamy" ? "high" : favorite === "Quiet" ? "low" : this.states[id("default_fan_speed")].state;
      this.set(id("fan_state"), on ? speed : "off", { favorite: on ? favorite : null });
      this.set(id("fan"), on ? "on" : "off", { preset_mode: on ? favorite : null });
    };
    const light = (on: boolean, favorite: string | null = null) => {
      const brightness = favorite === "Night" ? 20 : favorite === "Morning" ? 80 : Number(this.states[id("default_light_brightness")].state);
      this.set(id("light_state"), on ? "on" : "off", { brightness: on ? brightness : null, favorite: on ? favorite : null });
      this.set(id("light"), on ? "on" : "off", { effect: on ? favorite : null });
    };
    if (role === "stop_all") {
      fan(false);
      light(false);
    } else if (role === "fan") {
      fan(service !== "turn_off", (data.preset_mode as string | undefined) ?? null);
    } else if (role === "light") {
      light(service !== "turn_off", (data.effect as string | undefined) ?? null);
    } else if (service === "select_option") {
      this.set(id(role), String(data.option));
    } else if (service === "turn_on" || service === "turn_off") {
      this.set(id(role), service === "turn_on" ? "on" : "off");
    }
  }

  private valveService(device: SimValve, role: string, service: string, data: Record<string, unknown>) {
    const option = String(data.option ?? "");
    if (role === "shower") {
      if (service === "turn_off") return this.stopValve(device);
      return this.startValve(device, this.stageFromDefaults(device));
    }
    if (role.startsWith("outlet:")) {
      const on = service === "turn_on";
      const running = this.states[this.role(device, "status")]?.state !== "Idle";
      if (!running && on) {
        const [, zone, position] = role.split(":");
        return this.startValve(device, {
          zone1_temperature: Number(this.states[this.role(device, "temperature:1")].state),
          [`zone${zone}_outlet_${position}`]: true,
        });
      }
      this.set(this.role(device, role), on ? "on" : "off");
      const anyOn = Object.keys(device.descriptor.roles).some(
        (r) => r.startsWith("outlet:") && this.states[this.role(device, r)].state === "on",
      );
      if (!anyOn) this.stopValve(device);
      return;
    }
    if (role === "favorite" || role === "experience") {
      if (option === "Off") return this.stopValve(device);
      this.set(this.role(device, role), option);
      return this.startValve(device, this.stageFromDefaults(device), role);
    }
    if (role === "warmup" || role === "max_duration") this.set(this.role(device, role), option);
    if (role === "warmup_auto_restore") this.set(this.role(device, role), service === "turn_on" ? "on" : "off");
    if (role === "report_log") {
      const on = service === "turn_on";
      this.set(this.role(device, role), on ? "on" : "off", { file: on ? "report_20261028_161502.jsonl" : null });
    }
  }

  private stageFromDefaults(device: SimValve): Record<string, unknown> {
    const data: Record<string, unknown> = { zone1_temperature: device.defaultTemperature };
    for (const z of device.defaultOutlets) {
      for (const p of z.outlets) data[`zone${z.zone}_outlet_${p}`] = true;
      if (z.zone === 2) data.zone2_temperature = device.defaultTemperature;
    }
    return data;
  }

  private startValve(device: SimValve, data: Record<string, unknown>, scene?: string) {
    const id = device.descriptor.id;
    // A start while the valve waits after its warm-up resumes it: the water is warm already.
    const resuming = this.states[this.role(device, "status")]?.state === "Paused";
    this.cancel(id);
    this.lastStage.set(id, data);
    for (const z of device.descriptor.zones) {
      const temperature = data[`zone${z.zone}_temperature`] ?? data.zone1_temperature;
      if (temperature !== undefined) this.set(this.role(device, `temperature:${z.zone}`), String(temperature));
      for (const o of z.outlets) {
        this.set(this.role(device, `outlet:${z.zone}:${o.position}`), data[`zone${z.zone}_outlet_${o.position}`] ? "on" : "off");
      }
    }
    if (typeof data.flow === "number") this.set(this.role(device, "flow:1"), String(data.flow));
    if (typeof data.zone2_flow === "number") this.set(this.role(device, "flow:2"), String(data.zone2_flow));
    this.set(this.role(device, "shower"), "on");
    this.set(this.role(device, "at_temperature"), "off");
    const warmup = this.states[this.role(device, "warmup")]?.state ?? "Off";
    const duration = parseInt(this.states[this.role(device, "max_duration")]?.state ?? "30", 10) * 60;
    const run = () => {
      this.set(this.role(device, "status"), "Water Running", { seconds_remaining: duration, flowing_for_seconds: 0 });
      this.later(id, 4200, () => this.set(this.role(device, "at_temperature"), "on"));
      this.tick(device, duration);
    };
    if (warmup !== "Off" && !scene && !resuming) {
      this.set(this.role(device, "status"), "Warming Up", { seconds_remaining: null });
      this.later(id, 6500, () => {
        this.set(this.role(device, "status"), "Paused", { seconds_remaining: null });
        this.set(this.role(device, "at_temperature"), "on");
      });
    } else {
      run();
    }
  }

  private tick(device: SimValve, remaining: number) {
    if (remaining <= 0) return this.stopValve(device);
    this.later(device.descriptor.id, 1000, () => {
      const status = this.states[this.role(device, "status")];
      if (status?.state !== "Water Running") return;
      this.set(this.role(device, "status"), "Water Running", {
        seconds_remaining: remaining - 1,
        flowing_for_seconds: Number(status.attributes.flowing_for_seconds ?? 0) + 1,
      });
      this.tick(device, remaining - 1);
    });
  }

  private stopValve(device: SimValve) {
    this.cancel(device.descriptor.id);
    for (const role of Object.keys(device.descriptor.roles)) {
      if (role.startsWith("outlet:")) this.set(this.role(device, role), "off");
    }
    this.set(this.role(device, "shower"), "off");
    this.set(this.role(device, "status"), "Idle", { seconds_remaining: null, flowing_for_seconds: null });
    this.set(this.role(device, "at_temperature"), "off");
    this.set(this.role(device, "favorite"), "Off");
    this.set(this.role(device, "experience"), "Off");
  }

  private faucetService(device: SimFaucet, role: string, service: string) {
    const id = device.descriptor.id;
    if (role !== "water") return;
    this.cancel(id);
    if (service === "turn_on") {
      if (this.states[this.role(device, "handle")]?.state === "closed") {
        throw new Error("Open the handle to dispense water remotely.");
      }
      this.set(this.role(device, "water"), "on");
      this.set(this.role(device, "status"), "on");
      const minutes = Number(this.options.entry_main?.max_run_minutes ?? 10);
      if (minutes > 0) this.autoOff.set(id, new Date(Date.now() + minutes * 60_000).toISOString());
    } else {
      this.set(this.role(device, "water"), "off");
      this.set(this.role(device, "status"), "off");
      this.set(this.role(device, "dispensing"), "off", { preset: undefined });
      this.autoOff.delete(id);
    }
    this.pushLive(id);
  }

  private dispense(device: SimFaucet, data: Record<string, unknown>) {
    const id = device.descriptor.id;
    const presets = (device.descriptor.capabilities.presets ?? []) as { name: string; amount: number; unit: string }[];
    const preset = data.preset ? presets.find((p) => p.name === data.preset) : undefined;
    const amount = preset?.amount ?? Number(data.amount ?? 0);
    const seconds = Math.min(9, Math.max(3, device.metric ? amount / 400 : amount / 3));
    this.cancel(id);
    this.set(this.role(device, "dispensing"), "on", { preset: preset?.name });
    this.set(this.role(device, "status"), "on");
    this.later(id, seconds * 1000, () => {
      this.set(this.role(device, "dispensing"), "off", { preset: undefined });
      this.set(this.role(device, "status"), "off");
      this.set(this.role(device, "last_dispensed"), String(amount));
    });
  }

  private controllerService(device: SimDevice, role: string, service: string, data: Record<string, unknown>) {
    const on = service === "turn_on";
    const option = String(data.option ?? "");
    const start = () => {
      this.set(this.role(device, "shower"), "on");
      this.set(this.role(device, "status"), "Water Running");
      this.set(this.role(device, "outlet:1:1"), "on");
      this.set(this.role(device, "outlet:2:1"), "on");
    };
    const stop = () => {
      this.set(this.role(device, "shower"), "off");
      this.set(this.role(device, "status"), "Idle");
      for (const r of Object.keys(device.descriptor.roles)) if (r.startsWith("outlet:")) this.set(this.role(device, r), "off");
      this.set(this.role(device, "favorite"), "Off");
      this.set(this.role(device, "experience"), "Off");
    };
    if (role === "shower") return on ? start() : stop();
    if (role === "steam") {
      if (on && this.states[this.role(device, "shower")]?.state === "on") {
        throw new Error("Steam can't start while the shower runs.");
      }
      this.set(this.role(device, "steam"), on ? "on" : "off");
      this.set(this.role(device, "steam_state"), on ? "on" : "off");
      return;
    }
    if (role === "favorite" || role === "experience") {
      if (option === "Off") return stop();
      this.set(this.role(device, role), option);
      return start();
    }
    if (role === "system") this.set(this.role(device, role), on ? "on" : "off");
  }
}

const STATE_LABELS: Record<string, string> = {
  warmwhite: "Warm white",
  coolwhite: "Cool white",
  on: "On",
  off: "Off",
};

function formatState(entity: HassEntity, state: string): string {
  const own = state === entity.state;
  if (own && entity.attributes.device_class === "timestamp" && !Number.isNaN(Date.parse(state))) {
    return new Date(state).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }
  const unit = entity.attributes.unit_of_measurement;
  if (own && unit && Number.isFinite(Number(state))) return `${Number(state).toLocaleString()} ${String(unit)}`;
  return STATE_LABELS[state] ?? state.charAt(0).toUpperCase() + state.slice(1);
}
