import { css, html, LitElement } from "lit";
import { shared } from "../theme/shared";
import { define } from "./define";

export interface TabItem {
  key: string;
  label: string;
}

/** Text tabs with the app's short accent underline: Devices / Usage, Week / Month / Year. */
export class KkpTabs extends LitElement {
  static override properties = {
    items: { attribute: false },
    active: {},
  };

  declare items: TabItem[];
  declare active: string;

  static override styles = [
    shared,
    css`
      :host {
        display: block;
        border-bottom: 1px solid var(--kk-divider);
      }

      div {
        display: flex;
      }

      button {
        flex: 1;
        position: relative;
        padding: 16px 8px 18px;
        font-size: 19px;
        color: var(--kk-text-3);
        transition: color 200ms var(--kk-ease);
      }

      button[aria-selected="true"] {
        color: var(--kk-text);
        font-weight: 500;
      }

      button::after {
        content: "";
        position: absolute;
        left: 50%;
        bottom: -1px;
        width: 40px;
        height: 3px;
        border-radius: 2px;
        background: var(--kk-accent);
        transform: translateX(-50%) scaleX(0);
        transition: transform 240ms var(--kk-ease);
      }

      button[aria-selected="true"]::after {
        transform: translateX(-50%) scaleX(1);
      }
    `,
  ];

  /** Arrow keys, Home and End move between tabs; Enter or Space shows the one moved to. */
  private onKey(event: KeyboardEvent) {
    const tabs = [...this.renderRoot.querySelectorAll<HTMLElement>('[role="tab"]')];
    const index = tabs.indexOf(event.target as HTMLElement);
    const last = tabs.length - 1;
    const moves: Record<string, number> = {
      ArrowRight: index === last ? 0 : index + 1,
      ArrowLeft: index === 0 ? last : index - 1,
      Home: 0,
      End: last,
    };
    if (index < 0 || !(event.key in moves)) return;
    event.preventDefault();
    tabs.forEach((tab, i) => (tab.tabIndex = i === moves[event.key] ? 0 : -1));
    tabs[moves[event.key]].focus();
  }

  override render() {
    return html`<div role="tablist" @keydown=${this.onKey}>
      ${this.items.map(
        (item) => html`<button
          role="tab"
          aria-selected=${item.key === this.active ? "true" : "false"}
          tabindex=${item.key === this.active ? 0 : -1}
          @click=${() =>
            this.dispatchEvent(new CustomEvent("kkp-select", { detail: item.key }))}
        >
          ${item.label}
        </button>`,
      )}
    </div>`;
  }
}

define("kkp-tabs", KkpTabs);
