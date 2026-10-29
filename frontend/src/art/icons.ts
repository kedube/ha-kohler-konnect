import {
  mdiAccountCircleOutline,
  mdiAlertCircleOutline,
  mdiCheck,
  mdiCheckCircle,
  mdiChevronDown,
  mdiChevronLeft,
  mdiChevronRight,
  mdiClose,
  mdiCloudOutline,
  mdiCogOutline,
  mdiCreationOutline,
  mdiCupWater,
  mdiDotsHorizontal,
  mdiDownload,
  mdiFan,
  mdiFaucet,
  mdiHeart,
  mdiHeartOutline,
  mdiHelpCircleOutline,
  mdiInformationOutline,
  mdiLightbulbOutline,
  mdiLock,
  mdiMenu,
  mdiMinus,
  mdiMusicNote,
  mdiPause,
  mdiPlay,
  mdiPlus,
  mdiRestart,
  mdiStop,
  mdiThermometer,
  mdiTimerOutline,
  mdiTuneVariant,
  mdiWater,
  mdiWaterOutline,
  mdiWeatherNight,
  mdiWifi,
} from "@mdi/js";
import { svg, type SVGTemplateResult } from "lit";

// Generic glyphs come from Material Design Icons. Fixture and product drawings are this
// project's own (see `products.ts`): nothing here is taken from Kohler's app.
export const UI = {
  account: mdiAccountCircleOutline,
  alert: mdiAlertCircleOutline,
  back: mdiChevronLeft,
  check: mdiCheck,
  checkCircle: mdiCheckCircle,
  chevronDown: mdiChevronDown,
  chevronRight: mdiChevronRight,
  close: mdiClose,
  cloud: mdiCloudOutline,
  controls: mdiTuneVariant,
  cup: mdiCupWater,
  download: mdiDownload,
  experiences: mdiCreationOutline,
  fan: mdiFan,
  faucet: mdiFaucet,
  favorite: mdiHeartOutline,
  favoriteFilled: mdiHeart,
  help: mdiHelpCircleOutline,
  info: mdiInformationOutline,
  light: mdiLightbulbOutline,
  lock: mdiLock,
  menu: mdiMenu,
  minus: mdiMinus,
  more: mdiDotsHorizontal,
  music: mdiMusicNote,
  night: mdiWeatherNight,
  pause: mdiPause,
  play: mdiPlay,
  plus: mdiPlus,
  restart: mdiRestart,
  settings: mdiCogOutline,
  stop: mdiStop,
  thermometer: mdiThermometer,
  timer: mdiTimerOutline,
  water: mdiWater,
  waterOutline: mdiWaterOutline,
  wifi: mdiWifi,
} as const;

export type UiIcon = keyof typeof UI;

export function icon(name: UiIcon, label?: string): SVGTemplateResult {
  return svg`<svg viewBox="0 0 24 24" aria-hidden=${label ? "false" : "true"} role=${
    label ? "img" : "presentation"
  }>${label ? svg`<title>${label}</title>` : null}<path fill="currentColor" d=${UI[name]}></path></svg>`;
}

/** A fixture drawing, 48 × 48, for the valve's outlet type code. */
export type FixtureKind =
  | "rainhead"
  | "showerhead"
  | "showerheads"
  | "handshower"
  | "bodyspray"
  | "footspray"
  | "tubfiller"
  | "notplumbed"
  | "outlet";

export function fixtureKind(code: number | null, name = ""): FixtureKind {
  if (code !== null) {
    if (code === 1) return "handshower";
    if (code === 11) return "showerhead";
    if (code === 12) return "showerheads";
    if (code === 21) return "tubfiller";
    if (code === 30) return "notplumbed";
    if (code >= 31 && code <= 39) return "rainhead";
    if (code >= 51 && code <= 53) return "bodyspray";
    if (code >= 61 && code <= 62) return "footspray";
  }
  const lower = name.toLowerCase();
  if (lower.includes("rain")) return "rainhead";
  if (lower.includes("hand")) return "handshower";
  if (lower.includes("body")) return "bodyspray";
  if (lower.includes("foot")) return "footspray";
  if (lower.includes("tub") || lower.includes("bath")) return "tubfiller";
  if (lower.includes("shower")) return "showerhead";
  return "outlet";
}

const SPRAY = "1.6 4.4";

const FIXTURES: Record<FixtureKind, SVGTemplateResult> = {
  rainhead: svg`
    <line x1="24" y1="4" x2="24" y2="11" />
    <rect x="7" y="11" width="34" height="5" rx="2.5" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="11" y1="21" x2="11" y2="44" />
      <line x1="16.2" y1="21" x2="16.2" y2="44" />
      <line x1="21.4" y1="21" x2="21.4" y2="44" />
      <line x1="26.6" y1="21" x2="26.6" y2="44" />
      <line x1="31.8" y1="21" x2="31.8" y2="44" />
      <line x1="37" y1="21" x2="37" y2="44" />
    </g>`,
  showerhead: svg`
    <line x1="4" y1="6" x2="4" y2="14" />
    <line x1="4" y1="10" x2="17" y2="10" />
    <path d="M14 10 H22 L28 20 H8 Z" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="10" y1="24" x2="6" y2="44" />
      <line x1="14" y1="24" x2="12" y2="44" />
      <line x1="18" y1="24" x2="18" y2="44" />
      <line x1="22" y1="24" x2="24" y2="44" />
      <line x1="26" y1="24" x2="30" y2="44" />
    </g>`,
  showerheads: svg`
    <line x1="6" y1="6" x2="6" y2="10" />
    <path d="M3 10 H9 L12 16 H0 Z" transform="translate(4 0)" />
    <line x1="34" y1="6" x2="34" y2="10" />
    <path d="M31 10 H37 L40 16 H28 Z" transform="translate(4 0)" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="7" y1="20" x2="5" y2="42" />
      <line x1="10" y1="20" x2="10" y2="42" />
      <line x1="13" y1="20" x2="15" y2="42" />
      <line x1="35" y1="20" x2="33" y2="42" />
      <line x1="38" y1="20" x2="38" y2="42" />
      <line x1="41" y1="20" x2="43" y2="42" />
    </g>`,
  handshower: svg`
    <circle cx="16" cy="14" r="6.5" />
    <line x1="20.5" y1="18.5" x2="31" y2="35" />
    <path d="M31 35 C34 41 38 43.5 44 43.5" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="11" y1="21" x2="5" y2="34" />
      <line x1="15" y1="22" x2="11" y2="38" />
      <line x1="19" y1="23" x2="17" y2="42" />
    </g>`,
  bodyspray: svg`
    <rect x="5" y="8" width="8" height="32" rx="4" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="18" y1="16" x2="44" y2="16" />
      <line x1="18" y1="24" x2="44" y2="24" />
      <line x1="18" y1="32" x2="44" y2="32" />
    </g>`,
  footspray: svg`
    <rect x="5" y="28" width="8" height="14" rx="4" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="18" y1="31" x2="44" y2="34" />
      <line x1="18" y1="38" x2="44" y2="43" />
    </g>
    <line x1="5" y1="45" x2="44" y2="45" opacity="0.5" />`,
  tubfiller: svg`
    <path d="M5 12 H26 A6 6 0 0 1 32 18 V22" />
    <g class="spray" stroke-dasharray=${SPRAY}>
      <line x1="32" y1="26" x2="32" y2="40" />
    </g>
    <path d="M12 40 H44 V42 A4 4 0 0 1 40 46 H16 A4 4 0 0 1 12 42 Z" />`,
  notplumbed: svg`
    <circle cx="24" cy="24" r="15" stroke-dasharray="3 4" />
    <line x1="14" y1="34" x2="34" y2="14" />`,
  outlet: svg`
    <path d="M24 6 C24 6 12 20 12 29 A12 12 0 0 0 36 29 C36 20 24 6 24 6 Z" />`,
};

export function fixtureIcon(kind: FixtureKind): SVGTemplateResult {
  return svg`<svg class="fixture" viewBox="0 0 48 48" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${FIXTURES[kind]}</svg>`;
}
