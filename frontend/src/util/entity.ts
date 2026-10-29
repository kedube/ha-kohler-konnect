import type { DeviceDescriptor, HassEntity, HomeAssistant } from "../types";

// Reading a device's entities by role. A role whose entity is disabled, missing or
// unavailable reads as absent, so the control that depends on it is simply not drawn.

export function entityFor(
  hass: HomeAssistant | undefined,
  device: DeviceDescriptor | undefined,
  role: string,
): HassEntity | undefined {
  const entityId = device?.roles[role];
  return entityId ? hass?.states[entityId] : undefined;
}

export function isAvailable(entity: HassEntity | undefined): entity is HassEntity {
  return !!entity && entity.state !== "unavailable";
}

export function isOn(entity: HassEntity | undefined): boolean {
  return entity?.state === "on";
}

export function numericState(entity: HassEntity | undefined): number | null {
  if (!isAvailable(entity) || entity.state === "unknown") return null;
  const value = Number(entity.state);
  return Number.isFinite(value) ? value : null;
}

export function rolesWithPrefix(device: DeviceDescriptor, prefix: string): string[] {
  return Object.keys(device.roles).filter((role) => role.startsWith(prefix));
}

/** A state, or one of the entity's options, as Home Assistant shows it: translated, with its unit. */
export function stateLabel(hass: HomeAssistant | undefined, entity: HassEntity, state = entity.state): string {
  if (hass?.formatEntityState) return hass.formatEntityState(entity, state);
  const unit = entity.attributes.unit_of_measurement;
  return unit && state === entity.state ? `${state} ${unit}` : state;
}

/** An attribute's value as Home Assistant shows it: a light effect's translated name, say. */
export function attributeLabel(
  hass: HomeAssistant | undefined,
  entity: HassEntity,
  attribute: string,
  value: unknown = entity.attributes[attribute],
): string {
  return hass?.formatEntityAttributeValue?.(entity, attribute, value) ?? String(value ?? "");
}

export function isUntested(device: DeviceDescriptor, role: string): boolean {
  return device.untested.includes(role);
}

/** Options of a select, without the `Off` placeholder the integration adds. */
export function selectChoices(entity: HassEntity | undefined): string[] {
  const options: unknown = entity?.attributes.options;
  return Array.isArray(options) ? options.filter((o) => o !== "Off").map(String) : [];
}
