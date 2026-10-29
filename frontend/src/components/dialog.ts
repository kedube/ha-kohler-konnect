import { css, html, LitElement } from "lit";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { define } from "./define";

/**
 * A modal confirm step — the "are you sure?" a button entity can't ask. A native
 * `<dialog>`, so focus trapping and Escape come with it.
 */
export class KkpDialog extends LitElement {
  static override properties = {
    open: { type: Boolean },
    heading: {},
    body: {},
    confirmLabel: {},
    destructive: { type: Boolean },
  };

  declare open: boolean;
  declare heading: string;
  declare body: string;
  declare confirmLabel: string;
  declare destructive: boolean;

  static override styles = [
    shared,
    css`
      dialog {
        width: min(440px, calc(100vw - 32px));
        padding: 28px;
        border: none;
        border-radius: 22px;
        background: var(--kk-surface);
        color: var(--kk-text);
        box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
      }

      dialog::backdrop {
        background: rgba(0, 0, 0, 0.6);
        backdrop-filter: blur(4px);
      }

      h2 {
        margin: 0 0 12px;
        font-size: 22px;
        font-weight: 400;
      }

      p {
        margin: 0 0 28px;
        font-size: 16px;
        line-height: 1.5;
        color: var(--kk-text-2);
      }

      .actions {
        display: flex;
        gap: 12px;
        justify-content: flex-end;
      }
    `,
  ];

  private get dialog(): HTMLDialogElement | null {
    return this.renderRoot.querySelector("dialog");
  }

  protected override updated(): void {
    const dialog = this.dialog;
    if (!dialog) return;
    if (this.open && !dialog.open) dialog.showModal();
    if (!this.open && dialog.open) dialog.close();
  }

  private answer(confirmed: boolean) {
    this.dispatchEvent(new CustomEvent("kkp-answer", { detail: confirmed }));
  }

  override render() {
    return html`<dialog
      aria-labelledby="title"
      aria-describedby="body"
      @cancel=${(event: Event) => {
        event.preventDefault();
        this.answer(false);
      }}
    >
      <h2 id="title">${this.heading}</h2>
      <p id="body">${this.body}</p>
      <div class="actions">
        <button class="pill" @click=${() => this.answer(false)}>${t("dialog.cancel")}</button>
        <button class="pill ${this.destructive ? "danger" : "filled"}" @click=${() => this.answer(true)}>
          ${this.confirmLabel}
        </button>
      </div>
    </dialog>`;
  }
}

define("kkp-dialog", KkpDialog);
