import { css, html, LitElement, nothing } from "lit";
import { icon } from "../art/icons";
import { productArt } from "../art/products";
import "../components/tabs";
import { define } from "../components/define";
import type { PanelContext } from "../context";
import { t } from "../i18n";
import { artStyles } from "../theme/art";
import { shared } from "../theme/shared";
import type { DeviceDescriptor, DevicesMessage, HomeAssistant } from "../types";
import { deviceStatus } from "../util/status";
import "./account";
import "./home-usage";

const HELP_URL = "https://github.com/kedube/ha-kohler-konnect/blob/main/docs/user_guide.md";

/** Home: a greeting, Devices / Usage / Account, and the line-art device grid by room. */
export class KkpHome extends LitElement {
  static override properties = {
    hass: { attribute: false },
    data: { attribute: false },
    ctx: { attribute: false },
    tab: {},
    narrow: { type: Boolean },
  };

  declare hass: HomeAssistant;
  declare data: DevicesMessage;
  declare ctx: PanelContext;
  declare tab: "devices" | "usage" | "account";
  declare narrow: boolean;

  static override styles = [
    shared,
    artStyles,
    css`
      :host {
        display: block;
      }

      .intro {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        padding: 28px 4px 26px;
      }

      .menu {
        margin: -4px 12px 0 -4px;
      }

      h1 {
        margin: 0;
        font-size: clamp(28px, 4vw, 36px);
        font-weight: 300;
        letter-spacing: -0.01em;
      }

      .summary {
        margin-top: 8px;
        font-size: 16px;
        color: var(--kk-text-2);
      }

      .summary .live {
        color: var(--kk-accent-text);
      }

      .summary .warn {
        color: var(--kk-danger);
      }

      .help {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 15px;
        color: var(--kk-text);
        text-decoration: none;
        white-space: nowrap;
      }

      .help svg {
        width: 26px;
        height: 26px;
      }

      :host([narrow]) .help span {
        display: none;
      }

      .area {
        margin: 34px 4px 14px;
      }

      /* Hairlines between tiles, as on the app's home grid: each tile draws its right and
         bottom edge, and the grid clips the last column's right edge. */
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 210px), 1fr));
        border-top: 1px solid var(--kk-divider);
        overflow: hidden;
      }

      :host([narrow]) .grid {
        grid-template-columns: repeat(2, 1fr);
        margin: 0 -16px;
      }

      .tile {
        position: relative;
        display: grid;
        justify-items: center;
        align-content: start;
        gap: 14px;
        margin-right: -1px;
        padding: 34px 16px 30px;
        border-right: 1px solid var(--kk-divider);
        border-bottom: 1px solid var(--kk-divider);
        background: var(--kk-canvas);
        text-align: center;
        transition: background 200ms var(--kk-ease);
      }

      .tile:hover {
        background: var(--kk-surface);
      }

      /* Inside the tile: its neighbours paint over anything drawn beyond its edges. */
      .tile:focus-visible {
        outline-offset: -3px;
      }

      /* Wide screens trade the phone grid's hairlines for cards, which hold together
         when a room has only one or two devices. */
      :host([wide]) .grid {
        grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
        gap: 18px;
        border-top: none;
        overflow: visible;
      }

      :host([wide]) .tile {
        margin-right: 0;
        padding: 40px 20px 34px;
        border: none;
        border-radius: 20px;
        background: var(--kk-surface);
        box-shadow: var(--kk-shadow);
        transition: transform 220ms var(--kk-ease), background 220ms var(--kk-ease);
      }

      :host([wide]) .tile:hover {
        transform: translateY(-3px);
        background: var(--kk-surface-raised);
      }

      :host([wide]) .art-box {
        width: 150px;
        height: 150px;
      }

      .art-box {
        position: relative;
        width: 132px;
        height: 132px;
      }

      .art-box::before {
        content: "";
        position: absolute;
        inset: 6%;
        border-radius: 50%;
        background: radial-gradient(circle, var(--kk-accent-soft), transparent 68%);
        opacity: 0;
        transition: opacity 500ms var(--kk-ease);
      }

      .running .art-box::before {
        opacity: 1;
        animation: kk-breathe 4.8s ease-in-out infinite;
      }

      .name {
        font-size: 19px;
      }

      .status {
        min-height: 20px;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .status.active {
        color: var(--kk-accent-text);
      }

      .status.alert {
        color: var(--kk-danger);
      }

      .status.offline {
        color: var(--kk-text-3);
      }

      .badge {
        position: absolute;
        top: 16px;
        right: 16px;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--kk-danger);
        box-shadow: 0 0 0 4px color-mix(in srgb, var(--kk-danger) 20%, transparent);
      }

      /* Controls not yet seen to work on hardware: said quietly, in the corner. */
      .untested {
        position: absolute;
        top: 12px;
        left: 12px;
        padding: 2px 8px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        font-size: 11px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--kk-text-2);
      }

      .empty {
        display: grid;
        justify-items: center;
        gap: 10px;
        padding: 80px 24px;
        text-align: center;
        color: var(--kk-text-2);
      }

      .empty strong {
        font-size: 20px;
        font-weight: 400;
        color: var(--kk-text);
      }

      .tab-body {
        padding-bottom: 48px;
      }

      @keyframes kk-breathe {
        50% {
          transform: scale(1.1);
        }
      }
    `,
  ];

  private open(device: DeviceDescriptor) {
    this.ctx.navigate(`/d/${device.id}`);
  }

  private renderTile(device: DeviceDescriptor) {
    const status = deviceStatus(this.hass, device);
    const untested = device.untested.length > 0;
    // Named by the device alone, with its status as the description: a name that changed
    // with every temperature report would be read out again while the tile has focus.
    return html`<button
      class="tile ${status.running ? "running" : ""}"
      aria-label=${untested ? `${device.name}, ${t("status.untested")}` : device.name}
      aria-describedby="status-${device.id}"
      @click=${() => this.open(device)}
    >
      ${status.tone === "alert" ? html`<span class="badge" aria-hidden="true"></span>` : nothing}
      ${untested ? html`<span class="untested">${t("status.untested")}</span>` : nothing}
      <span class="art-box">${productArt(device.kind)}</span>
      <span class="name">${device.name}</span>
      <span class="status ${status.tone}" id="status-${device.id}">${status.text}</span>
    </button>`;
  }

  private renderDevices() {
    const devices = this.data.devices;
    if (!devices.length) {
      return html`<div class="empty">
        <strong>${t("app.no_devices")}</strong>
        <span>${t("app.no_devices_body")}</span>
      </div>`;
    }
    const areas = new Map<string, DeviceDescriptor[]>();
    for (const device of devices) {
      const area = device.area ?? t("home.other_area");
      areas.set(area, [...(areas.get(area) ?? []), device]);
    }
    const single = areas.size === 1;
    return [...areas.entries()].map(
      ([area, list]) => html`
        ${single ? html`<div style="height: 8px"></div>` : html`<h2 class="area eyebrow">${area}</h2>`}
        <div class="grid">${list.map((device) => this.renderTile(device))}</div>
      `,
    );
  }

  private renderSummary() {
    const statuses = this.data.devices.map((d) => deviceStatus(this.hass, d));
    const running = statuses.filter((s) => s.running).length;
    const alerts = statuses.filter((s) => s.tone === "alert").length;
    if (alerts) {
      return html`<span class="warn">${t("home.summary_alert", { count: alerts })}</span>`;
    }
    if (running) return html`<span class="live">${t("home.summary_running", { count: running })}</span>`;
    return html`${t("home.summary_quiet")}`;
  }

  override render() {
    const name = this.hass.user?.name?.split(" ")[0];
    const tabs = [
      { key: "devices", label: t("nav.devices") },
      { key: "usage", label: t("nav.usage") },
      ...(this.ctx.admin ? [{ key: "account", label: t("nav.account") }] : []),
    ];
    return html`
      <div class="intro">
        <div style="display: flex; align-items: flex-start;">
          ${this.narrow
            ? html`<button
                class="round menu"
                aria-label=${t("nav.menu")}
                @click=${() =>
                  this.dispatchEvent(new CustomEvent("kkp-menu", { bubbles: true, composed: true }))}
              >
                ${icon("menu")}
              </button>`
            : nothing}
          <div>
            <h1 tabindex="-1">${name ? t("home.welcome", { name }) : t("home.welcome_anonymous")}</h1>
            <div class="summary">${this.renderSummary()}</div>
          </div>
        </div>
        <a class="help" href=${HELP_URL} target="_blank" rel="noreferrer" aria-label=${t("home.help")}>
          ${icon("help")}<span>${t("home.help")}</span>
        </a>
      </div>
      <kkp-tabs
        .items=${tabs}
        .active=${this.tab}
        @kkp-select=${(event: CustomEvent<string>) =>
          this.ctx.navigate(event.detail === "devices" ? "/" : `/${event.detail}`)}
      ></kkp-tabs>
      <div class="tab-body" role="tabpanel" aria-label=${tabs.find((tab) => tab.key === this.tab)?.label ?? ""}>
        ${this.tab === "usage"
          ? html`<kkp-home-usage .hass=${this.hass} .data=${this.data} .ctx=${this.ctx}></kkp-home-usage>`
          : this.tab === "account"
            ? html`<kkp-account .hass=${this.hass} .data=${this.data} .ctx=${this.ctx}></kkp-account>`
            : this.renderDevices()}
      </div>
    `;
  }
}

define("kkp-home", KkpHome);
