import { css, html, LitElement, nothing } from "lit";
import { fixtureIcon, icon, type FixtureKind } from "../art/icons";
import { t } from "../i18n";
import { artStyles } from "../theme/art";
import { shared } from "../theme/shared";
import { define } from "./define";

export type OutletState = "off" | "staged" | "on";

/**
 * A round fixture button with its name beneath, as on the app's Outlets card.
 * `staged` is chosen for a shower not yet started; `on` is running water.
 */
export class KkpOutlet extends LitElement {
  static override properties = {
    name: {},
    kind: {},
    variant: {},
    state: {},
    locked: { type: Boolean },
    disabled: { type: Boolean },
  };

  declare name: string;
  declare kind: FixtureKind;
  declare variant: string | null;
  declare state: OutletState;
  declare locked: boolean;
  declare disabled: boolean;

  constructor() {
    super();
    this.state = "off";
    this.variant = null;
    this.locked = false;
    this.disabled = false;
  }

  static override styles = [
    shared,
    artStyles,
    css`
      :host {
        display: block;
      }

      button {
        display: grid;
        justify-items: center;
        gap: 12px;
        width: 100%;
      }

      .circle {
        position: relative;
        display: grid;
        place-items: center;
        width: 72px;
        height: 72px;
        border-radius: 50%;
        background: var(--kk-outlet);
        color: var(--kk-text);
        transition: background 220ms var(--kk-ease), color 220ms var(--kk-ease),
          box-shadow 220ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      button:hover:not(:disabled) .circle {
        background: var(--kk-outlet-hover);
      }

      button:active:not(:disabled) .circle {
        transform: scale(0.95);
      }

      .circle svg.fixture {
        width: 40px;
        height: 40px;
      }

      .staged .circle,
      button.staged:hover:not(:disabled) .circle {
        background: var(--kk-accent-soft);
        color: var(--kk-accent);
        box-shadow: inset 0 0 0 2px var(--kk-accent);
      }

      .on .circle,
      button.on:hover:not(:disabled) .circle {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .on .circle::after {
        content: "";
        position: absolute;
        inset: -6px;
        border-radius: 50%;
        border: 2px solid var(--kk-accent);
        opacity: 0;
        animation: kk-pulse 2.4s var(--kk-ease) infinite;
      }

      .on.running .fixture .spray line {
        stroke: currentColor;
      }

      .badge {
        position: absolute;
        right: -2px;
        bottom: -2px;
        display: grid;
        place-items: center;
        width: 26px;
        height: 26px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-text-2);
      }

      .badge svg {
        width: 15px;
        height: 15px;
      }

      .name {
        font-size: 17px;
        text-align: center;
        line-height: 1.25;
      }

      .variant {
        margin-top: -8px;
        font-size: 13px;
        color: var(--kk-text-3);
      }

      button:disabled {
        opacity: 0.45;
      }

      button.locked:disabled {
        opacity: 0.75;
      }

      @keyframes kk-pulse {
        0% {
          transform: scale(0.92);
          opacity: 0.7;
        }
        70%,
        100% {
          transform: scale(1.18);
          opacity: 0;
        }
      }
    `,
  ];

  override render() {
    const classes = [this.state, this.state === "on" ? "running" : "", this.locked ? "locked" : ""];
    return html`<button
      class=${classes.join(" ")}
      aria-pressed=${this.state === "off" ? "false" : "true"}
      ?disabled=${this.disabled || this.locked}
      @click=${() => this.dispatchEvent(new CustomEvent("kkp-toggle"))}
    >
      <span class="circle">
        ${fixtureIcon(this.kind)}
        ${this.locked ? html`<span class="badge">${icon("lock", t("shower.locked"))}</span>` : nothing}
      </span>
      <span class="name">${this.name}</span>
      ${this.variant ? html`<span class="variant">${this.variant}</span>` : nothing}
    </button>`;
  }
}

define("kkp-outlet", KkpOutlet);
