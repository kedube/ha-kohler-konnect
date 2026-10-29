// The slice of Home Assistant's frontend object the panel uses, and the payloads of the
// integration's websocket commands. The fake Home Assistant in `dev/` implements the same
// shapes, so the panel runs unchanged against either.

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, any>;
  last_changed: string;
  last_updated: string;
}

export interface HassUser {
  id: string;
  name: string;
  is_admin: boolean;
}

export interface HassConnection {
  subscribeMessage<T>(
    callback: (message: T) => void,
    message: Record<string, unknown>,
  ): Promise<() => Promise<void>>;
}

export interface HomeAssistant {
  states: Record<string, HassEntity>;
  /** The entity registry as the frontend has it: here, only whether one is hidden. */
  entities?: Record<string, { hidden?: boolean }>;
  user?: HassUser;
  language: string;
  locale?: { language: string; first_weekday?: string };
  themes?: { darkMode?: boolean };
  connection: HassConnection;
  callWS<T>(message: Record<string, unknown>): Promise<T>;
  /** A state (or one of an entity's options) as Home Assistant shows it, translated. */
  formatEntityState?(stateObj: HassEntity, state?: string): string;
  formatEntityAttributeValue?(stateObj: HassEntity, attribute: string, value?: unknown): string;
  callService(
    domain: string,
    service: string,
    data?: Record<string, unknown>,
    target?: Record<string, unknown>,
  ): Promise<unknown>;
}

export interface PanelInfo {
  config?: { version?: string };
}

export interface Route {
  prefix: string;
  path: string;
}

export type DeviceKind = "valve" | "controller" | "faucet" | (string & {});

export interface OutletInfo {
  position: number;
  /** The valve's fixture code (`OUTLET_TYPE_NAMES`), or null until the valve reports it. */
  code: number | null;
  name: string;
  variant: string | null;
}

export interface ZoneInfo {
  zone: number;
  outlets: OutletInfo[];
}

export interface QuickAmount {
  key: string;
  amount: number;
  unit: string;
  label: string;
}

export interface Units {
  temperature: string;
  volume: string;
  /** The faucet's dispense unit: `mL` or `fl oz`. */
  dispense?: string;
}

export interface DeviceDescriptor {
  /** Home Assistant's device id. Kohler's own ids never reach the browser. */
  id: string;
  entry_id: string;
  kind: DeviceKind;
  name: string;
  area: string | null;
  model: string | null;
  model_name: string | null;
  units: Units;
  zones: ZoneInfo[];
  capabilities: Record<string, unknown>;
  /** Role → entity id, for entities that are enabled. */
  roles: Record<string, string>;
  /** Role → entity id, for entities the owner (or a default) has disabled. */
  disabled_roles: Record<string, string>;
  /** Roles whose control has not been confirmed on real hardware. */
  untested: string[];
  quick_amounts?: QuickAmount[];
}

export interface EntryInfo {
  entry_id: string;
  /** The entry's title, which carries the account email. Sent to admins only. */
  title: string | null;
  sidebar_panel: boolean;
  /** Whether the account's push stream to Kohler is up; null before setup has finished. */
  stream_connected: boolean | null;
}

export interface DevicesMessage {
  entries: EntryInfo[];
  devices: DeviceDescriptor[];
}

export interface DefaultShowerZone {
  zone: number;
  outlets: number[];
  temperature: number | null;
}

export interface DeviceLive {
  device_id: string;
  stream_connected: boolean | null;
  cloud_connected: boolean | null;
  last_update: string | null;
  default_shower?: { zones: DefaultShowerZone[]; refused_because: string | null };
  /** Per zone: what a staged temperature may be, in the account's unit. */
  staging_bounds?: { zone: number; min: number; max: number }[];
  /** When the faucet's water safety limit turns its water off. */
  auto_off_at?: string | null;
}

export type UsagePeriod = "week" | "month" | "year";

export interface UsageBucket {
  key: string;
  volume: number | null;
  minutes: number | null;
  uses: number | null;
}

export interface UsageFigures {
  volume: number | null;
  minutes: number | null;
}

export interface UsageHistory {
  period: UsagePeriod;
  anchor: string;
  start: string;
  end: string;
  unit: string;
  buckets: UsageBucket[];
  total: UsageFigures;
  average: UsageFigures;
  high: UsageFigures;
  previous: string | null;
  next: string | null;
}

export interface DevicePart {
  kind: string;
  name: string | null;
  model: string | null;
  serial: string | null;
  firmware: string | null;
  status: string | null;
  install_date: string | null;
}

export interface DeviceFault {
  code: string | null;
  title: string | null;
  area: string | null;
  time: string | null;
}

export interface DeviceDetails {
  device_id: string;
  model: string | null;
  model_name: string | null;
  serial: string | null;
  install_date: string | null;
  registered: string | null;
  hardware: string | null;
  /** Each part's firmware; `key` names the part (see `about.firmware.*`). */
  firmware: { key: string; version: string | null }[];
  parts: DevicePart[];
  faults: DeviceFault[];
  leak_history: string[];
  web_url: string | null;
  diagnostics_path: string | null;
}

export interface OptionField {
  key: string;
  kind: "select" | "minutes" | "toggle";
  options?: string[];
  min?: number;
  max?: number;
  /** Saving a change to this field reloads the integration, as Configure does. */
  reloads: boolean;
}

export interface AccountOptions {
  entry_id: string;
  title: string | null;
  values: Record<string, unknown>;
  fields: OptionField[];
}
