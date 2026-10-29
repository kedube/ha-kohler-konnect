import { css, html, nothing } from "lit";
import { icon } from "../art/icons";
import { define } from "../components/define";
import { DeviceView } from "../context";
import { t } from "../i18n";
import { shared } from "../theme/shared";
import { selectChoices } from "../util/entity";

/**
 * Favorites or experiences as cards. Each is an option of the device's `Favorite` or
 * `Experience` select; starting one selects it, and `Off` stops it, as the integration's
 * selects define.
 */
export class KkpScenes extends DeviceView {
  static override properties = {
    ...DeviceView.properties,
    role: {},
    compact: { type: Boolean },
  };

  declare role: "favorite" | "experience";
  declare compact: boolean;

  static override styles = [
    shared,
    css`
      :host {
        display: block;
      }

      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr));
        gap: 12px;
      }

      :host([compact]) .grid {
        grid-template-columns: 1fr;
        gap: 8px;
      }

      .scene {
        position: relative;
        display: grid;
        grid-template-columns: auto 1fr auto;
        align-items: center;
        gap: 14px;
        min-height: 76px;
        padding: 16px 18px;
        border-radius: var(--kk-radius-tile);
        background: var(--kk-surface);
        box-shadow: var(--kk-shadow);
        text-align: left;
        overflow: hidden;
        transition: transform 180ms var(--kk-ease), background 200ms var(--kk-ease);
      }

      .scene:not(.active):hover:not(:disabled) {
        transform: translateY(-1px);
        background: var(--kk-control);
      }

      .scene.active {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .scene-icon {
        display: grid;
        place-items: center;
        width: 44px;
        height: 44px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-accent);
      }

      .active .scene-icon {
        background: rgba(255, 255, 255, 0.2);
        color: #fff;
      }

      .scene-icon svg {
        width: 22px;
        height: 22px;
      }

      .scene-name {
        font-size: 17px;
        line-height: 1.3;
      }

      .scene-action {
        padding: 7px 16px;
        border-radius: var(--kk-radius-pill);
        border: 1.5px solid var(--kk-text-2);
        font-size: 14px;
      }

      .active .scene-action {
        border-color: rgba(255, 255, 255, 0.85);
      }

      .empty {
        display: grid;
        justify-items: center;
        gap: 8px;
        padding: 48px 24px;
        text-align: center;
        color: var(--kk-text-2);
        border-radius: var(--kk-radius-card);
        background: var(--kk-surface);
      }

      .empty svg {
        width: 36px;
        height: 36px;
        color: var(--kk-text-3);
      }

      .empty strong {
        font-weight: 400;
        color: var(--kk-text);
        font-size: 18px;
      }
    `,
  ];

  private choose(option: string, active: boolean) {
    const entityId = this.entityId(this.role);
    if (!entityId) return;
    void this.queued("scene", "select", "select_option", {
      entity_id: entityId,
      option: active ? "Off" : option,
    });
  }

  override render() {
    const entity = this.entity(this.role);
    const choices = selectChoices(entity);
    const isFavorite = this.role === "favorite";
    if (!choices.length) {
      return html`<div class="empty">
        ${icon(isFavorite ? "favorite" : "experiences")}
        <strong>${isFavorite ? t("scenes.empty_favorites") : t("scenes.empty_experiences")}</strong>
        <span>${isFavorite ? t("scenes.empty_favorites_body") : t("scenes.empty_experiences_body")}</span>
      </div>`;
    }
    return html`<div class="grid">
      ${choices.map((option) => {
        const active = entity?.state === option;
        return html`<button
          class="scene ${active ? "active" : ""}"
          aria-pressed=${String(active)}
          @click=${() => this.choose(option, active)}
        >
          <span class="scene-icon">${icon(isFavorite ? (active ? "favoriteFilled" : "favorite") : "experiences")}</span>
          <span class="scene-name">${option}</span>
          <span class="scene-action">${active ? t("scenes.running") : t("scenes.start")}</span>
        </button>`;
      })}
    </div>${nothing}`;
  }
}

define("kkp-scenes", KkpScenes);
