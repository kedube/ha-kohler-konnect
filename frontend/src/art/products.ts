import { svg, type SVGTemplateResult } from "lit";
import type { DeviceKind } from "../types";

// Line drawings of each product, 160 × 160, in the spirit of the Konnect app's home grid:
// thin strokes in the art colour with a single accent detail, each drawn from the product's
// own shape and centred in the frame. Lines classed `spray` or `stream` animate while the
// device runs (see `theme/art.ts`).

/**
 * A showerhead on its arm, drawn about the head's centre and tilted. A scaled head takes a
 * matching stroke width, so its lines stay as thick as the rest of the drawing.
 */
function showerhead(transform: string, strokeWidth = 2.2): SVGTemplateResult {
  return svg`
  <g transform=${transform} stroke-width=${strokeWidth}>
    <line x1="0" y1="-22" x2="0" y2="-78" />
    <rect x="-8" y="-22" width="16" height="13" rx="4" />
    <ellipse cx="0" cy="0" rx="36" ry="10.5" />
    <path d="M-36 0 V7 A36 10.5 0 0 0 36 7 V0" />
    <ellipse cx="0" cy="7" rx="29" ry="7" opacity="0.45" />
    <g class="spray">
      <line x1="-25" y1="22" x2="-25" y2="60" />
      <line x1="-15" y1="23" x2="-15" y2="68" />
      <line x1="-5" y1="24" x2="-5" y2="72" />
      <line x1="5" y1="24" x2="5" y2="72" />
      <line x1="15" y1="23" x2="15" y2="68" />
      <line x1="25" y1="22" x2="25" y2="60" />
    </g>
  </g>`;
}

const SHOWER = showerhead("translate(86 81) rotate(30)");

const FAUCET = svg`
  <path d="M108 140 V66 A36 36 0 0 0 36 66 V70" />
  <path d="M97 140 V66 A25 25 0 0 0 47 66 V70" />
  <path d="M33.5 70 H49.5 L51 92 A4 4 0 0 1 47 96.5 H37 A4 4 0 0 1 32 92 Z" />
  <line x1="35" y1="79" x2="48" y2="79" opacity="0.5" />
  <path class="accent" d="M53 50 A14 14 0 0 1 63 42.5" />
  <path d="M84 140 H122" />
  <circle cx="113" cy="118" r="6" />
  <path d="M117 113 L127 97" />
  <line class="stream" x1="41.5" y1="101" x2="41.5" y2="152" />`;

// A ceiling exhaust fan from the side: the housing above the ceiling, the trim plate and its
// chamfered grille, the light lens, and air drawn up into it.
const SMART_FAN = svg`
  <line x1="12" y1="44" x2="148" y2="44" opacity="0.45" />
  <rect x="48" y="26" width="64" height="18" rx="2" opacity="0.45" />
  <rect x="22" y="44" width="116" height="12" rx="2" />
  <path d="M28 56 L42 70 H118 L132 56" />
  <g opacity="0.45">
    <line x1="52" y1="60" x2="52" y2="66" />
    <line x1="62" y1="60" x2="62" y2="66" />
    <line x1="72" y1="60" x2="72" y2="66" />
    <line x1="88" y1="60" x2="88" y2="66" />
    <line x1="98" y1="60" x2="98" y2="66" />
    <line x1="108" y1="60" x2="108" y2="66" />
  </g>
  <path class="accent" d="M60 74 H100" />
  <g class="spray">
    <line x1="44" y1="118" x2="44" y2="86" />
    <line x1="62" y1="130" x2="62" y2="86" />
    <line x1="80" y1="134" x2="80" y2="86" />
    <line x1="98" y1="130" x2="98" y2="86" />
    <line x1="116" y1="118" x2="116" y2="86" />
  </g>`;

// The Numi 2.0 from the side: one sculpted body with a slanted front, the lid and seat, the
// rear housing, and the ambient light on the floor beneath it.
const NUMI = svg`
  <path d="M104 117 V37 A6 6 0 0 1 110 31 H126 A6 6 0 0 1 132 37 V117" />
  <path d="M104 33 H31 A3 3 0 0 0 28 36 V38 A3 3 0 0 0 31 41 H104" />
  <path d="M32 41 L50 117 H132" />
  <line x1="34" y1="46" x2="104" y2="46" opacity="0.45" />
  <path d="M58 117 V123 H124 V117" />
  <path class="accent" d="M54 129 H128" />`;

// The Purewash E930 seat from above: its closed lid, the wash unit at the back and the side
// panel's buttons.
const PUREWASH = svg`
  <g transform="translate(79 82) rotate(-22) scale(0.84)" stroke-width="2.62">
    <path d="M34 -24 H-22 C-58 -24 -78 -12 -78 0 C-78 12 -58 24 -22 24 H34" />
    <path d="M-78 0 V7 C-78 19 -58 31 -22 31 H34" />
    <path d="M-62 0 C-62 -8 -46 -14 -24 -14 H22 C28 -14 28 14 22 14 H-24 C-46 14 -62 8 -62 0 Z"
      opacity="0.45" />
    <path d="M34 -32 H54 A6 6 0 0 1 60 -26 V32 A6 6 0 0 1 54 38 H34 Z" />
    <line x1="40" y1="-24" x2="40" y2="30" opacity="0.45" />
    <path d="M38 38 V46 A4 4 0 0 0 42 50 H58 A4 4 0 0 0 62 46 V36" />
    <g class="accent">
      <circle cx="45" cy="44" r="1.6" />
      <circle cx="51" cy="44" r="1.6" />
      <circle cx="57" cy="44" r="1.6" />
    </g>
  </g>`;

// The EvoCycle's head, with the cycle that recirculates its water.
const EVOCYCLE = svg`
  ${showerhead("translate(69 72) rotate(30) scale(0.84)", 2.62)}
  <g class="accent">
    <path d="M105 120 A16 16 0 0 1 133 114 M133 114 L134 106 M133 114 L126 112" />
    <path d="M135 130 A16 16 0 0 1 107 136 M107 136 L106 144 M107 136 L114 138" />
  </g>`;

// The Ice Bath's plunge tub: its rim, the water, and the headrest at one end.
const ICE_BATH = svg`
  <path d="M20 60 H112 L140 40 H48 Z" />
  <path d="M20 60 V114 A6 6 0 0 0 26 120 H106 A6 6 0 0 0 112 114 V60" />
  <path d="M112 120 L134 104 A6 6 0 0 0 140 98 V40" />
  <path d="M42 56 H98 L114 45 H58 Z" opacity="0.45" />
  <path class="accent" d="M48 52 C54 49 60 55 66 52 S78 49 84 52 S96 55 102 51" />
  <rect x="104" y="46" width="22" height="9" rx="4.5" transform="rotate(-35 115 50.5)" />`;

const GENERIC = svg`
  <circle cx="80" cy="80" r="56" opacity="0.4" />
  <path d="M80 40 C80 40 54 72 54 92 A26 26 0 0 0 106 92 C106 72 80 40 80 40 Z" />
  <path class="accent" d="M68 94 A12 12 0 0 0 80 106" />`;

const ART: Record<string, SVGTemplateResult> = {
  valve: SHOWER,
  // An Anthem+ system is drawn as its shower, as the Konnect app draws it.
  controller: SHOWER,
  faucet: FAUCET,
  smart_fan: SMART_FAN,
  toilet: NUMI,
  evocycle: EVOCYCLE,
  ice_bath: ICE_BATH,
};

/** The drawing of a device: by its kind and, where one kind covers two products, its model. */
export function productArt(kind: DeviceKind, model?: string | null): SVGTemplateResult {
  const drawing = kind === "toilet" && model?.startsWith("Purewash") ? PUREWASH : (ART[kind] ?? GENERIC);
  return svg`<svg class="art" viewBox="0 0 160 160" fill="none" stroke="currentColor"
    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${drawing}</svg>`;
}
