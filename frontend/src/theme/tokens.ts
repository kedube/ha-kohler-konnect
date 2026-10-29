import { css, unsafeCSS } from "lit";

// Two token sets on the root element, switched by `theme="dark"` / `theme="light"`. Custom
// properties inherit through shadow roots, so every component reads the same set.
//
// Dark follows the Konnect app as measured (`docs/handoff/panel-design-reference.md`);
// light is our own counterpart to it, since the app has no light mode.
export const TEMPERATURE_GRADIENT =
  "linear-gradient(90deg, #b4edfd 0%, #93daf3 10%, #72c8e9 20%, #52b0db 30%, #4b7cbb 40%, #3f4699 50%, #442a7c 60%, #713964 70%, #a13b45 80%, #c2382f 90%, #e43425 100%)";

export const tokens = css`
  :host {
    --kk-font: "Helvetica Now Text", "Helvetica Neue", Helvetica, Roboto, Arial,
      sans-serif;
    --kk-radius-card: 16px;
    --kk-radius-tile: 12px;
    --kk-radius-pill: 999px;
    --kk-temp-gradient: ${unsafeCSS(TEMPERATURE_GRADIENT)};
    --kk-cool: #43769b;
    --kk-warm: #b037fb;
    --kk-hot: #fd0002;
    --kk-ease: cubic-bezier(0.2, 0.7, 0.2, 1);
    /* Behind white text on a destructive button, in either theme. */
    --kk-danger-fill: #d62f2f;
  }

  :host([theme="dark"]) {
    color-scheme: dark;
    --kk-canvas: #000000;
    --kk-surface: #131517;
    --kk-surface-row: #121419;
    --kk-surface-raised: #171a1c;
    --kk-control: #202228;
    --kk-control-hover: #2a2d34;
    --kk-outlet: #353738;
    --kk-outlet-hover: #404244;
    --kk-divider: #222427;
    --kk-grid: #202329;
    --kk-nav: #202020;
    --kk-nav-active: #0a0a0a;
    --kk-sheet: #152226;
    --kk-scrim: rgba(0, 0, 0, 0.72);
    --kk-text: #f1f2f4;
    --kk-text-2: #9e9ea4;
    --kk-text-3: #84858b;
    --kk-accent: #00cdd4;
    --kk-accent-text: #00cccc;
    --kk-accent-fill: #2a7f81;
    --kk-accent-on: #ffffff;
    --kk-accent-soft: rgba(0, 205, 212, 0.14);
    --kk-accent-line: rgba(0, 205, 212, 0.45);
    --kk-success: #72ff66;
    --kk-warning: #ffb020;
    --kk-danger: #ff4d4d;
    --kk-destructive: #f26a2e;
    --kk-bar-top: #51d0d3;
    --kk-bar-bottom: #32707f;
    --kk-bar-track: #1f1f1f;
    --kk-art: #ffffff;
    --kk-thumb-edge: transparent;
    --kk-shadow: none;
  }

  :host([theme="light"]) {
    color-scheme: light;
    --kk-canvas: #f3f3f1;
    --kk-surface: #ffffff;
    --kk-surface-row: #ffffff;
    --kk-surface-raised: #ffffff;
    --kk-control: #ebebe8;
    --kk-control-hover: #e1e1dd;
    --kk-outlet: #e6e6e3;
    --kk-outlet-hover: #dcdcd8;
    --kk-divider: #e0e0dc;
    --kk-grid: #e6e6e2;
    --kk-nav: #ffffff;
    --kk-nav-active: #ecedea;
    --kk-sheet: #e7f1f1;
    --kk-scrim: rgba(17, 19, 22, 0.42);
    --kk-text: #111316;
    --kk-text-2: #5c5f66;
    --kk-text-3: #686c73;
    --kk-accent: #00838a;
    --kk-accent-text: #00747a;
    --kk-accent-fill: #007a7e;
    --kk-accent-on: #ffffff;
    --kk-accent-soft: rgba(0, 131, 138, 0.1);
    --kk-accent-line: rgba(0, 131, 138, 0.45);
    --kk-success: #1f9d3a;
    --kk-warning: #b26a00;
    --kk-danger: #c53628;
    --kk-destructive: #c2410c;
    --kk-bar-top: #2bb7bb;
    --kk-bar-bottom: #2a6a78;
    --kk-bar-track: #ececea;
    --kk-art: #111316;
    /* A white thumb on the pale cold end of the gradient needs an edge. */
    --kk-thumb-edge: #5c5f66;
    --kk-shadow: 0 1px 2px rgba(17, 19, 22, 0.06), 0 8px 24px rgba(17, 19, 22, 0.06);
  }
`;

/** The token values per theme, for tests that check contrast. */
export const THEME_COLOURS = {
  dark: {
    canvas: "#000000",
    surface: "#131517",
    text: "#f1f2f4",
    text2: "#9e9ea4",
    text3: "#84858b",
    accentText: "#00cccc",
    accentFill: "#2a7f81",
    accentOn: "#ffffff",
    destructive: "#f26a2e",
    danger: "#ff4d4d",
    dangerFill: "#d62f2f",
  },
  light: {
    canvas: "#f3f3f1",
    surface: "#ffffff",
    text: "#111316",
    text2: "#5c5f66",
    text3: "#686c73",
    accentText: "#00747a",
    accentFill: "#007a7e",
    accentOn: "#ffffff",
    destructive: "#c2410c",
    danger: "#c53628",
    dangerFill: "#d62f2f",
  },
} as const;
