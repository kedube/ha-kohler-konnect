import { css, html, nothing, type TemplateResult } from "lit";
import { icon } from "../art/icons";

// List rows and banners as plain templates, styled by `rowStyles` / `bannerStyles` in the
// view that uses them. Settings, About and Diagnostics are built from these.

export interface RowOptions {
  label: string;
  value?: unknown;
  description?: string;
  chevron?: boolean;
  expanded?: boolean;
  trailing?: unknown;
  destructive?: boolean;
  disabled?: boolean;
  tag?: string;
  /** Names the row's button, so focus can return to it when its editor closes. */
  key?: string;
  onClick?: () => void;
}

export function row(options: RowOptions): TemplateResult {
  const content = html`
    <span class="text">
      <span class="label">${options.label}${options.tag
        ? html`<span class="tag">${options.tag}</span>`
        : nothing}</span>
      ${options.description ? html`<span class="sub">${options.description}</span>` : nothing}
    </span>
    ${options.value !== undefined && options.value !== null
      ? html`<span class="value">${options.value}</span>`
      : nothing}
    ${options.trailing ?? nothing}
    ${options.chevron
      ? html`<span class="chev ${options.expanded ? "open" : ""}">${icon("chevronRight")}</span>`
      : nothing}
  `;
  if (!options.onClick) return html`<div class="row">${content}</div>`;
  return html`<button
    class="row ${options.destructive ? "destructive" : ""}"
    data-key=${options.key ?? nothing}
    ?disabled=${options.disabled}
    aria-expanded=${options.expanded === undefined ? nothing : String(options.expanded)}
    @click=${options.onClick}
  >
    ${content}
  </button>`;
}

export function group(
  title: string | null,
  body: unknown,
  description?: string,
): TemplateResult {
  return html`<section class="group">
    ${title ? html`<h2 tabindex="-1">${title}</h2>` : nothing}
    ${description ? html`<p class="desc">${description}</p>` : nothing}
    <div class="rows">${body}</div>
  </section>`;
}

/**
 * Arrow keys, Home and End move between a radio group's choices, and Tab reaches only the
 * one moved to; Space or Enter picks it, each choice being a button. Moving never picks: a
 * pick goes to the device at once.
 */
export function radioKeys(event: KeyboardEvent): void {
  const radios = [...(event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="radio"]')];
  const index = radios.indexOf(event.target as HTMLElement);
  const last = radios.length - 1;
  const moves: Record<string, number> = {
    ArrowDown: index === last ? 0 : index + 1,
    ArrowRight: index === last ? 0 : index + 1,
    ArrowUp: index === 0 ? last : index - 1,
    ArrowLeft: index === 0 ? last : index - 1,
    Home: 0,
    End: last,
  };
  if (index < 0 || !(event.key in moves)) return;
  event.preventDefault();
  radios.forEach((radio, i) => (radio.tabIndex = i === moves[event.key] ? 0 : -1));
  radios[moves[event.key]].focus();
}

/** The choice Tab reaches in a radio group: the chosen one, else the first. */
export function radioTabIndex(chosen: boolean, index: number, anyChosen: boolean): number {
  return chosen || (!anyChosen && index === 0) ? 0 : -1;
}

export function banner(
  kind: "info" | "warning" | "danger",
  text: string,
  action?: { label: string; onClick: () => void },
): TemplateResult {
  return html`<div class="banner ${kind}" role=${kind === "danger" ? "alert" : "status"}>
    ${icon(kind === "info" ? "info" : "alert")}
    <span>${text}</span>
    ${action
      ? html`<button class="banner-action" @click=${action.onClick}>${action.label}</button>`
      : nothing}
  </div>`;
}

export const rowStyles = css`
  .group {
    margin: 0 0 8px;
  }

  .group h2 {
    margin: 30px 4px 12px;
    font-size: 22px;
    font-weight: 400;
  }

  .group .desc {
    margin: -4px 4px 14px;
    font-size: 15px;
    line-height: 1.45;
    color: var(--kk-text-2);
  }

  .rows {
    overflow: hidden;
    border-radius: var(--kk-radius-card);
    background: var(--kk-surface-row);
    box-shadow: var(--kk-shadow);
  }

  /* Inside the row: the group's rounded corners clip anything drawn beyond it. */
  button.row:focus-visible {
    outline-offset: -3px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    min-height: 64px;
    padding: 14px 20px;
    border-top: 1px solid var(--kk-divider);
    font-size: 18px;
    text-align: left;
    color: var(--kk-text);
  }

  .rows > .row:first-child,
  .rows > :first-child .row {
    border-top: none;
  }

  button.row {
    transition: background 160ms var(--kk-ease);
  }

  button.row:hover:not(:disabled) {
    background: var(--kk-control);
  }

  button.row:disabled {
    opacity: 0.5;
  }

  .row .text {
    flex: 1;
    min-width: 0;
  }

  /* Long words in other languages wrap rather than run out of the row. */
  .row .label {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    overflow-wrap: anywhere;
    hyphens: auto;
  }

  .row .sub {
    display: block;
    margin-top: 4px;
    font-size: 14px;
    line-height: 1.4;
    color: var(--kk-text-2);
  }

  .row .value {
    color: var(--kk-text-2);
    font-size: 17px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .row > svg {
    width: 24px;
    height: 24px;
    flex: none;
    color: var(--kk-text-2);
  }

  .row .chev {
    display: grid;
    color: var(--kk-text-2);
    transition: transform 200ms var(--kk-ease);
  }

  .row .chev svg {
    width: 22px;
    height: 22px;
  }

  .row .chev.open {
    transform: rotate(90deg);
  }

  .row.destructive .label {
    color: var(--kk-destructive);
  }

  .tag {
    padding: 2px 8px;
    border-radius: var(--kk-radius-pill);
    background: var(--kk-control);
    color: var(--kk-text-2);
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .editor {
    padding: 6px 20px 22px;
    background: var(--kk-surface-row);
  }

  .choices {
    display: grid;
  }

  .choice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 56px;
    padding: 0 4px;
    font-size: 17px;
    text-align: left;
    border-top: 1px solid var(--kk-divider);
  }

  .choice:first-child {
    border-top: none;
  }

  .radio {
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    border: 2px solid var(--kk-text-2);
    flex: none;
  }

  .choice[aria-checked="true"] .radio {
    border-color: var(--kk-accent);
  }

  .choice[aria-checked="true"] .radio::after {
    content: "";
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--kk-accent);
  }

  .editor-actions {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    margin-top: 18px;
  }

  .dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    margin-right: 8px;
    border-radius: 50%;
    background: var(--kk-text-3);
    vertical-align: 1px;
  }

  .dot.good {
    background: var(--kk-success);
  }

  .dot.warn {
    background: var(--kk-warning);
  }

  .dot.bad {
    background: var(--kk-danger);
  }
`;

export const bannerStyles = css`
  .banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    border-radius: 14px;
    background: var(--kk-surface);
    font-size: 15px;
    line-height: 1.4;
  }

  .banner svg {
    width: 22px;
    height: 22px;
    flex: none;
  }

  .banner span {
    flex: 1;
  }

  .banner.info svg {
    color: var(--kk-text);
  }

  .banner.warning svg {
    color: var(--kk-warning);
  }

  .banner.danger {
    background: color-mix(in srgb, var(--kk-danger) 14%, var(--kk-surface));
  }

  .banner.danger svg {
    color: var(--kk-danger);
  }

  .banner-action {
    padding: 8px 16px;
    border-radius: var(--kk-radius-pill);
    border: 1.5px solid currentColor;
    font-size: 15px;
  }
`;
