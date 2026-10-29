import { css, html, nothing } from "lit";
import { icon } from "../art/icons";
import "../components/toggle";
import { define } from "../components/define";
import { group, row, rowStyles } from "../components/rows";
import { DeviceView, errorMessage } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import type { DeviceDetails } from "../types";
import { formatDateTime, relativeTime } from "../util/format";

/** Connection health, faults and the bug-report tools, in one place. */
export class KkpDiagnostics extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    details: { state: true },
  };

  declare details: DeviceDetails | null;

  constructor() {
    super();
    this.details = null;
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
    `,
  ];

  protected override async firstUpdated(): Promise<void> {
    try {
      this.details = await this.ctx.api.deviceDetails(this.device.id);
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  private connection(state: boolean | null | undefined) {
    if (state === null || state === undefined) {
      return html`<span class="dot"></span>${t("diagnostics.unknown")}`;
    }
    return state
      ? html`<span class="dot good"></span>${t("diagnostics.connected")}`
      : html`<span class="dot bad"></span>${t("diagnostics.disconnected")}`;
  }

  private async download() {
    const path = this.details?.diagnostics_path;
    if (!path) return;
    try {
      window.open(await this.ctx.api.signPath(path), "_blank");
    } catch (error) {
      this.ctx.toast(errorMessage(error), "error");
    }
  }

  override render() {
    const faults = this.details?.faults ?? [];
    const reportLog = this.entity("report_log");
    return html`
      ${group(
        null,
        html`
          ${row({
            label: t("diagnostics.stream"),
            description: t("diagnostics.stream_body"),
            value: this.connection(this.live?.stream_connected),
          })}
          ${row({
            label: t("diagnostics.cloud"),
            description: t("diagnostics.cloud_body"),
            value: this.connection(this.live?.cloud_connected),
          })}
          ${row({
            label: t("diagnostics.last_update"),
            value: this.live?.last_update ? relativeTime(this.live.last_update) : "—",
          })}
        `,
      )}
      ${group(
        t("diagnostics.faults"),
        html`
          ${faults.length
            ? faults.map((fault) =>
                row({
                  label: fault.title ?? fault.code ?? "—",
                  description: [fault.code, fault.area, fault.time ? formatDateTime(fault.time) : null]
                    .filter(Boolean)
                    .join(" · "),
                }),
              )
            : row({ label: t("diagnostics.no_faults") })}
          ${this.has("clear_errors")
            ? row({
                label: t("diagnostics.clear_errors"),
                trailing: icon("restart"),
                onClick: () => void this.roleService("clear_errors", "button", "press"),
              })
            : nothing}
        `,
      )}
      ${group(
        t("settings.diagnostics"),
        html`
          ${reportLog
            ? row({
                label: t("diagnostics.report_log"),
                description: reportLog.attributes.file ?? t("diagnostics.report_log_body"),
                trailing: html`<kkp-toggle
                  .checked=${reportLog.state === "on"}
                  label=${t("diagnostics.report_log")}
                  @change=${(event: CustomEvent<boolean>) =>
                    void this.roleService("report_log", "switch", event.detail ? "turn_on" : "turn_off")}
                ></kkp-toggle>`,
              })
            : nothing}
          ${this.details?.diagnostics_path
            ? row({
                label: t("diagnostics.download"),
                description: t("diagnostics.download_body"),
                trailing: icon("download"),
                onClick: () => this.download(),
              })
            : nothing}
        `,
      )}
    `;
  }
}

define("kkp-diagnostics", KkpDiagnostics);
