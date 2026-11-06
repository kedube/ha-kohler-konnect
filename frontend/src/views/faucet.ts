import { css, html, nothing } from "lit";
import { icon } from "../art/icons";
import "../components/faucet-ring";
import "../components/wheel";
import { define } from "../components/define";
import { banner, bannerStyles } from "../components/rows";
import { DeviceView } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import {
  DEFAULT_AMOUNT_INDEX,
  DISPENSE_UNITS,
  dispenseAmounts,
  dispenseUnitName,
  formatAsked,
  formatDispenseAmount,
  type DispenseUnit,
} from "../util/dispense";
import { formatCountdown, formatNumber } from "../util/format";

interface Preset {
  name: string;
  amount: number;
  unit: string;
  /** As saved in the app, when known. */
  asked_amount?: number;
  asked_unit?: string;
}

/**
 * A Sensate faucet, laid out as the app's faucet screen: the faucet in a ring, Start, and
 * the Faucet / Dispense Amount modes, the latter with the app's amount and unit wheels, and
 * presets under See more. Water goes on only through the Water switch, which arms the
 * integration's safety auto-off.
 */
export class KkpFaucet extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    wide: { type: Boolean },
    mode: { state: true },
    pickedUnit: { state: true },
    pickedAmounts: { state: true },
    sheetOpen: { state: true },
    now: { state: true },
  };

  declare wide: boolean;
  declare mode: "faucet" | "dispense";
  /** The unit wheel's choice; null until there is one. */
  declare pickedUnit: DispenseUnit | null;
  /** Each unit wheel's position, once moved. Each unit keeps its own, as in the app. */
  declare pickedAmounts: Partial<Record<DispenseUnit, number>>;
  declare sheetOpen: boolean;
  declare now: number;

  private ticker?: number;

  constructor() {
    super();
    this.mode = "faucet";
    this.pickedUnit = null;
    this.pickedAmounts = {};
    this.sheetOpen = false;
    this.now = Date.now();
  }

  override connectedCallback(): void {
    super.connectedCallback();
    this.ticker = window.setInterval(() => (this.now = Date.now()), 1000);
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    window.clearInterval(this.ticker);
  }

  static override styles = [
    shared,
    bannerStyles,
    css`
      :host {
        display: block;
      }

      .layout {
        display: grid;
        gap: 24px;
      }

      :host([wide]) .layout {
        grid-template-columns: minmax(0, 1.1fr) minmax(320px, 1fr);
        align-items: start;
        gap: 32px;
      }

      .stage {
        display: grid;
        justify-items: center;
        gap: 26px;
        padding: 18px 0 8px;
      }

      .status {
        min-height: 22px;
        margin-top: -6px;
        font-size: 16px;
        color: var(--kk-text-2);
        text-align: center;
      }

      .status.active {
        color: var(--kk-accent-text);
      }

      .start {
        min-width: 150px;
        min-height: 50px;
        font-size: 20px;
      }

      .modes {
        display: flex;
        justify-content: center;
        gap: 56px;
      }

      .mode {
        display: grid;
        justify-items: center;
        gap: 12px;
        width: 120px;
        font-size: 18px;
        color: var(--kk-text-2);
      }

      .mode .circle {
        display: grid;
        place-items: center;
        width: 76px;
        height: 76px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-text);
        transition: background 220ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      .mode .circle svg {
        width: 34px;
        height: 34px;
      }

      .mode[aria-pressed="true"] {
        color: var(--kk-text);
      }

      .mode[aria-pressed="true"] .circle {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .mode:active .circle {
        transform: scale(0.95);
      }

      .amounts {
        display: grid;
        grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
        gap: 12px;
        width: 100%;
        padding: 12px;
      }

      .sheet {
        border-radius: 22px 22px var(--kk-radius-card) var(--kk-radius-card);
        background: linear-gradient(180deg, var(--kk-sheet), var(--kk-surface) 140px);
        box-shadow: var(--kk-shadow);
        overflow: hidden;
      }

      .sheet-toggle {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        width: 100%;
        padding: 18px;
        font-size: 19px;
        color: var(--kk-text-2);
        border-top: 1px solid var(--kk-accent-line);
      }

      /* Inside, since the sheet clips anything drawn beyond its edges. */
      .sheet-toggle:focus-visible {
        outline-offset: -3px;
      }

      .sheet-toggle svg {
        width: 22px;
        height: 22px;
        transition: transform 240ms var(--kk-ease);
        transform: rotate(180deg);
      }

      .sheet-toggle[aria-expanded="true"] svg {
        transform: none;
      }

      .presets-head {
        margin: 0;
        padding: 6px 20px 10px;
        font-size: 18px;
        font-weight: 400;
      }

      .preset {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 16px 20px;
        border-top: 1px solid var(--kk-divider);
      }

      .preset-name {
        font-size: 18px;
      }

      .preset-amount {
        display: block;
        font-size: 14px;
        color: var(--kk-text-2);
        margin-top: 2px;
      }

      .preset .pill {
        min-height: 40px;
        padding: 0 22px;
      }

      .empty {
        padding: 18px 20px 24px;
        color: var(--kk-text-2);
      }

      .banners {
        display: grid;
        gap: 10px;
      }

      :host([wide]) .sheet-toggle {
        display: none;
      }
    `,
  ];

  private get presets(): Preset[] {
    const raw = this.device.capabilities.presets;
    return Array.isArray(raw) ? (raw as Preset[]) : [];
  }

  /** The app opens on Milliliters on any account; a US one opens on Cups here. */
  private get unit(): DispenseUnit {
    return this.pickedUnit ?? (this.device.units.volume === "L" ? "ml" : "cup");
  }

  private get amountIndex(): number {
    return this.pickedAmounts[this.unit] ?? DEFAULT_AMOUNT_INDEX;
  }

  private start() {
    if (this.mode === "faucet") {
      const entityId = this.entityId("water");
      if (entityId) void this.queued("water", "switch", "turn_on", { entity_id: entityId });
      return;
    }
    void this.queued("water", "kohler_konnect", "dispense", {
      device_id: this.device.id,
      amount: dispenseAmounts(this.unit)[this.amountIndex],
      unit: this.unit,
    });
  }

  private stop() {
    const entityId = this.entityId("water");
    if (entityId) void this.stopNow("switch", "turn_off", { entity_id: entityId });
  }

  private pour(preset: Preset) {
    void this.queued("water", "kohler_konnect", "dispense", { device_id: this.device.id, preset: preset.name });
  }

  private renderStatus(running: boolean, dispensing: boolean) {
    if (dispensing) {
      const preset = this.entity("dispensing")?.attributes.preset;
      return html`<div class="status active">${preset ?? t("status.dispensing")}</div>`;
    }
    if (running) {
      const deadline = this.live?.auto_off_at ? Date.parse(this.live.auto_off_at) : NaN;
      const left = Number.isFinite(deadline) ? Math.max(0, (deadline - this.now) / 1000) : null;
      return html`<div class="status active">
        ${left !== null ? t("faucet.auto_off", { time: formatCountdown(left) }) : t("status.water_on")}
      </div>`;
    }
    const last = this.entity("last_dispensed");
    return html`<div class="status">
      ${last && last.state !== "unknown" && last.state !== "unavailable"
        ? t("faucet.last_dispensed", {
            amount: formatAsked(last.attributes) ?? `${last.state} ${last.attributes.unit_of_measurement ?? ""}`.trim(),
          })
        : nothing}
    </div>`;
  }

  private renderBanners() {
    const items = [];
    if (this.isOn("leak")) {
      items.push(
        banner("danger", t("faucet.leak"), {
          label: t("faucet.clear_leak"),
          onClick: () => void this.roleService("clear_leak", "button", "press"),
        }),
      );
    }
    if (this.entity("handle")?.state === "closed") items.push(banner("info", t("faucet.handle_closed")));
    if (this.entity("firmware")?.attributes.in_progress) {
      items.push(banner("warning", t("faucet.firmware_downloading")));
    }
    return items.length ? html`<div class="banners">${items}</div>` : nothing;
  }

  private renderAmounts() {
    const unit = this.unit;
    return html`<div class="card amounts" role="group" aria-label=${t("faucet.dispense_amount")}>
      <kkp-wheel
        label=${t("faucet.amount")}
        .options=${dispenseAmounts(unit).map(formatDispenseAmount)}
        .index=${this.amountIndex}
        @change=${(event: CustomEvent<number>) =>
          (this.pickedAmounts = { ...this.pickedAmounts, [unit]: event.detail })}
      ></kkp-wheel>
      <kkp-wheel
        label=${t("faucet.unit")}
        .options=${DISPENSE_UNITS.map(dispenseUnitName)}
        .index=${DISPENSE_UNITS.indexOf(unit)}
        @change=${(event: CustomEvent<number>) => (this.pickedUnit = DISPENSE_UNITS[event.detail])}
      ></kkp-wheel>
    </div>`;
  }

  private renderPresets() {
    return html`
      <h2 class="presets-head">${t("faucet.presets")}</h2>
      ${this.presets.length
        ? this.presets.map(
            (preset, index) => html`<div class="preset">
              <div id="preset-${index}">
                <span class="preset-name">${preset.name}</span>
                <span class="preset-amount">
                  ${formatAsked(preset) ?? `${formatNumber(preset.amount, 2)} ${preset.unit}`}
                </span>
              </div>
              <button class="pill" aria-describedby="preset-${index}" @click=${() => this.pour(preset)}>
                ${t("faucet.start")}
              </button>
            </div>`,
          )
        : html`<div class="empty">${t("faucet.no_presets")}</div>`}
    `;
  }

  override render() {
    const dispensing = this.isOn("dispensing");
    const running = dispensing || this.isOn("water") || this.isOn("status");
    const offline = !this.has("water");
    const main = html`<div class="stage">
      ${this.renderBanners()}
      <kkp-faucet-ring .flow=${dispensing ? "dispensing" : running ? "on" : "off"}></kkp-faucet-ring>
      ${this.renderStatus(running, dispensing)}
      <button class="pill start" ?disabled=${!running && offline} @click=${running ? this.stop : this.start}>
        ${running ? html`${icon("stop")}${t("faucet.stop")}` : t("faucet.start")}
      </button>
      <div class="modes">
        <button class="mode" aria-pressed=${String(this.mode === "faucet")} @click=${() => (this.mode = "faucet")}>
          <span class="circle">${icon("faucet")}</span>${t("faucet.faucet")}
        </button>
        <button class="mode" aria-pressed=${String(this.mode === "dispense")} @click=${() => (this.mode = "dispense")}>
          <span class="circle">${icon("cup")}</span>${t("faucet.dispense_amount")}
        </button>
      </div>
      ${this.mode === "dispense" ? this.renderAmounts() : nothing}
    </div>`;

    const status = this.statusRegion(
      dispensing ? t("status.dispensing") : running ? t("status.water_on") : t("status.off"),
    );
    if (this.wide) {
      return html`${status}<div class="layout">${main}<div class="sheet">${this.renderPresets()}</div></div>`;
    }
    return html`${status}<div class="layout">
      ${main}
      <div class="sheet">
        <button class="sheet-toggle" aria-expanded=${String(this.sheetOpen)} @click=${() => (this.sheetOpen = !this.sheetOpen)}>
          ${icon("chevronDown")}${this.sheetOpen ? t("faucet.see_less") : t("faucet.see_more")}
        </button>
        ${this.sheetOpen ? this.renderPresets() : nothing}
      </div>
    </div>`;
  }
}

define("kkp-faucet", KkpFaucet);
