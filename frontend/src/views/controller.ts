import { css, html, nothing } from "lit";
import { icon, type UiIcon } from "../art/icons";
import { tintFraction } from "../components/running-hero";
import { define } from "../components/define";
import { bannerStyles, banner } from "../components/rows";
import { DeviceView } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { numericState } from "../util/entity";
import { formatTemperature } from "../util/format";
import { showerPhase, showerPhaseText } from "../util/status";
import "./scenes";

/**
 * An Anthem Plus controller. It runs whole scenes — its default shower, favorites,
 * experiences, steam — and reports zones read-only; outlets belong to the valve.
 */
export class KkpController extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    tab: {},
    wide: { type: Boolean },
  };

  declare tab: "controls" | "favorites" | "experiences";
  declare wide: boolean;

  static override styles = [
    shared,
    bannerStyles,
    css`
      :host {
        display: block;
      }

      .layout {
        display: grid;
        gap: 18px;
        align-items: start;
      }

      :host([wide]) .layout {
        grid-template-columns: minmax(0, 1.25fr) minmax(320px, 1fr);
        gap: 28px;
      }

      .col {
        display: grid;
        gap: 18px;
      }

      .simple-start {
        display: flex;
        align-items: center;
        gap: 20px;
        width: 100%;
        min-height: 84px;
        padding: 16px 24px;
        border-radius: var(--kk-radius-card);
        border: 1.5px solid var(--kk-accent-line);
        background: var(--kk-surface-raised);
        text-align: left;
        font-size: 26px;
      }

      .simple-start svg {
        width: 34px;
        height: 34px;
        color: var(--kk-accent-fill);
      }

      .panel {
        display: grid;
        gap: 18px;
        padding: 22px 24px;
      }

      .panel-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }

      .zones {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 12px;
      }

      .zone {
        display: grid;
        gap: 10px;
        padding: 16px;
        border-radius: var(--kk-radius-tile);
        background: var(--kk-control);
      }

      .zone strong {
        font-size: 30px;
        font-weight: 300;
      }

      .dots {
        display: flex;
        gap: 6px;
      }

      .dots i {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--kk-outlet);
        /* An edge, so an off dot shows against the tile by more than its fill. */
        box-shadow: inset 0 0 0 1.5px var(--kk-text-2);
      }

      .dots i.on {
        background: var(--kk-accent);
        box-shadow: none;
      }

      .accessories {
        display: flex;
        gap: 12px;
      }

      .acc {
        display: grid;
        justify-items: center;
        gap: 8px;
        flex: 1;
        padding: 14px 8px;
        border-radius: var(--kk-radius-tile);
        background: var(--kk-control);
        color: var(--kk-text-2);
        font-size: 14px;
      }

      .acc svg {
        width: 26px;
        height: 26px;
      }

      .acc.on {
        background: var(--kk-accent-soft);
        color: var(--kk-accent-text);
        font-weight: 500;
      }

      .steam-meta {
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .note {
        margin: 0;
        font-size: 14px;
        color: var(--kk-text-3);
      }

      .side h2 {
        margin: 6px 4px 2px;
        font-size: 18px;
        font-weight: 400;
        color: var(--kk-text-2);
      }
    `,
  ];

  protected override focusFallback(): HTMLElement | null {
    return this.renderRoot.querySelector<HTMLElement>("kkp-running, .simple-start");
  }

  private toggle(role: string) {
    const entityId = this.entityId(role);
    if (!entityId) return;
    const on = this.isOn(role);
    if (on) void this.stopNow("switch", "turn_off", { entity_id: entityId });
    else void this.queued(role, "switch", "turn_on", { entity_id: entityId });
  }

  private renderTop() {
    const phase = showerPhase(this.hass, this.device);
    if (phase === "idle" || phase === "offline") {
      return html`<button
        class="simple-start"
        ?disabled=${phase === "offline" || !this.has("shower")}
        @click=${() => this.toggle("shower")}
      >
        ${icon("play")}<span>${t("shower.simple_start")}</span>
      </button>`;
    }
    const sensor = this.entity("temperature:1");
    const temp = numericState(sensor);
    const unit = String(sensor?.attributes.unit_of_measurement ?? this.device.units.temperature);
    return html`<kkp-running
      .phase=${phase === "paused" ? "running" : phase}
      .temperature=${temp}
      .setpoint=${temp}
      .unit=${unit}
      .tint=${tintFraction(temp, unit)}
      .atTemperature=${false}
      .secondsRemaining=${null}
      @kkp-stop=${() => this.toggle("shower")}
    ></kkp-running>`;
  }

  private renderSteam() {
    if (!this.has("steam")) return nothing;
    const steaming = this.isOn("steam");
    const showerOn = this.isOn("shower");
    const steamState = this.entity("steam_state");
    const temperature = steamState?.attributes.temperature;
    return html`<section class="card panel">
      <div class="panel-head">
        <h2 class="section-label">${t("controller.steam")}</h2>
        <button class="pill ${steaming ? "" : "filled"}" ?disabled=${!steaming && showerOn} @click=${() => this.toggle("steam")}>
          ${steaming ? t("controller.steam_stop") : t("controller.steam_start")}
        </button>
      </div>
      ${steaming && temperature !== undefined
        ? // STEAM_STS reports whole °F whatever the account's unit (protocol/hub_controller.md).
          html`<div class="steam-meta">${formatTemperature(Number(temperature), "°F")}</div>`
        : nothing}
      ${!steaming && showerOn ? html`<p class="note">${t("controller.steam_blocked")}</p>` : nothing}
    </section>`;
  }

  private renderStatus() {
    const accessories: [string, UiIcon, string][] = [
      ["music", "music", t("controller.music")],
      ["light", "light", t("controller.light")],
      ["steam_state", "cloud", t("controller.steam")],
    ];
    return html`<section class="card panel">
      <h2 class="section-label">${t("controller.status")}</h2>
      <div class="zones">
        ${this.device.zones.map(
          (zone) => html`<div class="zone">
            <span class="muted">${t("shower.zone", { zone: zone.zone })}</span>
            <strong>${formatTemperature(
              this.number(`temperature:${zone.zone}`),
              String(this.entity(`temperature:${zone.zone}`)?.attributes.unit_of_measurement ?? this.device.units.temperature),
            )}</strong>
            <div class="dots">
              ${zone.outlets.map((o) => {
                const on = this.isOn(`outlet:${zone.zone}:${o.position}`);
                return html`<i
                  class=${on ? "on" : ""}
                  role="img"
                  aria-label=${`${o.name}: ${on ? t("status.on") : t("status.off")}`}
                  title=${o.name}
                ></i>`;
              })}
            </div>
          </div>`,
        )}
      </div>
      <div class="accessories">
        ${accessories
          .filter(([role]) => this.has(role))
          .map(
            ([role, glyph, label]) => html`<div class="acc ${this.isOn(role) ? "on" : ""}">
              ${icon(glyph)}<span>${label}</span>
              <span class="visually-hidden">${this.isOn(role) ? t("status.on") : t("status.off")}</span>
            </div>`,
          )}
      </div>
      <p class="note">${t("controller.read_only")}</p>
    </section>`;
  }

  private scenes(role: "favorite" | "experience", compact = false) {
    return html`<kkp-scenes
      .hass=${this.hass}
      .device=${this.device}
      .live=${this.live}
      .ctx=${this.ctx}
      .role=${role}
      ?compact=${compact}
    ></kkp-scenes>`;
  }

  override render() {
    return html`${this.statusRegion(showerPhaseText(showerPhase(this.hass, this.device)))}${this.renderTab()}`;
  }

  private renderTab() {
    const problem = this.isOn("problem");
    const controls = html`
      ${problem ? banner("warning", this.entity("problem")?.attributes.active_errors?.[0]?.title ?? t("status.problem")) : nothing}
      ${this.renderTop()} ${this.renderSteam()} ${this.renderStatus()}
    `;
    if (this.wide) {
      return html`<div class="layout">
        <div class="col">${controls}</div>
        <div class="col side">
          <h2>${t("nav.favorites")}</h2>${this.scenes("favorite", true)}
          ${this.has("experience") ? html`<h2>${t("nav.experiences")}</h2>${this.scenes("experience", true)}` : nothing}
        </div>
      </div>`;
    }
    if (this.tab === "favorites") return this.scenes("favorite");
    if (this.tab === "experiences") return this.scenes("experience");
    return html`<div class="layout"><div class="col">${controls}</div></div>`;
  }
}

define("kkp-controller", KkpController);
