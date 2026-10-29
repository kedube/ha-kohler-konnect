import { css, html, nothing } from "lit";
import { define } from "../components/define";
import { group, row, rowStyles } from "../components/rows";
import { DeviceView, errorMessage } from "../context";
import { t, type MessageKey } from "../i18n";
import { shared } from "../theme/shared";
import type { DeviceDetails, DevicePart } from "../types";
import { formatDate, formatDateTime } from "../util/format";

const PART_TITLES: Record<string, MessageKey> = {
  gateway: "about.gateway",
  valve: "about.valves",
  interface: "about.interfaces",
};

const FIRMWARE_LABELS: Record<string, MessageKey> = {
  interface: "about.firmware.interface",
  valve: "about.firmware.valve",
  second_valve: "about.firmware.second_valve",
  gateway: "about.firmware.gateway",
  controller: "about.firmware.controller",
  application: "about.firmware.application",
  bootloader: "about.firmware.bootloader",
  os: "about.firmware.os",
  certificate: "about.firmware.certificate",
  service_pack: "about.firmware.service_pack",
};

/** The app's About screen: identity, install date, firmware, and the system's parts. */
export class KkpAbout extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    details: { state: true },
    open: { state: true },
  };

  declare details: DeviceDetails | null;
  declare open: string | null;

  constructor() {
    super();
    this.details = null;
    this.open = null;
  }

  static override styles = [
    shared,
    rowStyles,
    css`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .part {
        padding: 6px 4px 6px;
        font-size: 16px;
      }

      .part dl {
        display: grid;
        grid-template-columns: max-content 1fr;
        gap: 10px 24px;
        margin: 0;
      }

      .part dt {
        color: var(--kk-text-2);
      }

      .part dd {
        margin: 0;
        text-align: right;
        font-variant-numeric: tabular-nums;
        overflow-wrap: anywhere;
      }

      .part + .part {
        border-top: 1px solid var(--kk-divider);
        padding-top: 14px;
        margin-top: 8px;
      }

      .part h3 {
        margin: 0 0 10px;
        font-size: 16px;
        font-weight: 500;
      }

      .note {
        font-size: 14px;
        color: var(--kk-text-2);
      }
    `,
  ];

  protected override async firstUpdated(): Promise<void> {
    try {
      this.details = await this.ctx.api.deviceDetails(this.device.id);
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  private firmwareValue() {
    const update = this.entity("firmware");
    if (!update) return this.details?.firmware[0]?.version ?? "—";
    if (update.state === "on") {
      return html`<span class="dot warn"></span>${t("about.update_available", {
        version: update.attributes.latest_version ?? "",
      })}`;
    }
    return html`<span class="dot good"></span>${t("about.up_to_date")}`;
  }

  private renderPart(part: DevicePart) {
    const fields: [MessageKey, string | null][] = [
      ["about.model", part.model],
      ["about.serial", part.serial],
      ["about.firmware", part.firmware],
      ["about.install_date", part.install_date ? formatDate(part.install_date) : null],
      ["about.status", part.status],
    ];
    return html`<div class="part">
      ${part.name ? html`<h3>${part.name}</h3>` : nothing}
      <dl>
        ${fields
          .filter(([, value]) => value)
          .map(([label, value]) => html`<dt>${t(label)}</dt><dd>${value}</dd>`)}
      </dl>
    </div>`;
  }

  private renderParts() {
    const parts = this.details?.parts ?? [];
    const kinds = [...new Set(parts.map((p) => p.kind))];
    if (!kinds.length) return nothing;
    return group(
      t("about.technical"),
      kinds.map((kind) => {
        const open = this.open === kind;
        return html`${row({
          label: t(PART_TITLES[kind] ?? "about.technical"),
          chevron: true,
          expanded: open,
          onClick: () => (this.open = open ? null : kind),
        })}
          ${open
            ? html`<div class="editor">${parts.filter((p) => p.kind === kind).map((p) => this.renderPart(p))}</div>`
            : nothing}`;
      }),
    );
  }

  override render() {
    const d = this.details;
    const unknown = t("about.unknown");
    const model = [d?.model_name ?? this.device.model_name, d?.model ?? this.device.model]
      .filter(Boolean)
      .join(" · ");
    const firmwareParts = d?.firmware.filter((f) => f.version) ?? [];
    return html`
      ${group(
        null,
        html`
          ${row({ label: this.device.name })}
          ${row({ label: t("about.model"), value: model || unknown })}
          ${row({ label: t("about.serial"), value: d?.serial ?? unknown })}
          ${row({ label: t("about.install_date"), value: d?.install_date ? formatDate(d.install_date) : unknown })}
          ${d?.registered ? row({ label: t("about.registered"), value: formatDate(d.registered) }) : nothing}
          ${d?.hardware ? row({ label: t("about.hardware"), value: d.hardware }) : nothing}
          ${row({
            label: t("about.firmware"),
            value: this.firmwareValue(),
            description: this.entity("firmware")?.state === "on" ? t("about.update_in_app") : undefined,
          })}
          ${firmwareParts.map((f) => row({ label: FIRMWARE_LABELS[f.key] ? t(FIRMWARE_LABELS[f.key]) : f.key, value: f.version }))}
          ${row({
            label: t("settings.diagnostics"),
            chevron: true,
            onClick: () => this.ctx.navigate(`/d/${this.device.id}/diagnostics`),
          })}
        `,
      )}
      ${this.renderParts()}
      ${this.device.kind === "faucet"
        ? group(
            t("about.leak_history"),
            d?.leak_history.length
              ? d.leak_history.map((time) => row({ label: formatDateTime(time) }))
              : row({ label: t("about.no_leaks") }),
          )
        : nothing}
    `;
  }
}

define("kkp-about", KkpAbout);
