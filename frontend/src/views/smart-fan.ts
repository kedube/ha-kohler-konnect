import { css, html, nothing } from "lit";
import { icon } from "../art/icons";
import { productArt } from "../art/products";
import { define } from "../components/define";
import { banner, bannerStyles } from "../components/rows";
import { DeviceView } from "../context";
import { t } from "../i18n";
import { artStyles } from "../theme/art";
import { shared } from "../theme/shared";
import type { HassEntity } from "../types";
import { isAvailable, stateLabel } from "../util/entity";

const IDLE = new Set(["off", "unavailable", "unknown"]);

/**
 * The Smart Fan: the fan and its light as the app's home screen has them, each started as
 * its stored defaults or as one of its favorites, and Stop all while either runs. A
 * control someone has turned off leaves only what that part is doing.
 */
export class KkpSmartFan extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
  };

  static override styles = [
    shared,
    bannerStyles,
    artStyles,
    css`
      :host {
        display: grid;
        gap: 24px;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .hero {
        display: grid;
        justify-items: center;
        gap: 12px;
        padding-top: 8px;
      }

      .art-box {
        width: 168px;
        height: 168px;
      }

      .now {
        font-size: 20px;
        color: var(--kk-text-2);
        text-align: center;
      }

      .parts {
        display: flex;
        justify-content: center;
        gap: 56px;
      }

      .part {
        display: grid;
        justify-items: center;
        gap: 10px;
        width: 128px;
        text-align: center;
      }

      .part .circle {
        display: grid;
        place-items: center;
        width: 76px;
        height: 76px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-text);
        transition: background 220ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      .part .circle svg {
        width: 34px;
        height: 34px;
      }

      button.part[aria-pressed="true"] .circle {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      button.part:active .circle {
        transform: scale(0.95);
      }

      .part .name {
        font-size: 18px;
      }

      .part .detail {
        min-height: 18px;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .favorites {
        display: grid;
        gap: 10px;
      }

      .favorites h2 {
        margin: 0;
        font-size: 17px;
        font-weight: 500;
      }

      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .chip {
        padding: 10px 16px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        font-size: 15px;
      }

      .chip[aria-pressed="true"] {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .actions {
        display: flex;
        justify-content: center;
      }
    `,
  ];

  private get fanRunning(): boolean {
    return !IDLE.has(this.entity("fan_state")?.state ?? "off");
  }

  private get lightOn(): boolean {
    return this.isOn("light_state");
  }

  /** What the fan is doing: its speed, and the favorite running it. */
  private fanDetail(): string {
    const reading = this.entity("fan_state");
    if (!reading || !isAvailable(reading)) return "—";
    const favorite = reading.attributes.favorite as string | null | undefined;
    return [stateLabel(this.hass, reading), favorite].filter(Boolean).join(" · ");
  }

  /** What the light is doing: off, or its brightness and the favorite running it. */
  private lightDetail(): string {
    const reading = this.entity("light_state");
    if (!reading || !isAvailable(reading)) return "—";
    if (reading.state !== "on") return t("status.off");
    const brightness = reading.attributes.brightness as number | null | undefined;
    const favorite = reading.attributes.favorite as string | null | undefined;
    return [brightness ? `${brightness}%` : t("smart_fan.on"), favorite].filter(Boolean).join(" · ");
  }

  private now(): string {
    const parts = [];
    if (this.fanRunning) parts.push(t("smart_fan.fan_on"));
    if (this.lightOn) parts.push(t("smart_fan.light_on"));
    return parts.length ? parts.join(" · ") : t("status.off");
  }

  private renderPart(role: "fan" | "light", label: string, detail: string, on: boolean) {
    const control = this.entity(role);
    const glyph = icon(role === "fan" ? "fan" : "light");
    if (!control || !isAvailable(control)) {
      // A reading only: the control is off until enabled, or the fan is offline.
      return html`<div class="part">
        <span class="circle" aria-hidden="true">${glyph}</span>
        <span class="name">${label}</span><span class="detail">${detail}</span>
      </div>`;
    }
    return html`<button
      class="part"
      aria-pressed=${String(on)}
      @click=${() => this.roleService(role, role, on ? "turn_off" : "turn_on")}
    >
      <span class="circle">${glyph}</span>
      <span class="name">${label}</span><span class="detail">${detail}</span>
    </button>`;
  }

  private favorites(role: "fan" | "light", control: HassEntity | undefined) {
    if (!control || !isAvailable(control)) return nothing;
    const fan = role === "fan";
    const names = (control.attributes[fan ? "preset_modes" : "effect_list"] as string[] | undefined) ?? [];
    if (!names.length) return nothing;
    const active = control.state === "on" ? control.attributes[fan ? "preset_mode" : "effect"] : null;
    return html`<section class="favorites">
      <h2>${t(fan ? "smart_fan.fan_favorites" : "smart_fan.light_favorites")}</h2>
      <div class="chips">
        ${names.map(
          (name) => html`<button
            class="chip"
            aria-pressed=${String(name === active)}
            @click=${() =>
              name === active
                ? this.roleService(role, role, "turn_off")
                : fan
                  ? this.roleService("fan", "fan", "set_preset_mode", { preset_mode: name })
                  : this.roleService("light", "light", "turn_on", { effect: name })}
          >
            ${name}
          </button>`,
        )}
      </div>
    </section>`;
  }

  protected override focusFallback(): HTMLElement | null {
    return this.renderRoot.querySelector<HTMLElement>("button.part");
  }

  override render() {
    const hasLight = this.has("light_state") || this.has("light");
    const anyControl = ["fan", "light", "stop_all"].some((role) => this.has(role));
    const running = this.fanRunning || this.lightOn;
    return html`
      ${this.statusRegion(this.now())}
      <div class="hero ${this.fanRunning ? "running" : ""}">
        <div class="art-box">${productArt("smart_fan")}</div>
        <div class="now">${this.now()}</div>
      </div>
      ${!anyControl && this.device.untested.length ? banner("info", t("generic.untested_body")) : nothing}
      <div class="parts">
        ${this.renderPart("fan", t("smart_fan.fan"), this.fanDetail(), this.fanRunning)}
        ${hasLight ? this.renderPart("light", t("smart_fan.light"), this.lightDetail(), this.lightOn) : nothing}
      </div>
      ${this.favorites("fan", this.entity("fan"))}
      ${hasLight ? this.favorites("light", this.entity("light")) : nothing}
      ${running && this.has("stop_all")
        ? html`<div class="actions">
            <button class="pill" @click=${() => this.roleService("stop_all", "button", "press")}>
              ${icon("stop")}${t("smart_fan.stop_all")}
            </button>
          </div>`
        : nothing}
    `;
  }
}

define("kkp-smart-fan", KkpSmartFan);
