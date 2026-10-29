import { css, html, LitElement, nothing } from "lit";
import { icon } from "../art/icons";
import { define } from "../components/define";
import { banner, bannerStyles, group, radioKeys, radioTabIndex, row, rowStyles } from "../components/rows";
import "../components/toggle";
import { errorMessage, type PanelContext } from "../context";
import { t, type MessageKey } from "../i18n";
import { shared } from "../theme/shared";
import type { AccountOptions, DevicesMessage, EntryInfo, HomeAssistant, OptionField } from "../types";

/** Each Kohler account's own settings: what Configure on the integration card holds. */
export class KkpAccount extends LitElement {
  static override properties = {
    hass: { attribute: false },
    data: { attribute: false },
    ctx: { attribute: false },
    options: { state: true },
    drafts: { state: true },
    saving: { state: true },
  };

  declare hass: HomeAssistant;
  declare data: DevicesMessage;
  declare ctx: PanelContext;
  declare options: Record<string, AccountOptions>;
  declare drafts: Record<string, Record<string, unknown>>;
  declare saving: string | null;

  constructor() {
    super();
    this.options = {};
    this.drafts = {};
    this.saving = null;
  }

  static override styles = [
    shared,
    rowStyles,
    bannerStyles,
    css`
      :host {
        display: block;
        max-width: 720px;
      }

      .stepper {
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .stepper .round {
        width: 40px;
        height: 40px;
      }

      .stepper strong {
        min-width: 72px;
        text-align: center;
        font-weight: 400;
        font-variant-numeric: tabular-nums;
      }

      .save {
        display: grid;
        justify-items: end;
        gap: 12px;
        margin-top: 18px;
      }

      .save .banner {
        width: 100%;
      }
    `,
  ];

  protected override firstUpdated(): void {
    void this.load();
  }

  private async load() {
    for (const entry of this.data.entries) {
      try {
        const options = await this.ctx.api.getOptions(entry.entry_id);
        this.options = { ...this.options, [entry.entry_id]: options };
      } catch (error) {
        this.ctx.toast(errorMessage(error), "error");
      }
    }
  }

  private value(entryId: string, key: string): unknown {
    return this.drafts[entryId]?.[key] ?? this.options[entryId]?.values[key];
  }

  private set(entryId: string, key: string, value: unknown) {
    this.drafts = { ...this.drafts, [entryId]: { ...this.drafts[entryId], [key]: value } };
  }

  private async save(entry: EntryInfo) {
    const changes = this.drafts[entry.entry_id];
    if (!changes || this.saving) return;
    this.saving = entry.entry_id;
    try {
      const result = await this.ctx.api.updateOptions(entry.entry_id, changes);
      const options = this.options[entry.entry_id];
      this.options = { ...this.options, [entry.entry_id]: { ...options, values: result.values } };
      const rest = { ...this.drafts };
      delete rest[entry.entry_id];
      this.drafts = rest;
      this.ctx.toast(t("account.saved"));
      // Save goes once there is nothing to save: focus the account's heading instead.
      await this.updateComplete;
      const index = this.data.entries.indexOf(entry);
      this.renderRoot.querySelectorAll<HTMLElement>(".group h2")[index]?.focus();
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    } finally {
      this.saving = null;
    }
  }

  private async download(entry: EntryInfo) {
    try {
      const path = await this.ctx.api.signPath(`/api/diagnostics/config_entry/${entry.entry_id}`);
      window.open(path, "_blank");
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  private renderField(entry: EntryInfo, field: OptionField) {
    const id = entry.entry_id;
    if (field.kind === "toggle") {
      return row({
        label: t("account.sidebar_panel"),
        description: t("account.sidebar_panel_body"),
        trailing: html`<kkp-toggle
          .checked=${Boolean(this.value(id, field.key))}
          label=${t("account.sidebar_panel")}
          @change=${(event: CustomEvent<boolean>) => this.set(id, field.key, event.detail)}
        ></kkp-toggle>`,
      });
    }
    if (field.kind === "minutes") {
      const minutes = Number(this.value(id, field.key) ?? 0);
      const [lowest, highest] = [field.min ?? 0, field.max ?? 120];
      return row({
        label: t("account.safety_limit"),
        description: t("account.safety_limit_body"),
        trailing: html`<span class="stepper">
          <button class="round" aria-label=${t("control.decrease", { name: t("account.safety_limit") })} aria-disabled=${String(minutes <= lowest)}
            @click=${() => minutes > lowest && this.set(id, field.key, minutes - 1)}>${icon("minus")}</button>
          <strong aria-live="polite">${minutes ? t("account.minutes", { count: minutes }) : t("account.off")}</strong>
          <button class="round" aria-label=${t("control.increase", { name: t("account.safety_limit") })} aria-disabled=${String(minutes >= highest)}
            @click=${() => minutes < highest && this.set(id, field.key, minutes + 1)}>${icon("plus")}</button>
        </span>`,
      });
    }
    const current = this.value(id, field.key);
    return html`<div class="row" style="display:block">
      <span class="label">${t("account.zone_grouping")}</span>
      <div
        class="choices"
        role="radiogroup"
        aria-label=${t("account.zone_grouping")}
        style="margin-top: 8px"
        @keydown=${radioKeys}
      >
        ${(field.options ?? []).map(
          (option, index, options) => html`<button
            class="choice"
            role="radio"
            aria-checked=${current === option ? "true" : "false"}
            tabindex=${radioTabIndex(current === option, index, options.includes(String(current)))}
            @click=${() => this.set(id, field.key, option)}
          >
            <span>${t(`account.zone_grouping.${option}` as MessageKey)}</span><span class="radio"></span>
          </button>`,
        )}
      </div>
    </div>`;
  }

  /** Configure's options, less zone grouping where no device has a second zone. */
  private shownFields(entry: EntryInfo, options: AccountOptions): OptionField[] {
    const zoned = this.data.devices.some((d) => d.entry_id === entry.entry_id && d.zones.length > 1);
    return options.fields.filter((field) => field.key !== "zone_grouping" || zoned);
  }

  private renderEntry(entry: EntryInfo) {
    const options = this.options[entry.entry_id];
    const draft = this.drafts[entry.entry_id];
    const reloads =
      !!draft && !!options?.fields.some((f) => f.reloads && f.key in draft);
    const stream =
      entry.stream_connected === null
        ? html`<span class="dot"></span>${t("diagnostics.unknown")}`
        : entry.stream_connected
          ? html`<span class="dot good"></span>${t("diagnostics.connected")}`
          : html`<span class="dot bad"></span>${t("diagnostics.disconnected")}`;
    return html`
      ${group(
        entry.title ?? t("app.title"),
        html`
          ${row({ label: t("account.connection"), value: stream })}
          ${options ? this.shownFields(entry, options).map((field) => this.renderField(entry, field)) : nothing}
          ${row({
            label: t("diagnostics.download"),
            description: t("diagnostics.download_body"),
            trailing: icon("download"),
            onClick: () => this.download(entry),
          })}
        `,
      )}
      ${draft
        ? html`<div class="save">
            ${reloads ? banner("warning", t("account.reload_warning")) : nothing}
            <button class="pill filled" aria-disabled=${String(this.saving === entry.entry_id)} @click=${() => this.save(entry)}>
              ${t("account.save")}
            </button>
          </div>`
        : nothing}
    `;
  }

  override render() {
    if (!this.ctx.admin) return banner("info", t("account.admin_only"));
    return this.data.entries.map((entry) => this.renderEntry(entry));
  }
}

define("kkp-account", KkpAccount);
