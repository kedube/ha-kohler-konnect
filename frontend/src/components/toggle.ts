import { css, html, LitElement } from "lit";
import { shared } from "../theme/shared";
import { define } from "./define";

/** The app's teal switch. Fires `change` with the new state; the caller owns `checked`. */
export class KkpToggle extends LitElement {
  static override properties = {
    checked: { type: Boolean },
    disabled: { type: Boolean },
    label: {},
  };

  declare checked: boolean;
  declare disabled: boolean;
  declare label: string;

  static override styles = [
    shared,
    css`
      :host {
        display: inline-block;
      }

      button {
        position: relative;
        width: 64px;
        height: 36px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        transition: background 200ms var(--kk-ease);
      }

      button[aria-checked="false"] {
        box-shadow: inset 0 0 0 1.5px var(--kk-text-2);
      }

      button[aria-checked="true"] {
        background: var(--kk-accent-fill);
      }

      span {
        position: absolute;
        top: 4px;
        left: 4px;
        width: 40px;
        height: 28px;
        border-radius: var(--kk-radius-pill);
        background: #fff;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
        transition: transform 220ms var(--kk-ease);
      }

      button[aria-checked="true"] span {
        transform: translateX(16px);
      }

      button:disabled {
        opacity: 0.4;
      }
    `,
  ];

  override render() {
    return html`<button
      role="switch"
      aria-checked=${this.checked ? "true" : "false"}
      aria-label=${this.label ?? ""}
      ?disabled=${this.disabled}
      @click=${() =>
        this.dispatchEvent(new CustomEvent("change", { detail: !this.checked }))}
    >
      <span></span>
    </button>`;
  }
}

define("kkp-toggle", KkpToggle);
