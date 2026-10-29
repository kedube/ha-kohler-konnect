import { svg, type SVGTemplateResult } from "lit";
import type { DeviceKind } from "../types";

// Line drawings of each product, 160 × 160, in the spirit of the Konnect app's home grid:
// thin strokes in the art colour with a single accent detail. Lines classed `spray` or
// `stream` animate while water runs (see `productArtStyles`).

const SHOWER = svg`
  <g transform="rotate(30 92 58)">
    <line x1="113" y1="30" x2="113" y2="2" />
    <rect x="103" y="30" width="20" height="16" rx="4" />
    <ellipse cx="92" cy="50" rx="44" ry="13" />
    <path d="M48 50 V58 A44 13 0 0 0 136 58 V50" />
    <ellipse cx="92" cy="58" rx="36" ry="8.5" opacity="0.45" />
    <g class="spray">
      <line x1="62" y1="76" x2="62" y2="122" />
      <line x1="74" y1="78" x2="74" y2="132" />
      <line x1="86" y1="79" x2="86" y2="136" />
      <line x1="98" y1="79" x2="98" y2="136" />
      <line x1="110" y1="78" x2="110" y2="132" />
      <line x1="122" y1="76" x2="122" y2="122" />
    </g>
  </g>`;

const FAUCET = svg`
  <path d="M108 140 V66 A36 36 0 0 0 36 66 V70" />
  <path d="M97 140 V66 A25 25 0 0 0 47 66 V70" />
  <path d="M33.5 70 H49.5 L51 92 A4 4 0 0 1 47 96.5 H37 A4 4 0 0 1 32 92 Z"
    transform="rotate(-14 41.5 83)" />
  <line x1="36" y1="80" x2="48" y2="77" opacity="0.5" />
  <path class="accent" d="M53 50 A14 14 0 0 1 63 42.5" />
  <path d="M84 140 H122" />
  <circle cx="113" cy="118" r="6" />
  <path d="M117 113 L127 97" />
  <line class="stream" x1="44" y1="101" x2="46" y2="152" />`;

const CONTROLLER = svg`
  <rect x="46" y="16" width="68" height="128" rx="14" />
  <rect x="55" y="27" width="50" height="78" rx="8" />
  <circle cx="80" cy="64" r="20" opacity="0.5" />
  <path class="accent" d="M63.7 75.9 A20 20 0 1 1 96.3 75.9" />
  <circle cx="80" cy="125" r="9" />
  <g class="spray">
    <line x1="72" y1="62" x2="72" y2="68" />
    <line x1="80" y1="58" x2="80" y2="70" />
    <line x1="88" y1="62" x2="88" y2="68" />
  </g>`;

const SMART_FAN = svg`
  <rect x="22" y="22" width="116" height="116" rx="20" />
  <rect x="34" y="34" width="92" height="92" rx="12" opacity="0.4" />
  <circle cx="80" cy="80" r="35" />
  <g class="rotor">
    <path d="M80 80 C 72 66 74 52 88 48 C 92 60 88 72 80 80 Z" />
    <path d="M80 80 C 72 66 74 52 88 48 C 92 60 88 72 80 80 Z" transform="rotate(120 80 80)" />
    <path d="M80 80 C 72 66 74 52 88 48 C 92 60 88 72 80 80 Z" transform="rotate(240 80 80)" />
  </g>
  <circle cx="80" cy="80" r="5" />
  <path class="accent" d="M56 124 H104" />`;

const TOILET = svg`
  <rect x="50" y="18" width="60" height="42" rx="9" />
  <path d="M38 70 H122 A6 6 0 0 1 128 76 C128 102 108 120 84 122 H76 C52 120 32 102 32 76 A6 6 0 0 1 38 70 Z" />
  <path d="M62 122 L58 144 H104 L100 122" />
  <ellipse cx="80" cy="76" rx="38" ry="5" opacity="0.45" />
  <path class="accent" d="M58 39 H102" />`;

const EVOCYCLE = svg`
  <rect x="30" y="14" width="100" height="132" rx="12" />
  <path d="M30 116 H130" opacity="0.45" />
  <line x1="62" y1="34" x2="62" y2="56" />
  <rect x="54" y="56" width="16" height="10" rx="3" />
  <g class="spray">
    <line x1="56" y1="72" x2="52" y2="96" />
    <line x1="62" y1="72" x2="62" y2="98" />
    <line x1="68" y1="72" x2="72" y2="96" />
  </g>
  <path class="accent" d="M96 128 A14 14 0 1 1 118 116 M118 106 V116 H108" />`;

const ICE_BATH = svg`
  <path d="M24 64 H136" />
  <path d="M30 64 V114 A18 18 0 0 0 48 132 H112 A18 18 0 0 0 130 114 V64" />
  <path d="M38 84 C46 79 54 89 62 84 S78 79 86 84 S102 89 110 84 S118 81 122 83" opacity="0.45" />
  <path d="M48 132 L44 144 M112 132 L116 144" />
  <g class="accent">
    <path d="M80 16 V48 M75 19 L80 23 L85 19 M75 45 L80 41 L85 45" />
    <path d="M80 16 V48 M75 19 L80 23 L85 19 M75 45 L80 41 L85 45" transform="rotate(60 80 32)" />
    <path d="M80 16 V48 M75 19 L80 23 L85 19 M75 45 L80 41 L85 45" transform="rotate(120 80 32)" />
  </g>`;

const GENERIC = svg`
  <circle cx="80" cy="80" r="56" opacity="0.4" />
  <path d="M80 40 C80 40 54 72 54 92 A26 26 0 0 0 106 92 C106 72 80 40 80 40 Z" />
  <path class="accent" d="M68 94 A12 12 0 0 0 80 106" />`;

const ART: Record<string, SVGTemplateResult> = {
  valve: SHOWER,
  controller: CONTROLLER,
  faucet: FAUCET,
  smart_fan: SMART_FAN,
  toilet: TOILET,
  evocycle: EVOCYCLE,
  ice_bath: ICE_BATH,
};

export function productArt(kind: DeviceKind): SVGTemplateResult {
  return svg`<svg class="art" viewBox="0 0 160 160" fill="none" stroke="currentColor"
    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${
      ART[kind] ?? GENERIC
    }</svg>`;
}
