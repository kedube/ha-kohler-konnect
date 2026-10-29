import { css } from "lit";

// Building blocks every component shares: type, buttons, cards and list rows in the
// Konnect app's shapes, a visible focus ring, and reduced motion.
export const shared = css`
  :host {
    font-family: var(--kk-font);
    color: var(--kk-text);
    -webkit-font-smoothing: antialiased;
    -webkit-tap-highlight-color: transparent;
  }

  * {
    box-sizing: border-box;
  }

  button {
    font: inherit;
    color: inherit;
    border: none;
    background: none;
    padding: 0;
    cursor: pointer;
  }

  button:disabled {
    cursor: default;
  }

  :focus-visible {
    outline: 2px solid var(--kk-accent);
    outline-offset: 3px;
  }

  /* A control reached by keyboard scrolls clear of the sticky header and the bars along
     the bottom (the capsule, the shower's Start). */
  button,
  a,
  input,
  [tabindex] {
    scroll-margin-block: 88px 170px;
  }

  .round {
    display: inline-grid;
    place-items: center;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: var(--kk-control);
    color: var(--kk-text);
    transition: background 160ms var(--kk-ease), transform 160ms var(--kk-ease);
    flex: none;
  }

  .round:hover:not(:disabled):not([aria-disabled="true"]) {
    background: var(--kk-control-hover);
  }

  .round:active:not(:disabled) {
    transform: scale(0.94);
  }

  .round:disabled,
  .round[aria-disabled="true"] {
    opacity: 0.4;
  }

  .round svg {
    width: 24px;
    height: 24px;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 44px;
    padding: 0 28px;
    border-radius: var(--kk-radius-pill);
    border: 1.5px solid var(--kk-text-2);
    font-size: 17px;
    letter-spacing: 0.01em;
    transition: background 160ms var(--kk-ease), border-color 160ms var(--kk-ease),
      transform 160ms var(--kk-ease), opacity 160ms;
  }

  .pill svg {
    width: 22px;
    height: 22px;
    flex: none;
  }

  .pill:hover:not(:disabled) {
    border-color: var(--kk-text);
  }

  .pill:active:not(:disabled) {
    transform: scale(0.97);
  }

  .pill:disabled,
  .pill[aria-disabled="true"] {
    opacity: 0.4;
  }

  .pill.filled {
    border-color: transparent;
    background: var(--kk-accent-fill);
    color: var(--kk-accent-on);
  }

  .pill.filled:hover:not(:disabled) {
    border-color: transparent;
    filter: brightness(1.08);
  }

  .pill.danger {
    border-color: transparent;
    background: var(--kk-danger-fill);
    color: #fff;
  }

  .card {
    background: var(--kk-surface);
    border-radius: var(--kk-radius-card);
    box-shadow: var(--kk-shadow);
  }

  .section-label {
    font-size: 17px;
    font-weight: 400;
    color: var(--kk-text-2);
    margin: 0;
  }

  .eyebrow {
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--kk-text-3);
  }

  .muted {
    color: var(--kk-text-2);
  }

  .accent {
    color: var(--kk-accent-text);
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    border: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 1ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 1ms !important;
    }
  }
`;
