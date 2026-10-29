import { css, html, LitElement, nothing } from "lit";
import { icon } from "../art/icons";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { define } from "./define";

/** The app's header: round back button, centred title, round ⋯ button. */
export class KkpHeader extends LitElement {
  static override properties = {
    heading: {},
    back: { type: Boolean },
    more: { type: Boolean },
    moreLabel: {},
    menu: { type: Boolean },
  };

  declare heading: string;
  declare back: boolean;
  declare more: boolean;
  /** What ⋯ opens, for its accessible name. */
  declare moreLabel: string;
  declare menu: boolean;

  static override styles = [
    shared,
    css`
      :host {
        display: block;
      }

      header {
        display: grid;
        grid-template-columns: 56px 1fr 56px;
        align-items: center;
        min-height: 72px;
        padding: 12px 0;
      }

      h1 {
        margin: 0;
        text-align: center;
        font-size: 20px;
        font-weight: 400;
        letter-spacing: 0.01em;
        overflow-wrap: anywhere;
      }

      .back svg {
        color: var(--kk-accent);
      }

      .end {
        justify-self: end;
      }
    `,
  ];

  private fire(name: string) {
    this.dispatchEvent(new CustomEvent(name, { bubbles: true, composed: true }));
  }

  /** Where focus goes when a new screen replaces the control that had it. */
  focusHeading() {
    this.renderRoot.querySelector<HTMLElement>("h1")?.focus();
  }

  override render() {
    const start = this.back
      ? html`<button class="round back" aria-label=${t("nav.back")} @click=${() => this.fire("kkp-back")}>
          ${icon("back")}
        </button>`
      : this.menu
        ? html`<button class="round" aria-label=${t("nav.menu")} @click=${() => this.fire("kkp-menu")}>
            ${icon("menu")}
          </button>`
        : html`<span></span>`;
    return html`<header>
      ${start}
      <h1 tabindex="-1">${this.heading}</h1>
      ${this.more
        ? html`<button
            class="round end"
            aria-label=${this.moreLabel || t("nav.more")}
            @click=${() => this.fire("kkp-more")}
          >
            ${icon("more")}
          </button>`
        : nothing}
    </header>`;
  }
}

define("kkp-header", KkpHeader);
