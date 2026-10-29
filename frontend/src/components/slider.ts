import { css, html, LitElement, nothing } from "lit";
import { icon } from "../art/icons";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { formatNumber, formatTemperature } from "../util/format";
import { define } from "./define";

const COMMIT_DELAY_MS = 400;
const DRAFT_TIMEOUT_MS = 5000;

/**
 * The Konnect slider: − and + buttons either side of a track, the value in the accent
 * colour above. `temperature` paints the app's cool-to-hot gradient; `flow` fills in teal.
 *
 * Fires `change` once a value settles — on release, or 400 ms after the last ± or key
 * press — never while dragging, so one gesture is one command to the valve.
 */
export class KkpSlider extends LitElement {
  static override properties = {
    label: {},
    value: { type: Number },
    min: { type: Number },
    max: { type: Number },
    step: { type: Number },
    unit: {},
    variant: {},
    coldValue: { type: Number, attribute: false },
    disabled: { type: Boolean },
    compact: { type: Boolean },
    draft: { state: true },
  };

  declare label: string;
  declare value: number | null;
  declare min: number;
  declare max: number;
  declare step: number;
  declare unit: string;
  declare variant: "temperature" | "flow";
  declare coldValue: number | null;
  declare disabled: boolean;
  declare compact: boolean;
  declare draft: number | null;

  private commitTimer?: number;
  private draftTimer?: number;
  private dragging = false;

  constructor() {
    super();
    this.value = null;
    this.min = 0;
    this.max = 100;
    this.step = 1;
    this.unit = "";
    this.variant = "temperature";
    this.coldValue = null;
    this.disabled = false;
    this.compact = false;
    this.draft = null;
  }

  static override styles = [
    shared,
    css`
      :host {
        display: block;
      }

      .head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 18px;
      }

      .label {
        font-size: 20px;
        color: var(--kk-text);
      }

      :host([compact]) .label {
        font-size: 17px;
        color: var(--kk-text-2);
      }

      .value {
        font-size: 22px;
        color: var(--kk-accent-text);
        font-variant-numeric: tabular-nums;
      }

      .row {
        display: flex;
        align-items: center;
        gap: 14px;
      }

      .track {
        position: relative;
        flex: 1;
        height: 48px;
        touch-action: none;
        cursor: pointer;
      }

      .track[aria-disabled="true"] {
        cursor: default;
        opacity: 0.4;
      }

      .rail,
      .fill {
        position: absolute;
        top: 50%;
        left: 0;
        right: 0;
        height: 10px;
        margin-top: -5px;
        border-radius: var(--kk-radius-pill);
      }

      .temperature .rail {
        background: var(--kk-temp-gradient);
      }

      .flow .rail {
        background: var(--kk-control);
      }

      .flow .fill {
        right: auto;
        background: linear-gradient(90deg, var(--kk-bar-bottom), var(--kk-bar-top));
      }

      .thumb {
        position: absolute;
        top: 50%;
        width: 40px;
        height: 40px;
        margin: -20px 0 0 -20px;
        border-radius: 50%;
        background: #fff;
        border: 2px solid var(--kk-thumb-edge);
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
        transition: transform 160ms var(--kk-ease);
      }

      .track.dragging .thumb {
        transform: scale(1.08);
      }

      .track:focus-visible {
        outline: none;
      }

      .track:focus-visible .thumb {
        outline: 2px solid var(--kk-accent);
        outline-offset: 3px;
      }

      /* With no value there is no thumb: ring the track instead. */
      .track.empty:focus-visible {
        outline: 2px solid var(--kk-accent);
        outline-offset: 3px;
        border-radius: var(--kk-radius-pill);
      }


    `,
  ];

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    window.clearTimeout(this.commitTimer);
    window.clearTimeout(this.draftTimer);
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    // A new value from Home Assistant settles any draft still on screen.
    if (changed.has("value") && !this.dragging && this.commitTimer === undefined) {
      this.draft = null;
    }
  }

  private get shown(): number | null {
    return this.draft ?? this.value;
  }

  private format(value: number | null): string {
    if (value === null) return "—";
    if (this.coldValue !== null && value <= this.coldValue) return t("shower.cold");
    if (this.variant === "temperature") return formatTemperature(value, this.unit);
    // Flow and other percentages come with no unit or "%"; anything else shows its own.
    if (!this.unit || this.unit === "%") return `${Math.round(value)}%`;
    return `${formatNumber(value, Number.isInteger(this.step) ? 0 : 1)} ${this.unit}`;
  }

  private fraction(value: number | null): number {
    if (value === null || this.max <= this.min) return 0;
    return Math.min(1, Math.max(0, (value - this.min) / (this.max - this.min)));
  }

  private snap(value: number): number {
    const snapped = Math.round((value - this.min) / this.step) * this.step + this.min;
    return Math.min(this.max, Math.max(this.min, Number(snapped.toFixed(4))));
  }

  private setDraft(value: number, commit: "now" | "soon" | "none") {
    this.draft = this.snap(value);
    window.clearTimeout(this.commitTimer);
    this.commitTimer = undefined;
    if (commit === "now") this.commit();
    if (commit === "soon") {
      this.commitTimer = window.setTimeout(() => this.commit(), COMMIT_DELAY_MS);
    }
  }

  private commit() {
    window.clearTimeout(this.commitTimer);
    this.commitTimer = undefined;
    if (this.draft === null || this.draft === this.value) {
      this.draft = null;
      return;
    }
    this.dispatchEvent(new CustomEvent("change", { detail: this.draft }));
    // Hold the draft until Home Assistant reports the new value, but not forever: a
    // refused change must snap back.
    window.clearTimeout(this.draftTimer);
    this.draftTimer = window.setTimeout(() => {
      this.draft = null;
    }, DRAFT_TIMEOUT_MS);
  }

  private valueAt(event: PointerEvent): number {
    const track = event.currentTarget as HTMLElement;
    const rect = track.getBoundingClientRect();
    const inner = rect.width - 40;
    const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left - 20) / inner));
    return this.min + fraction * (this.max - this.min);
  }

  private onPointerDown(event: PointerEvent) {
    if (this.disabled) return;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    this.dragging = true;
    this.setDraft(this.valueAt(event), "none");
    this.requestUpdate();
  }

  private onPointerMove(event: PointerEvent) {
    if (!this.dragging) return;
    this.setDraft(this.valueAt(event), "none");
  }

  private onPointerUp() {
    if (!this.dragging) return;
    this.dragging = false;
    this.commit();
    this.requestUpdate();
  }

  private nudge(direction: 1 | -1, size = 1) {
    if (this.disabled) return;
    const base = this.shown ?? this.min;
    this.setDraft(base + direction * this.step * size, "soon");
  }

  private onKey(event: KeyboardEvent) {
    const moves: Record<string, () => void> = {
      ArrowRight: () => this.nudge(1),
      ArrowUp: () => this.nudge(1),
      ArrowLeft: () => this.nudge(-1),
      ArrowDown: () => this.nudge(-1),
      PageUp: () => this.nudge(1, 5),
      PageDown: () => this.nudge(-1, 5),
      Home: () => this.setDraft(this.min, "soon"),
      End: () => this.setDraft(this.max, "soon"),
    };
    const move = moves[event.key];
    if (move && !this.disabled) {
      event.preventDefault();
      move();
    }
  }

  override render() {
    const shown = this.shown;
    const position = `calc(20px + (100% - 40px) * ${this.fraction(shown)})`;
    return html`
      <div class="head">
        <span class="label">${this.label}</span>
        <span class="value">${this.format(shown)}</span>
      </div>
      <div class="row">
        <button
          class="round"
          aria-label=${t("control.decrease", { name: this.label })}
          ?disabled=${this.disabled}
          aria-disabled=${String((shown ?? this.min) <= this.min)}
          @click=${() => this.nudge(-1)}
        >
          ${icon("minus")}
        </button>
        <div
          class="track ${this.variant} ${this.dragging ? "dragging" : ""} ${shown === null ? "empty" : ""}"
          role="slider"
          tabindex=${this.disabled ? -1 : 0}
          aria-label=${this.label}
          aria-valuemin=${this.min}
          aria-valuemax=${this.max}
          aria-valuenow=${shown ?? nothing}
          aria-valuetext=${this.format(shown)}
          aria-disabled=${this.disabled ? "true" : "false"}
          @pointerdown=${this.onPointerDown}
          @pointermove=${this.onPointerMove}
          @pointerup=${this.onPointerUp}
          @pointercancel=${this.onPointerUp}
          @keydown=${this.onKey}
        >
          <div class="rail"></div>
          ${this.variant === "flow"
            ? html`<div class="fill" style="width: ${position}"></div>`
            : nothing}
          ${shown === null ? nothing : html`<div class="thumb" style="left: ${position}"></div>`}
        </div>
        <button
          class="round"
          aria-label=${t("control.increase", { name: this.label })}
          ?disabled=${this.disabled}
          aria-disabled=${String((shown ?? this.max) >= this.max)}
          @click=${() => this.nudge(1)}
        >
          ${icon("plus")}
        </button>
      </div>
    `;
  }
}

define("kkp-slider", KkpSlider);
