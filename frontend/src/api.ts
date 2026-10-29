import type {
  AccountOptions,
  DeviceDetails,
  DeviceLive,
  DevicesMessage,
  HomeAssistant,
  UsageHistory,
  UsagePeriod,
} from "./types";
import { CommandQueue } from "./util/queue";

const PREFIX = "kohler_konnect";

type Unsubscribe = () => Promise<void>;

/** The integration's websocket commands and Home Assistant's services, in one place. */
export class KonnectApi {
  private readonly queues = new Map<string, CommandQueue>();

  constructor(private readonly hass: () => HomeAssistant) {}

  subscribeDevices(callback: (message: DevicesMessage) => void): Promise<Unsubscribe> {
    return this.hass().connection.subscribeMessage(callback, {
      type: `${PREFIX}/subscribe_devices`,
    });
  }

  subscribeDevice(deviceId: string, callback: (live: DeviceLive) => void): Promise<Unsubscribe> {
    return this.hass().connection.subscribeMessage(callback, {
      type: `${PREFIX}/subscribe_device`,
      device_id: deviceId,
    });
  }

  /** ``weekStart`` counts as Python does: Monday 0 to Sunday 6. */
  usageHistory(
    deviceId: string,
    period: UsagePeriod,
    weekStart: number,
    anchor?: string,
  ): Promise<UsageHistory> {
    return this.hass().callWS({
      type: `${PREFIX}/usage_history`,
      device_id: deviceId,
      period,
      week_start: weekStart,
      ...(anchor ? { anchor } : {}),
    });
  }

  deviceDetails(deviceId: string): Promise<DeviceDetails> {
    return this.hass().callWS({ type: `${PREFIX}/device_details`, device_id: deviceId });
  }

  restartValve(deviceId: string): Promise<void> {
    return this.hass().callWS({ type: `${PREFIX}/restart_valve`, device_id: deviceId });
  }

  setWarmupAutoRestore(deviceId: string, enabled: boolean): Promise<void> {
    return this.hass().callWS({
      type: `${PREFIX}/set_warmup_auto_restore`,
      device_id: deviceId,
      enabled,
    });
  }

  getOptions(entryId: string): Promise<AccountOptions> {
    return this.hass().callWS({ type: `${PREFIX}/get_options`, entry_id: entryId });
  }

  updateOptions(
    entryId: string,
    changes: Record<string, unknown>,
  ): Promise<{ reloads: boolean; values: Record<string, unknown> }> {
    return this.hass().callWS({
      type: `${PREFIX}/update_options`,
      entry_id: entryId,
      changes,
    });
  }

  async signPath(path: string): Promise<string> {
    const result = await this.hass().callWS<{ path: string }>({ type: "auth/sign_path", path });
    return result.path;
  }

  service(
    domain: string,
    service: string,
    data?: Record<string, unknown>,
    target?: Record<string, unknown>,
  ): Promise<unknown> {
    return this.hass().callService(domain, service, data, target);
  }

  /** Each device's commands go one at a time; see `CommandQueue`. */
  queue(deviceId: string): CommandQueue {
    let queue = this.queues.get(deviceId);
    if (!queue) {
      queue = new CommandQueue();
      this.queues.set(deviceId, queue);
    }
    return queue;
  }
}
