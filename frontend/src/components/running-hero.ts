import { css, html, LitElement, nothing } from "lit";
import { icon } from "../art/icons";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { formatCountdown, formatTemperature } from "../util/format";
import { define } from "./define";

export type RunPhase = "running" | "warming" | "paused";

/** The glow's hue follows the water: cool blue, aqua, violet, then red (`q90.a` in the app). */
const TINT_STOPS: [number, [number, number, number]][] = [
  [0, [67, 118, 155]],
  [0.35, [0, 205, 212]],
  [0.7, [176, 55, 251]],
  [1, [253, 0, 2]],
];

/**
 * Where a temperature sits in the showering band — 90–115 °F (32–46 °C) — as 0…1, so an
 * everyday shower glows violet and only a hot one turns red.
 */
export function tintFraction(temperature: number | null, unit: string): number {
  if (temperature === null) return 0.5;
  const [low, high] = unit === "°C" ? [32, 46] : [90, 115];
  return Math.min(1, Math.max(0, (temperature - low) / (high - low)));
}

export function tintFor(fraction: number): string {
  const f = Math.min(1, Math.max(0, fraction));
  for (let i = 1; i < TINT_STOPS.length; i++) {
    const [end, to] = TINT_STOPS[i];
    const [start, from] = TINT_STOPS[i - 1];
    if (f <= end) {
      const k = (f - start) / (end - start);
      const mix = from.map((c, j) => Math.round(c + (to[j] - c) * k));
      return `rgb(${mix.join(" ")})`;
    }
  }
  return "rgb(253 0 2)";
}

const BUBBLES = Array.from({ length: 16 }, (_, i) => ({
  left: (i * 37) % 100,
  size: 6 + ((i * 7) % 16),
  delay: -((i * 0.61) % 4.84),
  drift: ((i % 5) - 2) * 6,
}));

/**
 * The shower while water runs: big temperature, time left, and Stop — the panel's
 * counterpart of the app's immersive sheet, drawn with CSS rather than its animations.
 */
export class KkpRunning extends LitElement {
  // Focusing the sheet focuses its first button (Stop, or Resume), so focus that was on
  // Start lands on the control that ends what Start began.
  static override shadowRootOptions = { ...LitElement.shadowRootOptions, delegatesFocus: true };

  static override properties = {
    phase: {},
    temperature: { type: Number },
    setpoint: { type: Number },
    unit: {},
    tint: { type: Number },
    atTemperature: { type: Boolean },
    secondsRemaining: { type: Number },
    totalSeconds: { type: Number },
    flowingFor: { type: Number },
  };

  declare phase: RunPhase;
  declare temperature: number | null;
  declare setpoint: number | null;
  declare unit: string;
  declare tint: number;
  declare atTemperature: boolean;
  declare secondsRemaining: number | null;
  declare totalSeconds: number | null;
  declare flowingFor: number | null;

  static override styles = [
    shared,
    css`
      :host {
        display: block;
      }

      .hero {
        position: relative;
        overflow: hidden;
        isolation: isolate;
        border-radius: 22px;
        background: #040607;
        color: #f1f2f4;
        min-height: 300px;
        box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06) inset;
      }

      .glow {
        position: absolute;
        inset: -35%;
        z-index: -2;
        background: radial-gradient(42% 46% at 30% 68%, var(--tint) 0%, transparent 70%),
          radial-gradient(36% 40% at 74% 30%, rgba(0, 205, 212, 0.55) 0%, transparent 72%),
          radial-gradient(30% 30% at 60% 90%, var(--tint) 0%, transparent 70%);
        filter: blur(24px) saturate(1.15);
        opacity: 0.8;
        animation: kk-drift 8s ease-in-out infinite alternate;
      }

      .paused .glow {
        opacity: 0.45;
        animation-duration: 12s;
      }

      .bubbles {
        position: absolute;
        inset: 0;
        z-index: -1;
        pointer-events: none;
      }

      .bubbles span {
        position: absolute;
        bottom: -30px;
        border-radius: 50%;
        border: 1px solid rgba(255, 255, 255, 0.32);
        background: radial-gradient(
          circle at 32% 30%,
          rgba(255, 255, 255, 0.42),
          rgba(255, 255, 255, 0.04) 62%
        );
        animation: kk-rise 4.84s linear infinite;
      }

      .paused .bubbles span {
        animation-play-state: paused;
        opacity: 0.25;
      }

      .content {
        position: relative;
        display: grid;
        gap: 22px;
        padding: 22px 24px 24px;
      }

      .top {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 16px;
        letter-spacing: 0.02em;
      }

      .dot {
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background: #00cdd4;
        box-shadow: 0 0 0 0 rgba(0, 205, 212, 0.6);
        animation: kk-beacon 2s infinite;
      }

      .paused .dot {
        background: #ffb020;
        animation: none;
      }

      .main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
      }

      .temp {
        display: grid;
        gap: 6px;
      }

      .big {
        font-size: 84px;
        font-weight: 200;
        line-height: 0.95;
        letter-spacing: -0.03em;
        font-variant-numeric: tabular-nums;
      }

      .big small {
        font-size: 30px;
        font-weight: 300;
        letter-spacing: 0;
        vertical-align: top;
        margin-left: 4px;
      }

      .sub {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px 12px;
        font-size: 15px;
        color: rgba(241, 242, 244, 0.72);
      }

      .sub .ok {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        color: #72ff66;
      }

      .sub .ok svg {
        width: 16px;
        height: 16px;
      }

      .ring {
        position: relative;
        width: 112px;
        height: 112px;
        flex: none;
      }

      .ring svg {
        width: 100%;
        height: 100%;
        transform: rotate(-90deg);
      }

      .ring .track {
        stroke: rgba(255, 255, 255, 0.12);
      }

      .ring .progress {
        stroke: #f1f2f4;
        transition: stroke-dashoffset 900ms linear;
      }

      .ring .label {
        position: absolute;
        inset: 0;
        display: grid;
        place-content: center;
        text-align: center;
      }

      .ring .label strong {
        font-size: 24px;
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .ring .label span {
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: rgba(241, 242, 244, 0.6);
      }

      .actions {
        display: flex;
        gap: 12px;
      }

      .actions .pill {
        flex: 1;
        min-height: 52px;
        border-color: rgba(241, 242, 244, 0.8);
        color: #f1f2f4;
        font-size: 18px;
        backdrop-filter: blur(8px);
        background: rgba(0, 0, 0, 0.18);
      }

      .actions .pill.filled {
        background: var(--kk-accent-fill);
        border-color: transparent;
      }

      .paused-note {
        margin: -8px 0 0;
        font-size: 15px;
        color: rgba(241, 242, 244, 0.72);
      }

      @keyframes kk-drift {
        from {
          transform: translate3d(-3%, 2%, 0) rotate(0deg) scale(1);
        }
        to {
          transform: translate3d(4%, -3%, 0) rotate(8deg) scale(1.08);
        }
      }

      @keyframes kk-rise {
        0% {
          transform: translate3d(0, 0, 0) scale(0.55);
          opacity: 0;
        }
        12% {
          opacity: 0.9;
        }
        100% {
          transform: translate3d(var(--drift), -360px, 0) scale(1.15);
          opacity: 0;
        }
      }

      @keyframes kk-beacon {
        0% {
          box-shadow: 0 0 0 0 rgba(0, 205, 212, 0.6);
        }
        80%,
        100% {
          box-shadow: 0 0 0 10px rgba(0, 205, 212, 0);
        }
      }

      @media (max-width: 420px) {
        .big {
          font-size: 68px;
        }
        .ring {
          width: 96px;
          height: 96px;
        }
      }
    `,
  ];

  private fire(name: string) {
    this.dispatchEvent(new CustomEvent(name));
  }

  private renderRing() {
    if (this.secondsRemaining === null || this.secondsRemaining === undefined) return nothing;
    const total = Math.max(this.totalSeconds ?? this.secondsRemaining, this.secondsRemaining, 1);
    const circumference = 2 * Math.PI * 46;
    const offset = circumference * (1 - this.secondsRemaining / total);
    return html`<div class="ring">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle class="track" cx="50" cy="50" r="46" fill="none" stroke-width="4" />
        <circle
          class="progress"
          cx="50"
          cy="50"
          r="46"
          fill="none"
          stroke-width="4"
          stroke-linecap="round"
          stroke-dasharray=${circumference}
          stroke-dashoffset=${offset}
        />
      </svg>
      <div class="label">
        <strong>${formatCountdown(this.secondsRemaining)}</strong>
        <span>${t("shower.time_left")}</span>
      </div>
    </div>`;
  }

  override render() {
    const title =
      this.phase === "warming"
        ? t("shower.warming_title")
        : this.phase === "paused"
          ? t("shower.paused_title")
          : t("shower.running_title");
    const shown = this.temperature ?? this.setpoint;
    const number = shown === null || shown === undefined ? "—" : formatTemperature(shown, "");
    return html`<section class="hero ${this.phase}" style="--tint: ${tintFor(this.tint ?? 0.5)}">
      <div class="glow"></div>
      <div class="bubbles" aria-hidden="true">
        ${BUBBLES.map(
          (b) =>
            html`<span
              style="left:${b.left}%;width:${b.size}px;height:${b.size}px;animation-delay:${b.delay}s;--drift:${b.drift}px"
            ></span>`,
        )}
      </div>
      <div class="content">
        <div class="top"><span class="dot"></span><span>${title}</span></div>
        <div class="main">
          <div class="temp">
            <div class="big">${number}<small>${this.unit}</small></div>
            <div class="sub">
              ${this.atTemperature
                ? html`<span class="ok">${icon("checkCircle")}${t("shower.at_temperature")}</span>`
                : html`<span>${t("shower.heating")}</span>`}
              ${this.setpoint !== null && this.setpoint !== undefined && this.setpoint !== shown
                ? html`<span>${t("shower.set_to", {
                    temperature: formatTemperature(this.setpoint, this.unit),
                  })}</span>`
                : nothing}
            </div>
          </div>
          ${this.renderRing()}
        </div>
        ${this.phase === "paused"
          ? html`<p class="paused-note">${t("shower.paused_body")}</p>`
          : nothing}
        <div class="actions">
          ${this.phase === "paused"
            ? html`<button class="pill filled" @click=${() => this.fire("kkp-resume")}>
                ${icon("play")}${t("shower.resume")}
              </button>`
            : nothing}
          <button class="pill" @click=${() => this.fire("kkp-stop")}>
            ${icon("stop")}${t("shower.stop")}
          </button>
        </div>
      </div>
    </section>`;
  }
}

define("kkp-running", KkpRunning);
