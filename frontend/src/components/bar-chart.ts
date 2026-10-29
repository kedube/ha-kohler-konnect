import { css, html, LitElement, nothing, svg } from "lit";
import { shared } from "../theme/shared";
import { define } from "./define";

export interface ChartBar {
  key: string;
  label: string;
  value: number | null;
  tooltip: string;
}

export interface ChartTick {
  index: number;
  label: string;
}

const NICE_STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];

/** A round axis top in four steps: 12.7 → 16 (4 × 4), 48.8 → 60 (4 × 15). */
export function niceScale(max: number, ticks = 4): { top: number; step: number } {
  if (!(max > 0)) return { top: ticks, step: 1 };
  const raw = max / ticks;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const nice = NICE_STEPS.find((step) => raw / magnitude <= step) ?? 10;
  const step = nice * magnitude;
  return { top: step * ticks, step };
}

let gradientId = 0;

/**
 * Usage bars as the app draws them: a thin teal-gradient bar over a full-height track for
 * every period, the scale on the right. Hover or tap a bar for its figure.
 */
export class KkpBarChart extends LitElement {
  static override properties = {
    bars: { attribute: false },
    ticks: { attribute: false },
    caption: {},
    width: { state: true },
    selected: { state: true },
  };

  declare bars: ChartBar[];
  declare ticks: ChartTick[];
  declare caption: string;
  declare width: number;
  declare selected: number | null;

  private readonly gradient = `kk-bars-${++gradientId}`;
  private observer?: ResizeObserver;

  constructor() {
    super();
    this.bars = [];
    this.ticks = [];
    this.width = 0;
    this.selected = null;
  }

  static override styles = [
    shared,
    css`
      :host {
        display: block;
        position: relative;
      }

      svg {
        display: block;
        width: 100%;
        height: 260px;
        overflow: visible;
      }

      svg:focus-visible {
        outline: 2px solid var(--kk-accent);
        outline-offset: 4px;
        border-radius: 8px;
      }

      .grid {
        stroke: var(--kk-grid);
        stroke-width: 1;
      }

      .track {
        fill: var(--kk-bar-track);
      }

      .bar {
        transition: opacity 200ms var(--kk-ease);
      }

      .dim .bar {
        opacity: 0.45;
      }

      .dim .bar.selected {
        opacity: 1;
      }

      text {
        fill: var(--kk-text-3);
        font-size: 12px;
        font-family: var(--kk-font);
      }

      .hit {
        fill: transparent;
        cursor: pointer;
      }

      .tip {
        position: absolute;
        transform: translate(-50%, calc(-100% - 10px));
        padding: 8px 12px;
        border-radius: 10px;
        background: var(--kk-control);
        color: var(--kk-text);
        font-size: 14px;
        white-space: nowrap;
        pointer-events: none;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
      }
    `,
  ];

  override connectedCallback(): void {
    super.connectedCallback();
    this.observer = new ResizeObserver((entries) => {
      this.width = Math.round(entries[0].contentRect.width);
    });
    this.observer.observe(this);
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.observer?.disconnect();
    window.removeEventListener("keydown", this.onWindowKey);
  }

  // Escape closes a tooltip opened by hovering too, without moving the pointer.
  private onWindowKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") this.selected = null;
  };

  protected override updated(changed: Map<string, unknown>): void {
    if (!changed.has("selected")) return;
    window.removeEventListener("keydown", this.onWindowKey);
    if (this.selected !== null) window.addEventListener("keydown", this.onWindowKey);
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    if (changed.has("bars")) this.selected = null;
  }

  private onKey(event: KeyboardEvent) {
    if (!this.bars.length) return;
    const last = this.bars.length - 1;
    if (event.key === "ArrowRight") this.selected = Math.min(last, (this.selected ?? -1) + 1);
    else if (event.key === "ArrowLeft") this.selected = Math.max(0, (this.selected ?? last + 1) - 1);
    else if (event.key === "Escape") this.selected = null;
    else return;
    event.preventDefault();
  }

  override render() {
    const W = Math.max(this.width, 240);
    const H = 260;
    const top = 14;
    const bottom = 30;
    const right = 44;
    const plotW = W - right;
    const plotH = H - top - bottom;
    const max = Math.max(0, ...this.bars.map((b) => b.value ?? 0));
    const scale = niceScale(max);
    const n = Math.max(this.bars.length, 1);
    const slot = plotW / n;
    const barW = Math.max(3, Math.min(16, slot * 0.42));
    const y = (v: number) => top + plotH * (1 - v / scale.top);
    const gridValues = Array.from({ length: 5 }, (_, i) => i * scale.step);
    const selected = this.selected;
    const selectedBar = selected === null ? undefined : this.bars[selected];
    const cx = (i: number) => slot * (i + 0.5);

    return html`
      <svg
        viewBox="0 0 ${W} ${H}"
        tabindex="0"
        role="img"
        aria-label=${this.caption}
        class=${selected === null ? "" : "dim"}
        @keydown=${this.onKey}
        @pointerleave=${() => (this.selected = null)}
      >
        <defs>
          <linearGradient id=${this.gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style="stop-color: var(--kk-bar-top)" />
            <stop offset="1" style="stop-color: var(--kk-bar-bottom)" />
          </linearGradient>
        </defs>
        ${gridValues.map(
          (v) => svg`
            <line class="grid" x1="0" x2=${plotW} y1=${y(v)} y2=${y(v)} />
            <text x=${plotW + 10} y=${y(v) + 4}>${formatTick(v)}</text>`,
        )}
        ${this.bars.map((bar, i) => {
          const h = bar.value ? Math.max(2, plotH * (bar.value / scale.top)) : 0;
          return svg`
            <rect class="track" x=${cx(i) - barW / 2} y=${top} width=${barW} height=${plotH}
              rx=${barW / 2} />
            ${h
              ? svg`<rect class="bar ${i === selected ? "selected" : ""}" x=${cx(i) - barW / 2}
                  y=${top + plotH - h} width=${barW} height=${h} rx=${Math.min(barW / 2, h / 2)}
                  fill="url(#${this.gradient})" />`
              : nothing}
            <rect class="hit" x=${slot * i} y="0" width=${slot} height=${H}
              @pointerenter=${() => (this.selected = i)} @click=${() => (this.selected = i)} />`;
        })}
        ${this.ticks.map(
          (tick) => svg`<text x=${cx(tick.index)} y=${H - 8} text-anchor="middle">${tick.label}</text>`,
        )}
      </svg>
      ${selectedBar
        ? html`<div
            class="tip"
            style="left: ${(cx(selected!) / W) * 100}%; top: ${(y(selectedBar.value ?? 0) / H) * 260}px"
          >
            ${selectedBar.tooltip}
          </div>`
        : nothing}
      <div class="visually-hidden" aria-live="polite">${selectedBar?.tooltip ?? ""}</div>
      <div class="visually-hidden">
        <table>
          <caption>${this.caption}</caption>
          ${this.bars.map((bar) => html`<tr><th scope="row">${bar.label}</th><td>${bar.tooltip}</td></tr>`)}
        </table>
      </div>
    `;
  }
}

function formatTick(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

define("kkp-bar-chart", KkpBarChart);
