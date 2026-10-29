import { css, html, LitElement } from "lit";
import { icon, type UiIcon } from "../art/icons";
import { shared } from "../theme/shared";
import { define } from "./define";

export interface CapsuleItem {
  key: string;
  label: string;
  icon: UiIcon;
}

/** The floating capsule tab bar at the foot of a device screen. */
export class KkpCapsule extends LitElement {
  static override properties = {
    items: { attribute: false },
    active: {},
  };

  declare items: CapsuleItem[];
  declare active: string;

  static override styles = [
    shared,
    css`
      :host {
        display: flex;
        justify-content: center;
        position: sticky;
        bottom: 0;
        padding: 20px 16px max(20px, env(safe-area-inset-bottom));
        background: linear-gradient(to top, var(--kk-canvas) 55%, transparent);
        pointer-events: none;
        z-index: 2;
      }

      nav {
        display: flex;
        gap: 4px;
        padding: 6px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-nav);
        box-shadow: var(--kk-shadow);
        pointer-events: auto;
      }

      button {
        display: grid;
        justify-items: center;
        gap: 4px;
        min-width: 104px;
        padding: 10px 18px 8px;
        border-radius: var(--kk-radius-pill);
        color: var(--kk-text);
        font-size: 14px;
        transition: background 200ms var(--kk-ease), color 200ms var(--kk-ease);
      }

      button svg {
        width: 26px;
        height: 26px;
      }

      button[aria-current="page"] {
        background: var(--kk-nav-active);
        color: var(--kk-accent);
        font-weight: 500;
      }

      /* Three items still fit a 320 px screen. */
      @media (max-width: 359px) {
        button {
          min-width: 0;
          padding: 10px 12px 8px;
        }
      }
    `,
  ];

  override render() {
    return html`<nav>
      ${this.items.map(
        (item) => html`<button
          aria-current=${item.key === this.active ? "page" : "false"}
          @click=${() =>
            this.dispatchEvent(new CustomEvent("kkp-select", { detail: item.key }))}
        >
          ${icon(item.icon)}<span>${item.label}</span>
        </button>`,
      )}
    </nav>`;
  }
}

define("kkp-capsule", KkpCapsule);
