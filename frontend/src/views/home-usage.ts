import { css, html, LitElement, nothing } from "lit";
import { icon } from "../art/icons";
import { productArt } from "../art/products";
import { define } from "../components/define";
import type { PanelContext } from "../context";
import { t } from "../i18n";
import { artStyles } from "../theme/art";
import { shared } from "../theme/shared";
import type { DeviceDescriptor, DevicesMessage, HomeAssistant } from "../types";
import { entityFor, numericState } from "../util/entity";
import { formatVolume } from "../util/format";

/** Household usage: each device's month, today and last seven days, from its sensors. */
export class KkpHomeUsage extends LitElement {
  static override properties = {
    hass: { attribute: false },
    data: { attribute: false },
    ctx: { attribute: false },
  };

  declare hass: HomeAssistant;
  declare data: DevicesMessage;
  declare ctx: PanelContext;

  static override styles = [
    shared,
    artStyles,
    css`
      :host {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
        gap: 16px;
        padding-top: 28px;
      }

      .card {
        display: grid;
        gap: 18px;
        padding: 22px;
        text-align: left;
        transition: transform 200ms var(--kk-ease);
      }

      .card:hover {
        transform: translateY(-2px);
      }

      .top {
        display: flex;
        align-items: center;
        gap: 14px;
      }

      .thumb {
        width: 48px;
        height: 48px;
        flex: none;
      }

      .name {
        flex: 1;
        font-size: 18px;
      }

      .chev svg {
        width: 22px;
        height: 22px;
        color: var(--kk-text-2);
      }

      .figures {
        display: flex;
        gap: 28px;
      }

      .figure span {
        display: block;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .figure strong {
        font-size: 28px;
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .spark {
        display: flex;
        align-items: flex-end;
        gap: 6px;
        height: 56px;
      }

      .spark i {
        flex: 1;
        border-radius: 4px;
        background: linear-gradient(var(--kk-bar-top), var(--kk-bar-bottom));
        min-height: 3px;
      }

      .spark i.empty {
        background: var(--kk-bar-track);
      }

      .caption {
        font-size: 13px;
        color: var(--kk-text-3);
      }
    `,
  ];

  private renderCard(device: DeviceDescriptor) {
    const month = numericState(entityFor(this.hass, device, "water_month"));
    const today = numericState(entityFor(this.hass, device, "water_today"));
    const perDay = entityFor(this.hass, device, "water_week")?.attributes.per_day as
      | Record<string, number>
      | undefined;
    const days = perDay ? Object.values(perDay).slice(-7) : [];
    const max = Math.max(...days, 0);
    const unit = device.units.volume;
    return html`<button class="card" @click=${() => this.ctx.navigate(`/d/${device.id}/usage`)}>
      <div class="top">
        <span class="thumb">${productArt(device.kind)}</span>
        <span class="name">${device.name}</span>
        <span class="chev">${icon("chevronRight")}</span>
      </div>
      <div class="figures">
        <div class="figure"><span>${t("home.usage_this_month")}</span><strong>${formatVolume(month, unit)}</strong></div>
        <div class="figure"><span>${t("home.usage_today")}</span><strong>${formatVolume(today, unit)}</strong></div>
      </div>
      ${days.length
        ? html`<div>
            <div class="spark" aria-hidden="true">
              ${days.map(
                (v) =>
                  html`<i class=${v ? "" : "empty"} style="height:${max ? Math.max(6, (v / max) * 100) : 6}%"></i>`,
              )}
            </div>
            <div class="caption" aria-hidden="true">${t("home.usage_last_7_days")}</div>
          </div>`
        : nothing}
    </button>`;
  }

  override render() {
    return this.data.devices
      .filter((device) => device.roles.water_month || device.roles.water_today)
      .map((device) => this.renderCard(device));
  }
}

define("kkp-home-usage", KkpHomeUsage);
