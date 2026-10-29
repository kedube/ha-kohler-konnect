import { css, html, nothing, type PropertyValues } from "lit";
import { fixtureKind, icon } from "../art/icons";
import "../components/outlet-button";
import { tintFraction } from "../components/running-hero";
import "../components/slider";
import { define } from "../components/define";
import { banner, bannerStyles } from "../components/rows";
import { DeviceView } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import type { OutletInfo } from "../types";
import { isOn, numericState, selectChoices } from "../util/entity";
import { formatTemperature } from "../util/format";
import {
  canStart,
  customShowerData,
  defaultStage,
  hasStagedOutlets,
  setStagedTemperature,
  toggleStagedOutlet,
  type StagedZone,
} from "../util/staging";
import { showerPhase, showerPhaseText } from "../util/status";
import "./scenes";

/**
 * An Anthem valve. Idle, outlets and temperature are staged and sent as one
 * `custom_shower` on Start; while water runs, every change goes live through the queue.
 */
export class KkpShower extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    tab: {},
    wide: { type: Boolean },
    stage: { state: true },
    stagedFlow: { state: true },
  };

  declare tab: "controls" | "favorites" | "experiences";
  declare wide: boolean;
  declare stage: StagedZone[] | null;
  declare stagedFlow: Record<number, number>;

  private touched = false;
  private lastStarted: StagedZone[] | null = null;

  constructor() {
    super();
    this.stage = null;
    this.stagedFlow = {};
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
        gap: 20px;
        align-items: start;
      }

      :host([wide]) .layout {
        grid-template-columns: minmax(0, 1.25fr) minmax(320px, 1fr);
        gap: 28px;
      }

      .main,
      .side {
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
        box-shadow: var(--kk-shadow);
        text-align: left;
        transition: border-color 200ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      .simple-start:hover:not(:disabled) {
        border-color: var(--kk-accent);
      }

      .simple-start:active:not(:disabled) {
        transform: scale(0.99);
      }

      .simple-start:disabled {
        opacity: 0.5;
      }

      .simple-start .play svg {
        width: 34px;
        height: 34px;
        color: var(--kk-accent-fill);
      }

      .ss-title {
        display: block;
        font-size: 26px;
        font-weight: 400;
      }

      .ss-sub {
        display: block;
        margin-top: 4px;
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .zone {
        display: grid;
        gap: 22px;
        padding: 24px;
      }

      .zone-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }

      .outlets {
        display: grid;
        gap: 12px;
        grid-template-columns: repeat(var(--count), minmax(0, 1fr));
      }

      .hint {
        margin: -6px 0 0;
        font-size: 14px;
        color: var(--kk-text-3);
        text-align: center;
      }

      .lock-note {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        margin: -6px 0 0;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .lock-note svg {
        width: 16px;
        height: 16px;
      }

      .divider {
        height: 1px;
        background: var(--kk-divider);
      }

      .start-bar {
        position: sticky;
        bottom: 104px;
        display: flex;
        justify-content: center;
        z-index: 1;
        pointer-events: none;
      }

      :host([wide]) .start-bar {
        bottom: 24px;
      }

      .start-bar .pill {
        min-width: 220px;
        min-height: 56px;
        font-size: 19px;
        pointer-events: auto;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
        animation: kk-rise-in 320ms var(--kk-ease);
      }

      .side h2 {
        margin: 6px 4px 2px;
        font-size: 18px;
        font-weight: 400;
        color: var(--kk-text-2);
      }

      @keyframes kk-rise-in {
        from {
          opacity: 0;
          transform: translateY(12px);
        }
      }
    `,
  ];

  private get phase() {
    return showerPhase(this.hass, this.device);
  }

  protected override willUpdate(changed: PropertyValues): void {
    super.willUpdate(changed);
    const idle = this.phase === "idle";
    if (!idle) {
      this.stage = null;
      this.touched = false;
      return;
    }
    // Follow the valve's default shower until the owner changes something.
    if (!this.stage || !this.touched) this.stage = defaultStage(this.device, this.live, this.hass);
  }

  protected override focusFallback(): HTMLElement | null {
    return this.renderRoot.querySelector<HTMLElement>("kkp-running, .simple-start");
  }

  private defaultSummary(): string {
    const fallback = this.live?.default_shower;
    if (!fallback) return "";
    if (fallback.refused_because) return t("shower.simple_start_unknown");
    const names = fallback.zones.flatMap((z) =>
      z.outlets.map(
        (p) => this.device.zones.find((dz) => dz.zone === z.zone)?.outlets.find((o) => o.position === p)?.name,
      ),
    );
    const temperature = fallback.zones[0]?.temperature ?? null;
    return [names.filter(Boolean).join(", "), formatTemperature(temperature, this.device.units.temperature)]
      .filter((part) => part && part !== "—")
      .join(" · ");
  }

  private simpleStart() {
    const entityId = this.entityId("shower");
    if (entityId) void this.queued("shower", "switch", "turn_on", { entity_id: entityId });
  }

  private stop() {
    const entityId = this.entityId("shower");
    if (entityId) void this.stopNow("switch", "turn_off", { entity_id: entityId });
  }

  private sendShower(stage: StagedZone[]) {
    const flow = {
      zone1: this.stagedFlow[1] ?? this.number("flow:1"),
      zone2: this.stagedFlow[2] ?? this.number("flow:2"),
    };
    this.lastStarted = stage;
    void this.queued("shower", "kohler_konnect", "custom_shower", customShowerData(this.device.id, stage, flow));
  }

  private resume() {
    const fromSwitches = this.device.zones.map((z) => ({
      zone: z.zone,
      outlets: z.outlets.filter((o) => this.isOn(`outlet:${z.zone}:${o.position}`)).map((o) => o.position),
      temperature: this.number(`temperature:${z.zone}`),
    }));
    const stage =
      this.lastStarted ??
      (hasStagedOutlets(fromSwitches) ? fromSwitches : defaultStage(this.device, this.live, this.hass));
    this.sendShower(stage);
  }

  private toggleOutlet(zone: number, outlet: OutletInfo) {
    if (this.phase === "idle") {
      this.touched = true;
      this.stage = toggleStagedOutlet(this.stage ?? [], zone, outlet.position);
      return;
    }
    const role = `outlet:${zone}:${outlet.position}`;
    const entityId = this.entityId(role);
    if (!entityId) return;
    void this.queued(role, "switch", this.isOn(role) ? "turn_off" : "turn_on", { entity_id: entityId });
  }

  private setTemperature(zone: number, value: number) {
    if (this.phase === "idle") {
      this.touched = true;
      this.stage = setStagedTemperature(this.stage ?? [], zone, value, this.live);
      return;
    }
    const entityId = this.entityId(`temperature:${zone}`);
    if (entityId) void this.queued(`temperature:${zone}`, "number", "set_value", { entity_id: entityId, value });
  }

  private setFlow(zone: number, value: number) {
    if (this.phase === "idle") {
      this.stagedFlow = { ...this.stagedFlow, [zone]: value };
      return;
    }
    const entityId = this.entityId(`flow:${zone}`);
    if (entityId) void this.queued(`flow:${zone}`, "number", "set_value", { entity_id: entityId, value });
  }

  private renderZone(zone: { zone: number; outlets: OutletInfo[] }, multiZone: boolean) {
    const phase = this.phase;
    const idle = phase === "idle";
    const warming = phase === "warming";
    const staged = this.stage?.find((s) => s.zone === zone.zone);
    const tempEntity = this.entity(`temperature:${zone.zone}`);
    const bounds = this.live?.staging_bounds?.find((b) => b.zone === zone.zone);
    const minAttr = Number(tempEntity?.attributes.min ?? 58);
    const maxAttr = Number(tempEntity?.attributes.max ?? 118);
    const flowEntity = this.entity(`flow:${zone.zone}`);
    const showFlow = !!flowEntity && flowEntity.attributes.flow_control_available !== false;
    const temperature = idle ? (staged?.temperature ?? null) : numericState(tempEntity);
    // Staged values are in the account's unit; the live number in Home Assistant's own.
    const unit = idle
      ? this.device.units.temperature
      : String(tempEntity?.attributes.unit_of_measurement ?? this.device.units.temperature);
    // Named by its heading, so two zones' "Temperature" sliders and outlets tell apart.
    return html`<section class="card zone" aria-labelledby="zone-${zone.zone}">
      <div class="zone-head">
        <h2 class="section-label" id="zone-${zone.zone}">
          ${multiZone ? t("shower.zone", { zone: zone.zone }) : t("shower.outlets")}
        </h2>
      </div>
      <div class="outlets" style="--count: ${Math.max(zone.outlets.length, 1)}">
        ${zone.outlets.map((outlet) => {
          const role = `outlet:${zone.zone}:${outlet.position}`;
          const state = idle
            ? staged?.outlets.includes(outlet.position)
              ? "staged"
              : "off"
            : isOn(this.entity(role))
              ? "on"
              : "off";
          return html`<kkp-outlet
            .name=${outlet.name}
            .kind=${fixtureKind(outlet.code, outlet.name)}
            .variant=${outlet.variant}
            .state=${state}
            ?locked=${warming}
            ?disabled=${phase === "offline" || (!idle && !this.has(role))}
            @kkp-toggle=${() => this.toggleOutlet(zone.zone, outlet)}
          ></kkp-outlet>`;
        })}
      </div>
      ${warming
        ? html`<p class="lock-note">${icon("lock")}${t("shower.locked_warming")}</p>`
        : idle && !staged?.outlets.length
          ? html`<p class="hint">${t("shower.staged_hint")}</p>`
          : nothing}
      <div class="divider"></div>
      ${tempEntity || idle
        ? html`<kkp-slider
            label=${t("shower.temperature")}
            .value=${temperature}
            .min=${idle ? (bounds?.min ?? (unit === "°C" ? 15 : 59)) : minAttr}
            .max=${idle ? (bounds?.max ?? (unit === "°C" ? 47 : 118)) : maxAttr}
            .step=${Number(tempEntity?.attributes.step ?? 1)}
            unit=${unit}
            .coldValue=${idle ? null : minAttr}
            ?disabled=${warming || phase === "offline"}
            @change=${(event: CustomEvent<number>) => this.setTemperature(zone.zone, event.detail)}
          ></kkp-slider>`
        : nothing}
      ${showFlow
        ? html`<kkp-slider
            compact
            variant="flow"
            label=${t("shower.flow")}
            .value=${idle ? (this.stagedFlow[zone.zone] ?? numericState(flowEntity)) : numericState(flowEntity)}
            .min=${Number(flowEntity!.attributes.min ?? 8)}
            .max=${Number(flowEntity!.attributes.max ?? 100)}
            .step=${1}
            ?disabled=${warming || phase === "offline"}
            @change=${(event: CustomEvent<number>) => this.setFlow(zone.zone, event.detail)}
          ></kkp-slider>`
        : nothing}
    </section>`;
  }

  private renderHero() {
    const phase = this.phase;
    if (phase === "idle" || phase === "offline") {
      const summary = this.defaultSummary();
      return html`<button
        class="simple-start"
        ?disabled=${phase === "offline" || !this.has("shower")}
        @click=${this.simpleStart}
      >
        <span class="play">${icon("play")}</span>
        <span>
          <span class="ss-title">${t("shower.simple_start")}</span>
          ${summary ? html`<span class="ss-sub">${summary}</span>` : nothing}
        </span>
      </button>`;
    }
    const status = this.entity("status");
    const temperature = this.entity("temperature:1");
    const setpoint = numericState(temperature);
    const unit = String(temperature?.attributes.unit_of_measurement ?? this.device.units.temperature);
    const duration = parseInt(this.entity("max_duration")?.state ?? "", 10);
    const remaining = status?.attributes.seconds_remaining;
    return html`<kkp-running
      .phase=${phase}
      .temperature=${setpoint}
      .setpoint=${setpoint}
      .unit=${unit}
      .tint=${tintFraction(setpoint, unit)}
      .atTemperature=${this.isOn("at_temperature")}
      .secondsRemaining=${typeof remaining === "number" ? remaining : null}
      .totalSeconds=${Number.isFinite(duration) ? duration * 60 : null}
      @kkp-stop=${this.stop}
      @kkp-resume=${this.resume}
    ></kkp-running>`;
  }

  private renderControls() {
    const multiZone = this.device.zones.length > 1;
    const idle = this.phase === "idle";
    const stage = this.stage ?? [];
    return html`
      ${this.phase === "offline" ? banner("warning", t("status.offline")) : nothing}
      ${this.renderHero()}
      ${this.device.zones.map((zone) => this.renderZone(zone, multiZone))}
      ${idle && hasStagedOutlets(stage)
        ? html`<div class="start-bar">
            <button class="pill filled" ?disabled=${!canStart(stage)} @click=${() => this.sendShower(stage)}>
              ${icon("play")}${t("shower.start")}
            </button>
          </div>`
        : nothing}
    `;
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
    return html`${this.statusRegion(showerPhaseText(this.phase))}${this.renderTab()}`;
  }

  private renderTab() {
    if (this.wide) {
      const hasFavorites = this.has("favorite");
      const hasExperiences = selectChoices(this.entity("experience")).length > 0;
      return html`<div class="layout">
        <div class="main">${this.renderControls()}</div>
        <div class="side">
          ${hasFavorites ? html`<h2>${t("nav.favorites")}</h2>${this.scenes("favorite", true)}` : nothing}
          ${hasExperiences ? html`<h2>${t("nav.experiences")}</h2>${this.scenes("experience", true)}` : nothing}
        </div>
      </div>`;
    }
    if (this.tab === "favorites") return this.scenes("favorite");
    if (this.tab === "experiences") return this.scenes("experience");
    return html`<div class="layout"><div class="main">${this.renderControls()}</div></div>`;
  }
}

define("kkp-shower", KkpShower);
