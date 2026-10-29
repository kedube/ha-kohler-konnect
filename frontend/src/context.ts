import { html, LitElement, type PropertyValues } from "lit";
import type { KonnectApi } from "./api";
import type { DeviceDescriptor, DeviceLive, HassEntity, HomeAssistant } from "./types";
import { entityFor, isOn, numericState } from "./util/entity";
import { Superseded } from "./util/queue";

export interface ConfirmOptions {
  title: string;
  body: string;
  confirmLabel: string;
  destructive?: boolean;
}

/** What every view needs from the panel, passed down rather than looked up. */
export interface PanelContext {
  api: KonnectApi;
  admin: boolean;
  wide: boolean;
  navigate(path: string): void;
  toast(message: string, kind?: "info" | "error"): void;
  confirm(options: ConfirmOptions): Promise<boolean>;
}

export function errorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message: unknown }).message);
  }
  return String(error);
}

/** A view of one device: role lookups and service calls that report their own failures. */
export class DeviceView extends LitElement {
  static override properties = {
    hass: { attribute: false },
    device: { attribute: false },
    live: { attribute: false },
    ctx: { attribute: false },
  };

  declare hass: HomeAssistant;
  declare device: DeviceDescriptor;
  declare live: DeviceLive | undefined;
  declare ctx: PanelContext;

  private hadFocus = false;

  protected override willUpdate(changed: PropertyValues): void {
    super.willUpdate(changed);
    this.hadFocus = this.matches(":focus-within");
  }

  protected override updated(changed: PropertyValues): void {
    super.updated(changed);
    // An update that removed the focused control (Start, once the water runs) would leave
    // focus nowhere, and a keyboard or screen-reader user lost.
    if (this.hadFocus && !this.matches(":focus-within")) this.focusFallback()?.focus();
  }

  /** Where focus goes when an update removes the control that had it. */
  protected focusFallback(): HTMLElement | null {
    return null;
  }

  /** Says when the device changes phase; never its ticking numbers, which would flood it. */
  protected statusRegion(text: string) {
    return html`<div class="visually-hidden" role="status">${text}</div>`;
  }

  protected entity(role: string): HassEntity | undefined {
    return entityFor(this.hass, this.device, role);
  }

  protected has(role: string): boolean {
    return !!this.entity(role);
  }

  protected isOn(role: string): boolean {
    return isOn(this.entity(role));
  }

  protected number(role: string): number | null {
    return numericState(this.entity(role));
  }

  protected entityId(role: string): string | undefined {
    return this.device.roles[role];
  }

  /** Call a service now, reporting a refusal (a closed handle, an offline valve) as a toast. */
  protected async call(
    domain: string,
    service: string,
    data: Record<string, unknown> = {},
  ): Promise<boolean> {
    try {
      await this.ctx.api.service(domain, service, data);
      return true;
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
      return false;
    }
  }

  /** Call through the device's queue, so rapid changes reach the valve one at a time. */
  protected async queued(
    key: string,
    domain: string,
    service: string,
    data: Record<string, unknown>,
  ): Promise<boolean> {
    try {
      await this.ctx.api
        .queue(this.device.id)
        .enqueue(key, () => this.ctx.api.service(domain, service, data));
      return true;
    } catch (error) {
      if (!(error instanceof Superseded)) this.ctx.toast(errorMessage(error), "error");
      return false;
    }
  }

  /** Stop jumps the queue: it must never wait behind a change it is meant to cancel. */
  protected async stopNow(domain: string, service: string, data: Record<string, unknown>) {
    try {
      await this.ctx.api.queue(this.device.id).stop(() => this.ctx.api.service(domain, service, data));
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  protected roleService(role: string, domain: string, service: string, extra = {}) {
    const entityId = this.entityId(role);
    if (!entityId) return Promise.resolve(false);
    return this.call(domain, service, { entity_id: entityId, ...extra });
  }
}
