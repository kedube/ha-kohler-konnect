import { css, html, nothing, type TemplateResult } from "lit";
import { icon } from "../art/icons";
import "../components/toggle";
import { define } from "../components/define";
import { choiceEditor, numberEditor } from "../components/editors";
import { banner, bannerStyles, group, row, rowStyles } from "../components/rows";
import { DeviceView, errorMessage } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { isUntested, numericState, stateLabel } from "../util/entity";
import { formatClock, formatTemperature, formatWeekdays } from "../util/format";
import { showerPhase } from "../util/status";

/**
 * A device's settings, grouped as the app groups them. Each value opens in place; outlet
 * settings stay read-only while water runs, as the Konnect app requires.
 */
export class KkpSettings extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    open: { state: true },
    draft: { state: true },
  };

  declare open: string | null;
  declare draft: number | null;

  constructor() {
    super();
    this.open = null;
    this.draft = null;
  }

  static override styles = [
    shared,
    rowStyles,
    bannerStyles,
    css`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .lock {
        margin-top: 8px;
      }
    `,
  ];

  private toggleOpen(key: string, initial: number | null = null) {
    this.open = this.open === key ? null : key;
    this.draft = initial;
  }

  private navigate(sub: string) {
    this.ctx.navigate(`/d/${this.device.id}/${sub}`);
  }

  /** Close the open editor, with focus back on the row that opened it. */
  private async closeEditor(role: string) {
    this.open = null;
    await this.updateComplete;
    this.renderRoot.querySelector<HTMLElement>(`[data-key="${role}"]`)?.focus();
  }

  private async selectOption(role: string, option: string) {
    const entityId = this.entityId(role);
    if (!entityId) return;
    void this.closeEditor(role);
    await this.queued(role, "select", "select_option", { entity_id: entityId, option });
  }

  private async saveNumber(role: string, value: number) {
    const entityId = this.entityId(role);
    if (!entityId) return;
    if (role === "max_temperature") {
      const current = this.number(role);
      if (current === null || value > current) {
        const unit = String(this.entity(role)?.attributes.unit_of_measurement ?? this.device.units.temperature);
        const confirmed = await this.ctx.confirm({
          title: t("settings.scald_title"),
          body: t("settings.scald_body", { temperature: formatTemperature(value, unit) }),
          confirmLabel: t("settings.scald_confirm"),
          destructive: true,
        });
        if (!confirmed) return;
      }
    }
    void this.closeEditor(role);
    await this.queued(role, "number", "set_value", { entity_id: entityId, value });
  }

  private async restart() {
    const confirmed = await this.ctx.confirm({
      title: t("settings.restart_title", { name: this.device.name }),
      body: t("settings.restart_body"),
      confirmLabel: t("settings.restart_confirm"),
      destructive: true,
    });
    if (!confirmed) return;
    try {
      await this.ctx.api.restartValve(this.device.id);
      this.ctx.toast(t("settings.restart_done"));
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  private async setAutoRestore(enabled: boolean) {
    try {
      await this.ctx.api.setWarmupAutoRestore(this.device.id, enabled);
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  /** A select as a row that opens to radio choices. */
  private selectRow(role: string, label: string, locked = false): TemplateResult | typeof nothing {
    const entity = this.entity(role);
    if (!entity) return nothing;
    const options = Array.isArray(entity.attributes.options) ? (entity.attributes.options as string[]) : [];
    const open = this.open === role;
    return html`${row({
      label,
      value: stateLabel(this.hass, entity),
      chevron: true,
      expanded: open,
      disabled: locked,
      tag: isUntested(this.device, role) ? t("status.untested") : undefined,
      key: role,
      onClick: () => this.toggleOpen(role),
    })}
      ${open
        ? choiceEditor({
            label,
            choices: options,
            current: entity.state,
            display: (option) => stateLabel(this.hass, entity, option),
            onPick: (option) => void this.selectOption(role, option),
          })
        : nothing}`;
  }

  /**
   * Warm-up as the switch it is in practice. On, it warms all outlets unless the owner
   * picks the started ones; the valve's legacy delayed-start modes show while held but
   * can't be chosen, as the integration refuses to write them.
   */
  private warmupRows(): TemplateResult | typeof nothing {
    const entity = this.entity("warmup");
    if (!entity) return nothing;
    const modes = (Array.isArray(entity.attributes.options) ? (entity.attributes.options as string[]) : []).filter(
      (option) => option !== "Off",
    );
    const on = entity.state !== "Off";
    const turnOn = modes.includes("All Outlets") ? "All Outlets" : modes[0];
    return html`${row({
      label: t("settings.warmup"),
      description: t("settings.warmup_body"),
      trailing: html`<kkp-toggle
        .checked=${on}
        ?disabled=${!turnOn}
        label=${t("settings.warmup")}
        @change=${(event: CustomEvent<boolean>) => this.selectOption("warmup", event.detail ? turnOn : "Off")}
      ></kkp-toggle>`,
    })}
      ${on && modes.length > 1
        ? choiceEditor({
            label: t("settings.warmup_outlets"),
            choices: modes,
            current: entity.state,
            display: (option) => option,
            onPick: (option) => void (option !== entity.state && this.selectOption("warmup", option)),
          })
        : nothing}`;
  }

  /** A number as a row that opens to a slider with Save. */
  private numberRow(
    role: string,
    label: string,
    format: (value: number | null, unit: string) => string,
    locked = false,
  ): TemplateResult | typeof nothing {
    const entity = this.entity(role);
    if (!entity) return nothing;
    const value = numericState(entity);
    const open = this.open === role;
    const isTemperature = role.includes("temperature");
    // Home Assistant shows the entity in its own unit system, which can differ from the
    // account's; values and edits use the entity's unit throughout.
    const unit = String(entity.attributes.unit_of_measurement ?? "");
    return html`${row({
      label,
      value: format(value, unit),
      chevron: true,
      expanded: open,
      disabled: locked,
      tag: isUntested(this.device, role) ? t("status.untested") : undefined,
      key: role,
      onClick: () => this.toggleOpen(role, value),
    })}
      ${open
        ? numberEditor({
            label,
            value,
            draft: this.draft,
            min: Number(entity.attributes.min ?? 0),
            max: Number(entity.attributes.max ?? 100),
            step: Number(entity.attributes.step ?? 1),
            unit,
            variant: isTemperature ? "temperature" : "flow",
            onDraft: (next) => (this.draft = next),
            onCancel: () => void this.closeEditor(role),
            onSave: (next) => void this.saveNumber(role, next),
          })
        : nothing}`;
  }

  private generalGroup(includeRestart: boolean) {
    return group(
      t("settings.general"),
      html`
        ${this.ctx.admin ? row({ label: t("settings.about"), chevron: true, onClick: () => this.navigate("about") }) : nothing}
        ${this.device.capabilities.usage
          ? row({ label: t("settings.water_usage"), chevron: true, onClick: () => this.navigate("usage") })
          : nothing}
        ${this.ctx.admin ? row({ label: t("settings.diagnostics"), chevron: true, onClick: () => this.navigate("diagnostics") }) : nothing}
        ${includeRestart && this.ctx.admin
          ? row({ label: t("settings.restart"), destructive: true, onClick: () => this.restart() })
          : nothing}
      `,
    );
  }

  private renderValve() {
    const phase = showerPhase(this.hass, this.device);
    const running = phase !== "idle" && phase !== "offline";
    const temperature = (value: number | null, unit: string) =>
      formatTemperature(value, unit || this.device.units.temperature);
    const defaults = this.live?.default_shower;
    const defaultNames = defaults?.zones
      .flatMap((z) =>
        z.outlets.map(
          (p) => this.device.zones.find((d) => d.zone === z.zone)?.outlets.find((o) => o.position === p)?.name,
        ),
      )
      .filter(Boolean)
      .join(", ");
    const autoRestore = this.device.capabilities.warmup_auto_restore;
    const flowRoles = Object.keys(this.device.roles).filter((role) => role.startsWith("flow:"));
    return html`
      ${running ? html`<div class="lock">${banner("info", t("settings.locked_running"))}</div>` : nothing}
      ${group(
        t("settings.defaults"),
        html`
          ${this.numberRow("default_temperature", t("settings.default_temperature"), temperature, running)}
          ${row({
            label: t("settings.default_outlets"),
            value: defaultNames || "—",
            chevron: true,
            expanded: this.open === "default_outlets",
            onClick: () => this.toggleOpen("default_outlets"),
          })}
          ${this.open === "default_outlets"
            ? html`<div class="editor muted">${t("settings.default_outlets_body")}</div>`
            : nothing}
        `,
        t("settings.defaults_body"),
      )}
      ${group(
        t("settings.device_control"),
        html`
          ${this.warmupRows()}
          ${autoRestore !== undefined
            ? row({
                label: t("settings.warmup_auto_restore"),
                description: t("settings.warmup_auto_restore_body"),
                trailing: html`<kkp-toggle
                  .checked=${this.has("warmup_auto_restore") ? this.isOn("warmup_auto_restore") : Boolean(autoRestore)}
                  label=${t("settings.warmup_auto_restore")}
                  @change=${(event: CustomEvent<boolean>) => this.setAutoRestore(event.detail)}
                ></kkp-toggle>`,
              })
            : nothing}
          ${this.numberRow("max_temperature", t("settings.max_temperature"), temperature, running)}
          ${this.selectRow("max_duration", t("settings.max_duration"), running)}
          ${flowRoles.map((role) =>
            this.numberRow(
              role,
              flowRoles.length > 1 ? `${t("settings.flow")} ${role.split(":")[1]}` : t("settings.flow"),
              (v) => (v === null ? "—" : `${Math.round(v)}%`),
            ),
          )}
        `,
      )}
      ${this.renderTouchscreen(running)}
      ${this.generalGroup(true)}
    `;
  }

  /** The app's Settings → Control Panel: every touch panel's shared settings at once. */
  private renderTouchscreen(running: boolean) {
    const roles = [
      "panel_language",
      "panel_units",
      "wake_screen",
      "display_brightness",
      "haptic_feedback",
      "accessible_display",
      "flow_control",
      "water_saving_mode",
    ];
    if (!roles.some((role) => this.has(role))) return nothing;
    const level = (value: number | null) => (value === null ? "—" : `${Math.round(value)}`);
    const saving = this.entity("water_saving_mode");
    return group(
      t("settings.touchscreen"),
      html`
        ${this.selectRow("panel_language", t("settings.panel_language"), running)}
        ${this.selectRow("panel_units", t("settings.panel_units"), running)}
        ${this.selectRow("wake_screen", t("settings.wake_screen"), running)}
        ${this.numberRow("display_brightness", t("settings.display_brightness"), level, running)}
        ${this.switchRow("haptic_feedback", t("settings.haptic_feedback"), undefined, running)}
        ${this.switchRow("accessible_display", t("settings.accessible_display"), t("settings.accessible_display_body"), running)}
        ${this.switchRow("flow_control", t("settings.flow_control"), t("settings.flow_control_body"), running)}
        ${saving
          ? row({
              label: t("settings.water_saving_mode"),
              description: t("settings.water_saving_mode_body"),
              value: stateLabel(this.hass, saving),
            })
          : nothing}
      `,
      t("settings.touchscreen_body"),
    );
  }

  private renderFaucet() {
    return html`
      ${group(
        t("settings.dispense_defaults"),
        html`
          ${this.numberRow("dispense_amount", t("settings.dispense_amount"), (v, unit) =>
            v === null ? "—" : `${v} ${unit || this.device.units.dispense || ""}`.trim(),
          )}
          ${this.selectRow("preset", t("settings.preset"))}
          ${row({
            label: t("settings.safety_limit"),
            description: t("settings.safety_limit_body"),
            chevron: true,
            onClick: () => this.ctx.navigate("/account"),
          })}
        `,
      )}
      ${this.generalGroup(false)}
    `;
  }

  private renderController() {
    const webUrl = this.device.capabilities.web_url as string | undefined;
    const duration = this.entity("max_duration");
    return html`
      ${group(
        t("settings.device_control"),
        html`
          ${duration ? row({ label: t("settings.max_duration"), value: `${duration.state} min` }) : nothing}
          ${webUrl
            ? row({
                label: t("settings.web_settings"),
                trailing: icon("chevronRight"),
                onClick: () => window.open(webUrl, "_blank", "noreferrer"),
              })
            : nothing}
        `,
      )}
      ${this.generalGroup(false)}
    `;
  }

  /** A switch as a row with a toggle. */
  private switchRow(role: string, label: string, description?: string, locked = false) {
    const entity = this.entity(role);
    if (!entity) return nothing;
    return row({
      label,
      description,
      tag: isUntested(this.device, role) ? t("status.untested") : undefined,
      trailing: html`<kkp-toggle
        .checked=${entity.state === "on"}
        ?disabled=${locked || entity.state === "unavailable"}
        label=${label}
        @change=${(event: CustomEvent<boolean>) =>
          this.roleService(role, "switch", event.detail ? "turn_on" : "turn_off")}
      ></kkp-toggle>`,
    });
  }

  /** Whether a role's entity is there to use: the app hides a setting that waits on another. */
  private usable(role: string): boolean {
    const entity = this.entity(role);
    return !!entity && entity.state !== "unavailable";
  }

  /** The fan schedule, as it is saved: its times, its days and its speed. */
  private scheduleSummary(): string {
    const schedule = this.entity("fan_schedule");
    const { start, end, repeat, days, speed } = schedule?.attributes ?? {};
    if (typeof start !== "string" || typeof end !== "string") return t("smart_fan.schedule_body");
    const when =
      repeat === "custom" && Array.isArray(days)
        ? formatWeekdays(days as string[])
        : t(repeat === "once" ? "smart_fan.once" : "smart_fan.every_day");
    const speeds = this.entity("default_fan_speed");
    const pace = typeof speed === "string" && speeds ? stateLabel(this.hass, speeds, speed) : null;
    return [`${formatClock(start)}–${formatClock(end)}`, when, pace].filter(Boolean).join(" · ");
  }

  private renderSmartFan() {
    const fan = this.entity("fan_state")?.state ?? "off";
    const fanOn = fan !== "off" && fan !== "unknown" && fan !== "unavailable";
    const lightOn = this.isOn("light_state");
    // Audible Feedback waits for the night light too, as in the app.
    const anyOn = fanOn || lightOn || this.isOn("night_light");
    const roles = [
      "default_fan_speed",
      "default_fan_timer",
      "default_fan_run_time",
      "default_light_color",
      "default_light_brightness",
      "circadian_lighting",
      "audible_feedback",
      "humidity_sensing",
      "humidity_sensitivity",
      "fan_schedule",
    ];
    const minutes = (value: number | null) => (value === null ? "—" : t("account.minutes", { count: value }));
    const percent = (value: number | null) => (value === null ? "—" : `${value}%`);
    return html`
      ${anyOn ? banner("info", t("smart_fan.locked_running")) : nothing}
      ${roles.some((role) => this.has(role))
        ? html`${group(
            t("settings.defaults"),
            html`
              ${this.selectRow("default_fan_speed", t("smart_fan.fan_speed"), fanOn)}
              ${this.switchRow("default_fan_timer", t("smart_fan.fan_timer"), t("smart_fan.fan_timer_body"), fanOn)}
              ${this.usable("default_fan_run_time")
                ? this.numberRow("default_fan_run_time", t("smart_fan.fan_run_time"), minutes, fanOn)
                : nothing}
              ${this.usable("default_light_color")
                ? this.selectRow("default_light_color", t("smart_fan.light_color"), lightOn)
                : nothing}
              ${this.numberRow("default_light_brightness", t("smart_fan.light_brightness"), percent, lightOn)}
              ${this.switchRow("circadian_lighting", t("smart_fan.circadian"), t("smart_fan.circadian_body"), lightOn)}
              ${this.switchRow(
                "audible_feedback",
                t("smart_fan.audible_feedback"),
                t("smart_fan.audible_feedback_body"),
                anyOn,
              )}
            `,
            t("smart_fan.defaults_body"),
          )}
          ${group(
            t("smart_fan.automation"),
            html`
              ${this.switchRow("humidity_sensing", t("smart_fan.humidity"), t("smart_fan.humidity_body"), fanOn)}
              ${this.usable("humidity_sensitivity")
                ? this.selectRow("humidity_sensitivity", t("smart_fan.sensitivity"), fanOn)
                : nothing}
              ${this.switchRow("fan_schedule", t("smart_fan.schedule"), this.scheduleSummary(), fanOn)}
            `,
          )}`
        : banner("info", t("generic.untested_body"))}
      ${this.generalGroup(false)}
    `;
  }

  override render() {
    if (this.device.kind === "valve") return this.renderValve();
    if (this.device.kind === "faucet") return this.renderFaucet();
    if (this.device.kind === "controller") return this.renderController();
    if (this.device.kind === "smart_fan") return this.renderSmartFan();
    return this.generalGroup(false);
  }
}

define("kkp-settings", KkpSettings);
