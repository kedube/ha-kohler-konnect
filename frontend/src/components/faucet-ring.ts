import { css, html, LitElement } from "lit";
import { productArt } from "../art/products";
import { artStyles } from "../theme/art";
import { shared } from "../theme/shared";
import { define } from "./define";

export type FaucetFlow = "off" | "on" | "dispensing";

/** The faucet drawn in a ring, as on the app's faucet screen; it comes alive with water. */
export class KkpFaucetRing extends LitElement {
  static override properties = {
    flow: {},
  };

  declare flow: FaucetFlow;

  constructor() {
    super();
    this.flow = "off";
  }

  static override styles = [
    shared,
    artStyles,
    css`
      :host {
        display: grid;
        place-items: center;
      }

      .ring {
        position: relative;
        display: grid;
        place-items: center;
        width: min(70vw, 260px);
        aspect-ratio: 1;
        border-radius: 50%;
        box-shadow: inset 0 0 0 3px var(--kk-control), 0 0 40px rgba(0, 0, 0, 0.35);
        transition: box-shadow 400ms var(--kk-ease);
      }

      .ring.running {
        box-shadow: inset 0 0 0 3px var(--kk-accent-line), 0 0 60px var(--kk-accent-soft);
      }

      .art-wrap {
        width: 58%;
        aspect-ratio: 1;
      }

      .sweep {
        position: absolute;
        inset: -3px;
        border-radius: 50%;
        background: conic-gradient(from 0deg, transparent 0 70%, var(--kk-accent) 100%);
        -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 0);
        mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 0);
        animation: kk-spin 1.6s linear infinite;
      }

      .halo {
        position: absolute;
        inset: 14%;
        border-radius: 50%;
        background: radial-gradient(circle, var(--kk-accent-soft), transparent 70%);
        opacity: 0;
        transition: opacity 600ms var(--kk-ease);
      }

      .running .halo {
        opacity: 1;
        animation: kk-breathe 4.8s ease-in-out infinite;
      }

      @keyframes kk-spin {
        to {
          transform: rotate(1turn);
        }
      }

      @keyframes kk-breathe {
        50% {
          transform: scale(1.08);
        }
      }
    `,
  ];

  override render() {
    const running = this.flow !== "off";
    return html`<div class="ring ${running ? "running" : ""}">
      <div class="halo"></div>
      ${this.flow === "dispensing" ? html`<div class="sweep"></div>` : null}
      <div class="art-wrap">${productArt("faucet")}</div>
    </div>`;
  }
}

define("kkp-faucet-ring", KkpFaucetRing);
