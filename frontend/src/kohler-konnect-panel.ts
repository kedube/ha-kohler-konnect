import { css, html, LitElement, nothing } from "lit";
import { KonnectApi } from "./api";
import { icon } from "./art/icons";
import "./components/capsule-nav";
import { define } from "./components/define";
import "./components/dialog";
import "./components/header";
import type { ConfirmOptions, PanelContext } from "./context";
import { messagesLanguage, setLanguage, t } from "./i18n";
import { shared } from "./theme/shared";
import { tokens } from "./theme/tokens";
import type { DeviceDescriptor, DeviceLive, DevicesMessage, HomeAssistant, PanelInfo, Route } from "./types";
import "./views/about";
import "./views/controller";
import "./views/diagnostics";
import "./views/faucet";
import "./views/generic";
import "./views/home";
import "./views/settings";
import "./views/shower";
import "./views/smart-fan";
import "./views/usage";

const WIDE_PX = 980;

type DeviceScreen = "controls" | "favorites" | "experiences" | "settings" | "about" | "usage" | "diagnostics";

interface ParsedRoute {
  home?: "devices" | "usage" | "account";
  deviceId?: string;
  screen?: DeviceScreen;
}

export function parseRoute(path: string): ParsedRoute {
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "d" && parts[1]) {
    const screen = (parts[2] ?? "controls") as DeviceScreen;
    return { deviceId: parts[1], screen };
  }
  if (parts[0] === "usage" || parts[0] === "account") return { home: parts[0] };
  return { home: "devices" };
}

interface Toast {
  message: string;
  kind: "info" | "error";
  id: number;
}

/** The panel Home Assistant loads into its sidebar slot. */
export class KohlerKonnectPanel extends LitElement {
  static override properties = {
    hass: { attribute: false },
    narrow: { type: Boolean },
    route: { attribute: false },
    panel: { attribute: false },
    data: { state: true },
    live: { state: true },
    wide: { state: true },
    toastState: { state: true },
    dialog: { state: true },
    announcement: { state: true },
  };

  declare hass: HomeAssistant;
  declare narrow: boolean;
  declare route: Route;
  declare panel: PanelInfo;
  declare data: DevicesMessage | null;
  declare live: Record<string, DeviceLive>;
  declare wide: boolean;
  declare toastState: Toast | null;
  declare dialog: (ConfirmOptions & { resolve: (answer: boolean) => void }) | null;
  /** The screen just navigated to, for screen readers when focus stayed put. */
  declare announcement: string;

  /** Replaces Home Assistant's router; the design harness sets it. */
  navigateImpl?: (path: string) => void;

  private readonly api = new KonnectApi(() => this.hass);
  private unsubscribeDevices?: Promise<() => Promise<void>>;
  private liveSubscription?: { deviceId: string; unsubscribe: Promise<() => Promise<void>> };
  private observer?: ResizeObserver;
  private toastTimer?: number;

  constructor() {
    super();
    this.narrow = false;
    this.route = { prefix: "/kohler-konnect", path: "" };
    this.data = null;
    this.live = {};
    this.wide = false;
    this.toastState = null;
    this.dialog = null;
    this.announcement = "";
  }

  static override styles = [
    tokens,
    shared,
    css`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--kk-canvas);
        color: var(--kk-text);
      }

      .page {
        max-width: 1240px;
        margin: 0 auto;
        padding: 0 16px;
      }

      :host([wide]) .page {
        padding: 0 40px;
      }

      /* A full-height column, so the capsule tab bar rests on the bottom edge even when
         the screen's content is short. */
      .device-screen {
        display: flex;
        flex-direction: column;
        min-height: 100vh;
      }

      .device-screen > .page {
        flex: 1;
        width: 100%;
      }

      .device-head {
        position: sticky;
        top: 0;
        z-index: 3;
        background: linear-gradient(var(--kk-canvas) 78%, transparent);
      }

      .device-body {
        padding: 8px 0 24px;
      }

      .loading {
        display: grid;
        place-items: center;
        min-height: 60vh;
        color: var(--kk-text-2);
        font-size: 17px;
        text-align: center;
        gap: 16px;
      }

      .spinner {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        border: 3px solid var(--kk-control);
        border-top-color: var(--kk-accent);
        animation: kk-spin 900ms linear infinite;
      }

      .stale {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        padding: 10px 16px;
        background: var(--kk-accent-soft);
        color: var(--kk-text);
        font-size: 15px;
      }

      .stale button {
        color: var(--kk-accent-text);
        font-weight: 500;
      }

      .toast {
        position: fixed;
        left: 50%;
        bottom: 28px;
        z-index: 10;
        display: flex;
        align-items: center;
        gap: 12px;
        max-width: min(560px, calc(100vw - 32px));
        padding: 14px 20px;
        border-radius: 14px;
        background: var(--kk-control);
        color: var(--kk-text);
        font-size: 15px;
        line-height: 1.4;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
        transform: translateX(-50%);
        animation: kk-toast-in 260ms var(--kk-ease);
      }

      .toast.error {
        box-shadow: inset 4px 0 0 var(--kk-danger), 0 12px 40px rgba(0, 0, 0, 0.45);
      }

      .toast svg {
        width: 22px;
        height: 22px;
        flex: none;
      }

      .toast.error svg {
        color: var(--kk-danger);
      }

      @keyframes kk-spin {
        to {
          transform: rotate(1turn);
        }
      }

      @keyframes kk-toast-in {
        from {
          opacity: 0;
          transform: translate(-50%, 12px);
        }
      }
    `,
  ];

  override connectedCallback(): void {
    super.connectedCallback();
    this.observer = new ResizeObserver((entries) => {
      const wide = entries[0].contentRect.width >= WIDE_PX;
      if (wide !== this.wide) this.wide = wide;
    });
    this.observer.observe(this);
    if (this.hass && !this.unsubscribeDevices) this.subscribe();
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.observer?.disconnect();
    void this.unsubscribeDevices?.then((unsubscribe) => unsubscribe());
    this.unsubscribeDevices = undefined;
    this.dropLive();
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    if (changed.has("hass") && this.hass) {
      setLanguage(this.hass.locale?.language ?? this.hass.language);
      // Screen readers pronounce the text in its own language, not the page's.
      this.lang = messagesLanguage();
      this.setAttribute("theme", this.hass.themes?.darkMode === false ? "light" : "dark");
      if (!this.unsubscribeDevices && this.isConnected) this.subscribe();
    }
    this.toggleAttribute("wide", this.wide);
    if (changed.has("route") || changed.has("hass")) this.followDevice();
  }

  protected override updated(changed: Map<string, unknown>): void {
    const previous = changed.get("route") as Route | undefined;
    if (previous && previous.path !== this.route?.path) this.afterNavigation();
  }

  /**
   * A new screen. When it replaced the control that had focus, focus its heading, which
   * also says where you are; when focus stayed put (Back), say it instead. Tabs keep their
   * focus and say what they show themselves.
   */
  private afterNavigation() {
    requestAnimationFrame(() => {
      const focused = this.shadowRoot?.activeElement;
      if (!focused) {
        this.renderRoot
          .querySelector<HTMLElement & { focusHeading?: () => void }>("kkp-header, kkp-home")
          ?.focusHeading?.();
      } else if (focused.localName === "kkp-header") {
        this.announcement = (focused as HTMLElement & { heading?: string }).heading ?? "";
      }
    });
  }

  private subscribe() {
    this.unsubscribeDevices = this.api.subscribeDevices((message) => {
      this.data = message;
    });
    this.unsubscribeDevices.catch((error) => this.toast(String(error?.message ?? error), "error"));
  }

  /** Keep a live subscription to the device on screen, and only that one. */
  private followDevice() {
    const { deviceId } = parseRoute(this.route?.path ?? "");
    if (this.liveSubscription?.deviceId === deviceId) return;
    this.dropLive();
    if (!deviceId || !this.hass) return;
    this.liveSubscription = {
      deviceId,
      unsubscribe: this.api.subscribeDevice(deviceId, (live) => {
        this.live = { ...this.live, [deviceId]: live };
      }),
    };
    this.liveSubscription.unsubscribe.catch(() => undefined);
  }

  private dropLive() {
    void this.liveSubscription?.unsubscribe.then((unsubscribe) => unsubscribe()).catch(() => undefined);
    this.liveSubscription = undefined;
  }

  private navigate = (path: string) => {
    if (this.navigateImpl) {
      this.navigateImpl(path);
      return;
    }
    const prefix = this.route?.prefix ?? "/kohler-konnect";
    history.pushState(null, "", path === "/" ? prefix : `${prefix}${path}`);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    window.scrollTo({ top: 0 });
  };

  private renderToast(kind: Toast["kind"]) {
    const toast = this.toastState;
    if (toast?.kind !== kind) return nothing;
    return html`<div
      class="toast ${kind}"
      @mouseenter=${() => window.clearTimeout(this.toastTimer)}
      @mouseleave=${() => this.dismissLater(kind)}
    >
      ${icon(kind === "error" ? "alert" : "checkCircle")}<span>${toast.message}</span>
    </div>`;
  }

  private toast = (message: string, kind: "info" | "error" = "info") => {
    this.toastState = { message, kind, id: Date.now() };
    this.dismissLater(kind);
  };

  private dismissLater(kind: Toast["kind"]) {
    window.clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => (this.toastState = null), kind === "error" ? 10000 : 5000);
  }

  private confirm = (options: ConfirmOptions) =>
    new Promise<boolean>((resolve) => {
      this.dialog = { ...options, resolve };
    });

  private answer(confirmed: boolean) {
    this.dialog?.resolve(confirmed);
    this.dialog = null;
  }

  private get context(): PanelContext {
    return {
      api: this.api,
      admin: this.hass?.user?.is_admin ?? false,
      wide: this.wide,
      navigate: this.navigate,
      toast: this.toast,
      confirm: this.confirm,
    };
  }

  private get staleBundle(): boolean {
    const expected = this.panel?.config?.version;
    if (!expected) return false;
    const loaded = new URL(import.meta.url).searchParams.get("v");
    return !!loaded && loaded !== expected;
  }

  private screenTitle(device: DeviceDescriptor, screen: DeviceScreen): string {
    const titles: Partial<Record<DeviceScreen, string>> = {
      settings: t("settings.title"),
      about: t("about.title"),
      usage: t("usage.title"),
      diagnostics: t("diagnostics.title"),
    };
    return titles[screen] ?? device.name;
  }

  private back(device: DeviceDescriptor, screen: DeviceScreen) {
    if (screen === "about" || screen === "diagnostics") {
      this.navigate(`/d/${device.id}/settings`);
    } else if (screen === "settings" || screen === "usage") {
      this.navigate(`/d/${device.id}`);
    } else {
      this.navigate("/");
    }
  }

  private renderDeviceBody(device: DeviceDescriptor, screen: DeviceScreen) {
    const ctx = this.context;
    const live = this.live[device.id];
    switch (screen) {
      case "settings":
        return html`<kkp-settings .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx}></kkp-settings>`;
      case "about":
        return html`<kkp-about .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx}></kkp-about>`;
      case "usage":
        return html`<kkp-usage .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx}></kkp-usage>`;
      case "diagnostics":
        return html`<kkp-diagnostics .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx}></kkp-diagnostics>`;
      default:
        break;
    }
    const tab = screen as "controls" | "favorites" | "experiences";
    if (device.kind === "valve") {
      return html`<kkp-shower .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx} .tab=${tab} ?wide=${this.wide}></kkp-shower>`;
    }
    if (device.kind === "controller") {
      return html`<kkp-controller .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx} .tab=${tab} ?wide=${this.wide}></kkp-controller>`;
    }
    if (device.kind === "faucet") {
      return html`<kkp-faucet .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx} ?wide=${this.wide}></kkp-faucet>`;
    }
    if (device.kind === "smart_fan") {
      return html`<kkp-smart-fan .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx}></kkp-smart-fan>`;
    }
    return html`<kkp-generic .hass=${this.hass} .device=${device} .live=${live} .ctx=${ctx}></kkp-generic>`;
  }

  private renderDevice(deviceId: string, screen: DeviceScreen) {
    const device = this.data?.devices.find((d) => d.id === deviceId);
    if (!device) {
      return html`<div class="page">
        <kkp-header back .heading=${t("app.not_found")} @kkp-back=${() => this.navigate("/")}></kkp-header>
      </div>`;
    }
    const main = screen === "controls" || screen === "favorites" || screen === "experiences";
    const capsule = main && !this.wide && (device.kind === "valve" || device.kind === "controller");
    const admin = this.context.admin;
    // ⋯ opens Settings, or for anyone else Water Usage, if the device reports any.
    const more = main && (admin || !!device.capabilities.usage);
    return html`<div class="device-screen"><div class="page">
        <div class="device-head">
          <kkp-header
            .heading=${this.screenTitle(device, screen)}
            back
            ?more=${more}
            .moreLabel=${admin ? t("settings.title") : t("usage.title")}
            @kkp-back=${() => this.back(device, screen)}
            @kkp-more=${() => this.navigate(`/d/${device.id}/${admin ? "settings" : "usage"}`)}
          ></kkp-header>
        </div>
        <div class="device-body">${this.renderDeviceBody(device, screen)}</div>
      </div>
      ${capsule
        ? html`<kkp-capsule
            .items=${[
              { key: "controls", label: t("nav.controls"), icon: "controls" },
              { key: "favorites", label: t("nav.favorites"), icon: "favorite" },
              { key: "experiences", label: t("nav.experiences"), icon: "experiences" },
            ]}
            .active=${screen}
            @kkp-select=${(event: CustomEvent<string>) =>
              this.navigate(event.detail === "controls" ? `/d/${device.id}` : `/d/${device.id}/${event.detail}`)}
          ></kkp-capsule>`
        : nothing}</div>`;
  }

  override render() {
    if (!this.hass) return nothing;
    const route = parseRoute(this.route?.path ?? "");
    const body = !this.data
      ? html`<div class="loading"><div class="spinner"></div>${t("app.loading")}</div>`
      : route.deviceId
        ? this.renderDevice(route.deviceId, route.screen ?? "controls")
        : html`<div class="page">
            <kkp-home
              .hass=${this.hass}
              .data=${this.data}
              .ctx=${this.context}
              .tab=${route.home ?? "devices"}
              ?narrow=${this.narrow}
              ?wide=${this.wide}
            ></kkp-home>
          </div>`;
    return html`
      ${this.staleBundle
        ? html`<div class="stale">${t("app.stale_bundle")}<button @click=${() => location.reload()}>${t("app.refresh")}</button></div>`
        : nothing}
      <div
        @kkp-menu=${() =>
          this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }))}
      >
        ${body}
      </div>
      <!-- Live regions that stay put, so a screen reader announces each message put in
           them: errors at once, everything else when it can. -->
      <div role="status">${this.renderToast("info")}</div>
      <div role="alert">${this.renderToast("error")}</div>
      <div class="visually-hidden" aria-live="polite">${this.announcement}</div>
      <kkp-dialog
        .open=${!!this.dialog}
        .heading=${this.dialog?.title ?? ""}
        .body=${this.dialog?.body ?? ""}
        .confirmLabel=${this.dialog?.confirmLabel ?? t("dialog.confirm")}
        ?destructive=${this.dialog?.destructive ?? false}
        @kkp-answer=${(event: CustomEvent<boolean>) => this.answer(event.detail)}
      ></kkp-dialog>
    `;
  }
}

define("kohler-konnect-panel", KohlerKonnectPanel);
