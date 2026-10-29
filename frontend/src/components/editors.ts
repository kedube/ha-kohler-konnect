import { html, type TemplateResult } from "lit";
import { t } from "../i18n";
import { radioKeys, radioTabIndex } from "./rows";
import "./slider";

// The editors a settings row opens in place, styled by `rowStyles`. Settings and the
// generic view share them, so a select or number behaves the same wherever it is shown.

export interface ChoiceEditor {
  label: string;
  choices: string[];
  current: string;
  /** How a choice reads: Home Assistant's translation of an option, say. */
  display: (choice: string) => unknown;
  onPick: (choice: string) => void;
}

/** A select's choices as a radio group. A pick goes to the device at once. */
export function choiceEditor(editor: ChoiceEditor): TemplateResult {
  const known = editor.choices.includes(editor.current);
  return html`<div class="editor">
    <div class="choices" role="radiogroup" aria-label=${editor.label} @keydown=${radioKeys}>
      ${editor.choices.map(
        (choice, index) => html`<button
          class="choice"
          role="radio"
          aria-checked=${String(choice === editor.current)}
          tabindex=${radioTabIndex(choice === editor.current, index, known)}
          @click=${() => editor.onPick(choice)}
        >
          <span>${editor.display(choice)}</span><span class="radio"></span>
        </button>`,
      )}
    </div>
  </div>`;
}

export interface NumberEditor {
  label: string;
  value: number | null;
  /** The value moved to but not yet saved; null until the slider moves. */
  draft: number | null;
  min: number;
  max: number;
  step: number;
  unit: string;
  variant: "temperature" | "flow";
  onDraft: (value: number) => void;
  onCancel: () => void;
  onSave: (value: number) => void;
}

/** A number's slider, with Cancel and Save: nothing goes to the device until Save. */
export function numberEditor(editor: NumberEditor): TemplateResult {
  const draft = editor.draft;
  return html`<div class="editor">
    <kkp-slider
      compact
      label=${editor.label}
      .variant=${editor.variant}
      .value=${draft ?? editor.value}
      .min=${editor.min}
      .max=${editor.max}
      .step=${editor.step}
      unit=${editor.unit}
      @change=${(event: CustomEvent<number>) => editor.onDraft(event.detail)}
    ></kkp-slider>
    <div class="editor-actions">
      <button class="pill" @click=${editor.onCancel}>${t("settings.cancel")}</button>
      <button
        class="pill filled"
        ?disabled=${draft === null || draft === editor.value}
        @click=${() => draft !== null && editor.onSave(draft)}
      >
        ${t("settings.save")}
      </button>
    </div>
  </div>`;
}
