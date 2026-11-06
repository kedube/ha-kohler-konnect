import { css, html, LitElement, type PropertyValues } from "lit";
import { shared } from "../theme/shared";
import { define } from "./define";

const ROW = 44;
const ROWS_EACH_SIDE = 2;
const PAGE_ROWS = ROWS_EACH_SIDE * 2;
const SETTLE_MS = 120;

/**
 * A wheel like the Konnect app's amount pickers: a snapping column whose centre row is the
 * value. Scroll it, tap a row, or use the arrow keys.
 *
 * Fires `change` with the new index once the wheel comes to rest, so a spin past twenty
 * rows is one change.
 */
export class KkpWheel extends LitElement {
  static override properties = {
    label: {},
    // By content, so a parent can pass a fresh list each render without moving the wheel.
    options: {
      attribute: false,
      hasChanged: (value?: string[], old?: string[]) => value?.join("\n") !== old?.join("\n"),
    },
    index: { type: Number },
  };

  declare label: string;
  declare options: string[];
  declare index: number;

  private settleTimer?: number;

  constructor() {
    super();
    this.label = "";
    this.options = [];
    this.index = 0;
  }

  static override styles = [
    shared,
    css`
      :host {
        display: block;
        position: relative;
        isolation: isolate;
      }

      .column {
        height: ${ROW * (ROWS_EACH_SIDE * 2 + 1)}px;
        overflow-y: auto;
        overscroll-behavior: contain;
        scroll-snap-type: y mandatory;
        scrollbar-width: none;
        outline: none;
        /* Faint enough to set off the centre, not so faint the outer rows can't be read. */
        mask-image: linear-gradient(rgb(0 0 0 / 45%), #000 45%, #000 55%, rgb(0 0 0 / 45%));
      }

      .column::-webkit-scrollbar {
        display: none;
      }

      /* Room for the first and last rows to reach the centre. */
      .column::before,
      .column::after {
        content: "";
        display: block;
        height: ${ROW * ROWS_EACH_SIDE}px;
      }

      .option {
        display: flex;
        align-items: center;
        justify-content: center;
        height: ${ROW}px;
        padding: 0 8px;
        font-size: 19px;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
        cursor: pointer;
        scroll-snap-align: center;
        user-select: none;
      }

      .band {
        position: absolute;
        z-index: -1;
        inset: ${ROW * ROWS_EACH_SIDE}px 0;
        border-radius: 12px;
        background: var(--kk-control);
        pointer-events: none;
      }

      /* Here rather than on the column, whose fade would hide most of a ring. */
      .column:focus-visible + .band {
        outline: 2px solid var(--kk-accent);
        outline-offset: 1px;
      }

      @media (prefers-reduced-motion: no-preference) {
        .column {
          scroll-behavior: smooth;
        }
      }
    `,
  ];

  override connectedCallback(): void {
    super.connectedCallback();
    // A column taken out of the page loses its place, so it takes it up again on return.
    void this.updateComplete.then(() => this.scrollToIndex("instant"));
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    window.clearTimeout(this.settleTimer);
  }

  protected override updated(changed: PropertyValues): void {
    // A new list is a different wheel: land on its value rather than spin to it.
    if (changed.has("options")) this.scrollToIndex("instant");
    else if (changed.has("index")) this.scrollToIndex("auto");
  }

  private get column(): HTMLElement | null {
    return this.renderRoot.querySelector(".column");
  }

  private clamp(index: number): number {
    return Math.min(this.options.length - 1, Math.max(0, index));
  }

  private scrollToIndex(behavior: ScrollBehavior) {
    const column = this.column;
    if (!column || this.clamp(Math.round(column.scrollTop / ROW)) === this.index) return;
    column.scrollTo({ top: this.index * ROW, behavior });
  }

  private onScroll() {
    window.clearTimeout(this.settleTimer);
    this.settleTimer = window.setTimeout(() => this.settle(), SETTLE_MS);
  }

  private settle() {
    const column = this.column;
    // A hidden column reads as scrolled to the top, which says nothing about the value.
    if (!column?.clientHeight) return;
    this.pick(this.clamp(Math.round(column.scrollTop / ROW)));
  }

  private pick(index: number) {
    window.clearTimeout(this.settleTimer);
    if (index === this.index) return;
    this.index = index;
    this.dispatchEvent(new CustomEvent<number>("change", { detail: index }));
  }

  private onKeydown(event: KeyboardEvent) {
    const moves: Record<string, number> = {
      ArrowDown: this.index + 1,
      ArrowUp: this.index - 1,
      PageDown: this.index + PAGE_ROWS,
      PageUp: this.index - PAGE_ROWS,
      Home: 0,
      End: this.options.length - 1,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    this.pick(this.clamp(moves[event.key]));
  }

  override render() {
    return html`<div
        class="column"
        role="listbox"
        tabindex="0"
        aria-label=${this.label}
        aria-activedescendant="option-${this.index}"
        @scroll=${this.onScroll}
        @keydown=${this.onKeydown}
      >
        ${this.options.map(
          (option, index) => html`<div
            class="option"
            id="option-${index}"
            role="option"
            aria-selected=${String(index === this.index)}
            @click=${() => this.pick(index)}
          >
            ${option}
          </div>`,
        )}
      </div>
      <div class="band"></div>`;
  }
}

define("kkp-wheel", KkpWheel);
