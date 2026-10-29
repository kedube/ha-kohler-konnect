import de from "./de.json";
import en from "./en.json";
import es419 from "./es-419.json";
import es from "./es.json";
import fr from "./fr.json";
import it from "./it.json";
import nl from "./nl.json";
import pl from "./pl.json";
import ptBR from "./pt-BR.json";
import sv from "./sv.json";

export type MessageKey = keyof typeof en;
type Messages = Record<string, string>;

// Every language is bundled. Loading one on demand would mean serving more than the one
// file, and a moment of English before it arrived (see `scripts/size-budget.mjs`). Missing
// keys fall back to English.
const LANGUAGES: Record<string, Messages> = {
  en,
  de,
  es,
  "es-419": es419,
  fr,
  it,
  nl,
  pl,
  "pt-BR": ptBR,
  // Portugal's Portuguese reads Brazil's rather than English.
  pt: ptBR,
  sv,
};

let current: Messages = en;
let currentLocale = "en";
let currentMessages = "en";

export function setLanguage(language: string | undefined): void {
  const tag = language || "en";
  const base = tag.split("-")[0];
  currentMessages = LANGUAGES[tag] ? tag : LANGUAGES[base] ? base : "en";
  current = LANGUAGES[currentMessages];
  currentLocale = tag;
}

export function locale(): string {
  return currentLocale;
}

/** The language the panel's text is actually in: English where there is no translation. */
export function messagesLanguage(): string {
  return currentMessages;
}

export function t(key: MessageKey, params?: Record<string, string | number>): string {
  const template = current[key] ?? (en as Messages)[key] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

export const AVAILABLE_LANGUAGES = Object.keys(LANGUAGES);
