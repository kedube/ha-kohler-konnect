import { css, html, nothing } from "lit";
import { productArt } from "../art/products";
import "../components/toggle";
import { define } from "../components/define";
import { choiceEditor, numberEditor } from "../components/editors";
import { banner, bannerStyles, group, row, rowStyles } from "../components/rows";
import { DeviceView } from "../context";
import { t } from "../i18n";
import { artStyles } from "../theme/art";
import { shared } from "../theme/shared";
import type { HassEntity } from "../types";
import { attributeLabel, isUntested, numericState, stateLabel } from "../util/entity";

const CONTROL_DOMAINS = new Set(["switch", "button", "select", "number", "light", "fan"]);
// Roles with a place of their own: the Report Log is on the Diagnostics screen.
const SHOWN_ELSEWHERE = new Set(["report_log"]);
// Read in Home Assistant's own views: a calendar's state is only whether an event is on.
const OTHER_VIEWS = new Set(["calendar"]);
// Presses the app asks about first: an ozone clean runs through the shower itself.
const OZONE = new Set(["start_rapid_clean", "start_deep_clean"]);

/**
 * Any device without a view of its own: its controls and readings as rows, named as Home
 * Assistant names them. New products appear here the moment the integration supports them.
 * A select or number opens in place, as in Settings, so every control can be used here.
 * An entity hidden in Home Assistant stays out; one unavailable shows, but can't be used.
 */
export class KkpGeneric extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    open: { state: true },
    draft: { state: true },
    pressing: { state: true },
  };

  /** The role whose editor is open. */
  declare open: string | null;
  declare draft: number | null;
  /** Buttons pressed and not yet answered: a second tap would press again. */
  declare pressing: ReadonlySet<string>;

  constructor() {
    super();
    this.open = null;
    this.draft = null;
    this.pressing = new Set();
  }

  static override styles = [
    shared,
    rowStyles,
    bannerStyles,
    artStyles,
    css`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .art {
        width: 140px;
        height: 140px;
        margin: 8px auto 4px;
      }
    `,
  ];

  private name(entity: HassEntity): string {
    const friendly = String(entity.attributes.friendly_name ?? entity.entity_id);
    return friendly.startsWith(`${this.device.name} `)
      ? friendly.slice(this.device.name.length + 1)
      : friendly;
  }

  private toggleOpen(role: string, initial: number | null = null) {
    this.open = this.open === role ? null : role;
    this.draft = initial;
  }

  /** Close the open editor, with focus back on the row that opened it. */
  private async closeEditor(role: string) {
    this.open = null;
    await this.updateComplete;
    this.renderRoot.querySelector<HTMLElement>(`[data-key="${role}"]`)?.focus();
  }

  private async pick(role: string, option: string) {
    void this.closeEditor(role);
    const entityId = this.entityId(role);
    if (entityId) await this.queued(role, "select", "select_option", { entity_id: entityId, option });
  }

  private async save(role: string, value: number) {
    void this.closeEditor(role);
    const entityId = this.entityId(role);
    if (entityId) await this.queued(role, "number", "set_value", { entity_id: entityId, value });
  }

  private async press(role: string) {
    if (OZONE.has(role)) {
      const confirmed = await this.ctx.confirm({
        title: t("generic.ozone_title"),
        body: t("generic.ozone_body"),
        confirmLabel: t("generic.ozone_confirm"),
      });
      if (!confirmed) return;
    }
    this.pressing = new Set([...this.pressing, role]);
    try {
      await this.roleService(role, "button", "press");
    } finally {
      this.pressing = new Set([...this.pressing].filter((each) => each !== role));
    }
  }

  /** Turn a light on as asked, from one of its own rows (`key`). */
  private async light(role: string, key: string, data: Record<string, unknown>) {
    void this.closeEditor(key);
    const entityId = this.entityId(role);
    if (entityId) await this.queued(role, "light", "turn_on", { entity_id: entityId, ...data });
  }

  /** A light's brightness and effect, when it has them, as rows under its switch. */
  private lightRows(role: string, light: HassEntity) {
    const name = this.name(light);
    const modes = (light.attributes.supported_color_modes as string[] | undefined) ?? [];
    const effects = (light.attributes.effect_list as string[] | undefined) ?? [];
    const brightness = light.attributes.brightness;
    // Home Assistant's 0–255, as the percentage people think in.
    const percent = light.state === "on" && typeof brightness === "number" ? Math.round((brightness / 255) * 100) : null;
    const dimKey = `${role}:brightness`;
    const effectKey = `${role}:effect`;
    const dims = modes.some((mode) => mode !== "onoff");
    const effect = light.state === "on" ? light.attributes.effect : null;
    return html`
      ${dims
        ? html`${row({
            label: t("generic.brightness", { name }),
            value: percent === null ? "—" : `${percent}%`,
            chevron: true,
            expanded: this.open === dimKey,
            key: dimKey,
            onClick: () => this.toggleOpen(dimKey, percent),
          })}
          ${this.open === dimKey
            ? numberEditor({
                label: t("generic.brightness", { name }),
                value: percent,
                draft: this.draft,
                // A step a dimmer can tell apart: the Numi has ten levels.
                min: 10,
                max: 100,
                step: 10,
                unit: "%",
                variant: "flow",
                onDraft: (next) => (this.draft = next),
                onCancel: () => void this.closeEditor(dimKey),
                onSave: (next) => void this.light(role, dimKey, { brightness_pct: next }),
              })
            : nothing}`
        : nothing}
      ${effects.length
        ? html`${row({
            label: t("generic.effect", { name }),
            value: effect ? attributeLabel(this.hass, light, "effect", effect) : "—",
            chevron: true,
            expanded: this.open === effectKey,
            key: effectKey,
            onClick: () => this.toggleOpen(effectKey),
          })}
          ${this.open === effectKey
            ? choiceEditor({
                label: t("generic.effect", { name }),
                choices: effects,
                current: String(effect ?? ""),
                display: (choice) => attributeLabel(this.hass, light, "effect", choice),
                onPick: (choice) => void this.light(role, effectKey, { effect: choice }),
              })
            : nothing}`
        : nothing}
    `;
  }

  private control(role: string, entity: HassEntity) {
    const domain = entity.entity_id.split(".")[0];
    const tag = isUntested(this.device, role) ? t("status.untested") : undefined;
    const unavailable = entity.state === "unavailable";
    const open = this.open === role && !unavailable;
    if (domain === "select") {
      const options = Array.isArray(entity.attributes.options) ? (entity.attributes.options as string[]) : [];
      return html`${row({
        label: this.name(entity),
        tag,
        value: stateLabel(this.hass, entity),
        chevron: true,
        expanded: open,
        key: role,
        disabled: unavailable,
        onClick: () => this.toggleOpen(role),
      })}
        ${open
          ? choiceEditor({
              label: this.name(entity),
              choices: options,
              current: entity.state,
              display: (option) => stateLabel(this.hass, entity, option),
              onPick: (option) => void this.pick(role, option),
            })
          : nothing}`;
    }
    if (domain === "number") {
      const value = numericState(entity);
      const unit = String(entity.attributes.unit_of_measurement ?? "");
      return html`${row({
        label: this.name(entity),
        tag,
        value: stateLabel(this.hass, entity),
        chevron: true,
        expanded: open,
        key: role,
        disabled: unavailable,
        onClick: () => this.toggleOpen(role, value),
      })}
        ${open
          ? numberEditor({
              label: this.name(entity),
              value,
              draft: this.draft,
              min: Number(entity.attributes.min ?? 0),
              max: Number(entity.attributes.max ?? 100),
              step: Number(entity.attributes.step ?? 1),
              unit,
              variant: entity.attributes.device_class === "temperature" || /°[CF]/.test(unit) ? "temperature" : "flow",
              onDraft: (next) => (this.draft = next),
              onCancel: () => void this.closeEditor(role),
              onSave: (next) => void this.save(role, next),
            })
          : nothing}`;
    }
    if (domain === "switch" || domain === "light" || domain === "fan") {
      const toggle = row({
        label: this.name(entity),
        tag,
        trailing: html`<kkp-toggle
          .checked=${entity.state === "on"}
          ?disabled=${unavailable}
          label=${this.name(entity)}
          @change=${(event: CustomEvent<boolean>) =>
            void this.roleService(role, domain, event.detail ? "turn_on" : "turn_off")}
        ></kkp-toggle>`,
      });
      return domain === "light" ? html`${toggle}${this.lightRows(role, entity)}` : toggle;
    }
    if (domain === "button") {
      return row({
        label: this.name(entity),
        tag,
        trailing: html`<button
          class="pill"
          aria-label=${t("generic.press_named", { name: this.name(entity) })}
          ?disabled=${unavailable || this.pressing.has(role)}
          @click=${() => void this.press(role)}
        >
          ${t("generic.press")}
        </button>`,
      });
    }
    return row({ label: this.name(entity), tag, value: stateLabel(this.hass, entity) });
  }

  override render() {
    const entries = Object.entries(this.device.roles)
      .filter(([role]) => !SHOWN_ELSEWHERE.has(role))
      .map(([role, entityId]) => [role, this.hass.states[entityId]] as const)
      .filter((pair): pair is readonly [string, HassEntity] => !!pair[1])
      .filter(([, e]) => !OTHER_VIEWS.has(e.entity_id.split(".")[0]))
      .filter(([, e]) => !this.hass.entities?.[e.entity_id]?.hidden);
    const controls = entries.filter(([, e]) => CONTROL_DOMAINS.has(e.entity_id.split(".")[0]));
    const readings = entries.filter(([, e]) => !CONTROL_DOMAINS.has(e.entity_id.split(".")[0]));
    const hasUntested = this.device.untested.length > 0;
    return html`
      <div class="art">${productArt(this.device.kind, this.device.model)}</div>
      ${hasUntested ? banner("info", t("generic.untested_body")) : nothing}
      ${controls.length
        ? group(t("generic.controls"), controls.map(([role, entity]) => this.control(role, entity)))
        : nothing}
      ${readings.length
        ? group(
            t("generic.readings"),
            readings.map(([, entity]) =>
              row({ label: this.name(entity), value: stateLabel(this.hass, entity) }),
            ),
          )
        : nothing}
    `;
  }
}

define("kkp-generic", KkpGeneric);
