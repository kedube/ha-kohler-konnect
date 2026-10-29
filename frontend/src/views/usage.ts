import { css, html, nothing } from "lit";
import { icon } from "../art/icons";
import "../components/bar-chart";
import "../components/tabs";
import { define } from "../components/define";
import { DeviceView, errorMessage } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import type { UsageHistory, UsagePeriod } from "../types";
import {
  bathEquivalent,
  chartBars,
  chartTicks,
  firstWeekday,
  formatFigure,
  periodLabel,
  type UsageMetric,
} from "../util/usage";

/**
 * The app's Water Usage screen: Week / Month / Year, a pager, Average and High, and the
 * bars — plus Total and a Water / Time switch. The current period comes from data the
 * integration holds; earlier ones are read from Kohler as you page back.
 */
export class KkpUsage extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    period: { state: true },
    anchor: { state: true },
    metric: { state: true },
    history: { state: true },
    loading: { state: true },
    failed: { state: true },
  };

  declare period: UsagePeriod;
  declare anchor: string | undefined;
  declare metric: UsageMetric;
  declare history: UsageHistory | null;
  declare loading: boolean;
  declare failed: string | null;

  private request = 0;

  constructor() {
    super();
    this.period = "month";
    this.anchor = undefined;
    this.metric = "volume";
    this.history = null;
    this.loading = false;
    this.failed = null;
  }

  static override styles = [
    shared,
    css`
      :host {
        display: block;
        max-width: 860px;
        padding-bottom: 48px;
      }

      .pager {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 28px;
        margin: 26px 0 22px;
        font-size: 24px;
        font-weight: 300;
      }

      .pager button {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        color: var(--kk-text);
      }

      .pager button:disabled {
        color: var(--kk-text-3);
        opacity: 0.5;
      }

      .pager button svg {
        width: 26px;
        height: 26px;
      }

      .stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        text-align: center;
        margin-bottom: 18px;
      }

      .stat span {
        display: block;
        font-size: 17px;
        color: var(--kk-text-2);
        margin-bottom: 6px;
      }

      .stat strong {
        font-size: clamp(24px, 5vw, 32px);
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .stat small {
        display: block;
        margin-top: 2px;
        font-size: 13px;
        color: var(--kk-text-3);
      }

      .metric {
        display: flex;
        justify-content: center;
        margin: 6px 0 20px;
      }

      .segmented {
        display: inline-flex;
        padding: 4px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
      }

      .segmented button {
        padding: 8px 20px;
        border-radius: var(--kk-radius-pill);
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .segmented button[aria-pressed="true"] {
        background: var(--kk-canvas);
        color: var(--kk-text);
        box-shadow: inset 0 0 0 1.5px var(--kk-text-2);
      }

      .chart {
        position: relative;
        padding: 12px 0 0;
      }

      .chart.loading {
        opacity: 0.4;
        transition: opacity 300ms;
      }

      .footnote,
      .equivalent {
        margin: 18px 4px 0;
        font-size: 14px;
        color: var(--kk-text-3);
      }

      .equivalent {
        display: flex;
        align-items: center;
        gap: 8px;
        color: var(--kk-text-2);
        font-size: 15px;
      }

      .equivalent svg {
        width: 20px;
        height: 20px;
        color: var(--kk-accent);
      }

      .error {
        display: grid;
        justify-items: center;
        gap: 14px;
        padding: 60px 0;
        color: var(--kk-text-2);
      }
    `,
  ];

  protected override firstUpdated(): void {
    void this.load();
  }

  private async load() {
    const id = ++this.request;
    this.loading = true;
    this.failed = null;
    try {
      const history = await this.ctx.api.usageHistory(
        this.device.id,
        this.period,
        firstWeekday(this.hass.locale),
        this.anchor,
      );
      if (id === this.request) this.history = history;
    } catch (error) {
      if (id === this.request) this.failed = errorMessage(error);
    } finally {
      if (id === this.request) this.loading = false;
    }
  }

  private choosePeriod(period: UsagePeriod) {
    if (period === this.period) return;
    this.period = period;
    this.anchor = undefined;
    this.history = null;
    void this.load();
  }

  private page(anchor: string | null) {
    // Not disabled while loading: the button pressed would lose focus.
    if (!anchor || this.loading) return;
    this.anchor = anchor;
    void this.load();
  }

  override render() {
    const history = this.history;
    const unit = history?.unit ?? this.device.units.volume;
    const figure = (value: number | null | undefined) => formatFigure(value ?? null, this.metric, unit);
    const pick = (f: { volume: number | null; minutes: number | null } | undefined) =>
      f ? (this.metric === "volume" ? f.volume : f.minutes) : null;
    const baths = this.metric === "volume" && this.period !== "week" ? bathEquivalent(history?.total.volume ?? null, unit) : null;
    return html`
      <kkp-tabs
        .items=${[
          { key: "week", label: t("usage.week") },
          { key: "month", label: t("usage.month") },
          { key: "year", label: t("usage.year") },
        ]}
        .active=${this.period}
        @kkp-select=${(event: CustomEvent<UsagePeriod>) => this.choosePeriod(event.detail)}
      ></kkp-tabs>
      <div role="tabpanel" aria-label=${t(`usage.${this.period}`)}>
      <div class="pager">
        <button aria-label=${t("usage.previous")} ?disabled=${!history?.previous} @click=${() => this.page(history?.previous ?? null)}>
          ${icon("back")}
        </button>
        <span aria-live="polite">${history ? periodLabel(history) : "—"}</span>
        <button aria-label=${t("usage.next")} ?disabled=${!history?.next} @click=${() => this.page(history?.next ?? null)}>
          ${icon("chevronRight")}
        </button>
      </div>
      <div class="stats">
        <div class="stat"><span>${t("usage.average")}</span><strong>${figure(pick(history?.average))}</strong><small>${t("usage.per_day")}</small></div>
        <div class="stat"><span>${t("usage.high")}</span><strong>${figure(pick(history?.high))}</strong></div>
        <div class="stat"><span>${t("usage.total")}</span><strong>${figure(pick(history?.total))}</strong></div>
      </div>
      <div class="metric">
        <div class="segmented" role="group">
          <button aria-pressed=${String(this.metric === "volume")} @click=${() => (this.metric = "volume")}>${t("usage.water")}</button>
          <button aria-pressed=${String(this.metric === "minutes")} @click=${() => (this.metric = "minutes")}>${t("usage.time")}</button>
        </div>
      </div>
      ${this.failed
        ? html`<div class="error" role="alert">
            <span>${t("usage.error")}</span>
            <button class="pill" @click=${() => this.load()}>${t("usage.retry")}</button>
          </div>`
        : html`<div class="chart ${this.loading ? "loading" : ""}" aria-busy=${String(this.loading)}>
            <kkp-bar-chart
              .bars=${history ? chartBars(history, this.metric) : []}
              .ticks=${history ? chartTicks(history) : []}
              caption=${`${t("usage.title")} · ${history ? periodLabel(history) : ""}`}
            ></kkp-bar-chart>
          </div>`}
      ${baths && baths >= 2 ? html`<div class="equivalent">${icon("water")}${t("usage.equivalent_bath", { count: baths })}</div>` : nothing}
      ${this.period === "week" ? html`<p class="footnote">${t("usage.calendar_week")}</p>` : nothing}
      </div>
    `;
  }
}

define("kkp-usage", KkpUsage);
