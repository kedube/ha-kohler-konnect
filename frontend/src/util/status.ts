import { t } from "../i18n";
import type { DeviceDescriptor, HomeAssistant } from "../types";
import { entityFor, isAvailable, isOn, numericState, stateLabel } from "./entity";
import { formatCountdown, formatTemperature } from "./format";

export type Tone = "idle" | "active" | "alert" | "offline";

export interface DeviceStatus {
  text: string;
  tone: Tone;
  running: boolean;
}

/** The valve's System Status values, as `sensor.py` publishes them. */
export const VALVE_RUNNING = "Water Running";
export const VALVE_WARMING = "Warming Up";
export const VALVE_PAUSED = "Paused";

export function showerPhase(
  hass: HomeAssistant | undefined,
  device: DeviceDescriptor,
): "idle" | "running" | "warming" | "paused" | "offline" {
  const status = entityFor(hass, device, "status");
  if (!isAvailable(status)) return "offline";
  if (status.state === VALVE_RUNNING) return "running";
  if (status.state === VALVE_WARMING) return "warming";
  if (status.state === VALVE_PAUSED) return "paused";
  return "idle";
}

/** A shower's phase as a screen reader should hear it when it changes. */
export function showerPhaseText(phase: ReturnType<typeof showerPhase>): string {
  switch (phase) {
    case "warming":
      return `${t("shower.warming_title")}. ${t("shower.locked_warming")}`;
    case "running":
      return t("shower.running_title");
    case "paused":
      return `${t("shower.paused_title")}. ${t("shower.paused_body")}`;
    case "offline":
      return t("status.offline");
    default:
      return t("status.idle");
  }
}

export function deviceStatus(hass: HomeAssistant | undefined, device: DeviceDescriptor): DeviceStatus {
  const problem = isOn(entityFor(hass, device, "problem"));
  if (device.kind === "faucet") {
    if (isOn(entityFor(hass, device, "leak"))) {
      return { text: t("status.leak"), tone: "alert", running: false };
    }
    const water = entityFor(hass, device, "water") ?? entityFor(hass, device, "status");
    if (!isAvailable(water)) return { text: t("status.offline"), tone: "offline", running: false };
    if (isOn(entityFor(hass, device, "dispensing"))) {
      return { text: t("status.dispensing"), tone: "active", running: true };
    }
    if (isOn(entityFor(hass, device, "status")) || isOn(water)) {
      return { text: t("status.water_on"), tone: "active", running: true };
    }
    return { text: t("status.off"), tone: "idle", running: false };
  }

  if (device.kind === "valve" || device.kind === "controller") {
    const phase = showerPhase(hass, device);
    if (phase === "offline") return { text: t("status.offline"), tone: "offline", running: false };
    // In the unit Home Assistant shows the entity in, which can differ from the account's.
    const entity = entityFor(hass, device, "temperature:1");
    const unit = String(entity?.attributes.unit_of_measurement ?? device.units.temperature);
    const temperature = formatTemperature(numericState(entity), unit);
    const remaining = entityFor(hass, device, "status")?.attributes.seconds_remaining;
    if (phase === "running") {
      const parts = [t("status.running"), temperature];
      if (typeof remaining === "number") {
        parts.push(t("status.minutes_left", { time: formatCountdown(remaining) }));
      }
      return { text: parts.join(" · "), tone: "active", running: true };
    }
    if (phase === "warming") {
      return { text: `${t("status.warming")} · ${temperature}`, tone: "active", running: true };
    }
    if (phase === "paused") return { text: t("status.paused"), tone: "active", running: false };
    if (problem) return { text: t("status.problem"), tone: "alert", running: false };
    if (isOn(entityFor(hass, device, "steam_state"))) {
      return { text: t("controller.steam"), tone: "active", running: true };
    }
    return { text: t("status.idle"), tone: "idle", running: false };
  }

  if (device.kind === "smart_fan") {
    const fan = entityFor(hass, device, "fan_state");
    const connected = entityFor(hass, device, "connected");
    if (!isAvailable(fan) || connected?.state === "off") {
      return { text: t("status.offline"), tone: "offline", running: false };
    }
    const spinning = fan.state !== "off" && fan.state !== "unknown";
    const parts = spinning ? [t("smart_fan.fan_on"), stateLabel(hass, fan)] : [];
    if (isOn(entityFor(hass, device, "light_state"))) parts.push(t("smart_fan.light_on"));
    if (parts.length) return { text: parts.join(" · "), tone: "active", running: spinning };
    return { text: t("status.off"), tone: "idle", running: false };
  }

  if (device.kind === "toilet") {
    const activity = entityFor(hass, device, "activity");
    const connected = entityFor(hass, device, "connected");
    if (!isAvailable(activity) || connected?.state === "off") {
      return { text: t("status.offline"), tone: "offline", running: false };
    }
    const error = entityFor(hass, device, "last_error")?.state;
    if (error && error !== "unknown" && error !== "unavailable") {
      return { text: t("status.problem"), tone: "alert", running: false };
    }
    if (activity.state !== "idle") return { text: stateLabel(hass, activity), tone: "active", running: true };
    if (isOn(entityFor(hass, device, "cleaning"))) return { text: t("toilet.cleaning"), tone: "active", running: true };
    if (isOn(entityFor(hass, device, "occupied"))) return { text: t("toilet.occupied"), tone: "idle", running: false };
    return { text: t("status.ready"), tone: "idle", running: false };
  }

  if (device.kind === "evocycle") {
    const activity = entityFor(hass, device, "evo_activity");
    if (!isAvailable(activity) || entityFor(hass, device, "connected")?.state === "off") {
      return { text: t("status.offline"), tone: "offline", running: false };
    }
    if (isOn(entityFor(hass, device, "evo_problem"))) {
      return { text: t("status.problem"), tone: "alert", running: false };
    }
    if (activity.state !== "idle") return { text: stateLabel(hass, activity), tone: "active", running: true };
    return { text: t("status.ready"), tone: "idle", running: false };
  }

  if (device.kind === "ice_bath") {
    // A journal, not a connected bath: it says how the week is going.
    const week = entityFor(hass, device, "sessions_this_week");
    if (!isAvailable(week)) return { text: t("status.offline"), tone: "offline", running: false };
    const done = numericState(week) ?? 0;
    const target = week.attributes.target;
    const text = typeof target === "number" ? t("ice_bath.week_of", { done, target }) : t("ice_bath.week", { done });
    return { text, tone: "idle", running: false };
  }

  if (problem) return { text: t("status.problem"), tone: "alert", running: false };
  return { text: t("status.ready"), tone: "idle", running: false };
}
