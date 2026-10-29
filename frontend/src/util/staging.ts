import type { DeviceDescriptor, DeviceLive, HomeAssistant } from "../types";
import { entityFor, numericState } from "./entity";

// A shower composed while the valve is idle: outlets and a temperature per zone, sent as
// one `custom_shower` on Start. One command matters: the valve can't take two back to back
// during its warm-up, and `custom_shower` is the integration's single-command form.

export interface StagedZone {
  zone: number;
  outlets: number[];
  temperature: number | null;
}

export interface Bounds {
  min: number;
  max: number;
}

export function clampTemperature(value: number | null, bounds?: Bounds): number | null {
  if (value === null || !bounds) return value;
  return Math.min(bounds.max, Math.max(bounds.min, Math.round(value)));
}

function boundsFor(live: DeviceLive | undefined, zone: number): Bounds | undefined {
  return live?.staging_bounds?.find((b) => b.zone === zone);
}

/**
 * Start from what Simple Start would run: the valve's own default shower. Staged
 * temperatures are in the account's unit, as `custom_shower` takes them; the Temperature
 * number stands in only when it shows that same unit, since Home Assistant converts it to
 * its own unit system.
 */
export function defaultStage(
  device: DeviceDescriptor,
  live: DeviceLive | undefined,
  hass: HomeAssistant | undefined,
): StagedZone[] {
  return device.zones.map(({ zone }) => {
    const fallback = live?.default_shower?.zones.find((d) => d.zone === zone);
    const entity = entityFor(hass, device, `temperature:${zone}`);
    const sameUnit = entity?.attributes?.unit_of_measurement === device.units.temperature;
    const current = sameUnit ? numericState(entity) : null;
    return {
      zone,
      outlets: fallback ? [...fallback.outlets] : [],
      temperature: clampTemperature(fallback?.temperature ?? current, boundsFor(live, zone)),
    };
  });
}

export function toggleStagedOutlet(
  stage: StagedZone[],
  zone: number,
  position: number,
): StagedZone[] {
  return stage.map((z) => {
    if (z.zone !== zone) return z;
    const outlets = z.outlets.includes(position)
      ? z.outlets.filter((p) => p !== position)
      : [...z.outlets, position].sort((a, b) => a - b);
    return { ...z, outlets };
  });
}

export function setStagedTemperature(
  stage: StagedZone[],
  zone: number,
  temperature: number,
  live?: DeviceLive,
): StagedZone[] {
  return stage.map((z) =>
    z.zone === zone
      ? { ...z, temperature: clampTemperature(temperature, boundsFor(live, zone)) }
      : z,
  );
}

export function hasStagedOutlets(stage: StagedZone[]): boolean {
  return stage.some((z) => z.outlets.length > 0);
}

/** Zone 1's temperature is the one field `custom_shower` requires. */
export function canStart(stage: StagedZone[]): boolean {
  const zone1 = stage.find((z) => z.zone === 1);
  return hasStagedOutlets(stage) && zone1?.temperature !== null && zone1 !== undefined;
}

export function customShowerData(
  deviceId: string,
  stage: StagedZone[],
  flow?: { zone1?: number | null; zone2?: number | null },
): Record<string, unknown> {
  const data: Record<string, unknown> = { device_id: deviceId };
  for (const z of stage) {
    if (z.temperature !== null) data[`zone${z.zone}_temperature`] = z.temperature;
    for (const position of z.outlets) data[`zone${z.zone}_outlet_${position}`] = true;
  }
  if (flow?.zone1 !== undefined && flow.zone1 !== null) data.flow = Math.round(flow.zone1);
  if (flow?.zone2 !== undefined && flow.zone2 !== null && stage.some((z) => z.zone === 2)) {
    data.zone2_flow = Math.round(flow.zone2);
  }
  return data;
}
