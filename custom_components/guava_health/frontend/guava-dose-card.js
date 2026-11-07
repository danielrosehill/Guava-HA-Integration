// Dose card for the Guava Health integration. Unofficial. No dependencies.
// The tick only shows once Home Assistant reports the call finished, which is after Guava
// accepted the dose. A refused or failed dose shows the reason instead.
const DONE_MS = 2000;
const ERROR_MS = 4000;
const MIN_FADE_MS = 350;
const MAX_QUANTITY = 99;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class GuavaDoseCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = config;
    this._quantity = 1;
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._label();
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: 6, rows: 3, min_columns: 4, min_rows: 3 };
  }

  static getStubConfig() {
    return { entity: "" };
  }

  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { position: relative; height: 100%; min-height: 168px; overflow: hidden; }
        .face, .result { position: absolute; inset: 0; display: flex; flex-direction: column;
          align-items: center; justify-content: center; gap: 8px; padding: 10px 12px;
          box-sizing: border-box; text-align: center; transition: opacity 250ms ease; }
        .title { display: flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; }
        .title ha-icon { --mdc-icon-size: 20px; flex: none; color: var(--state-icon-color, var(--primary-color)); }
        .name { color: var(--primary-text-color); font-size: 14px; font-weight: 500; line-height: 1.25;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .stepper { display: flex; align-items: center; justify-content: center; gap: 12px; }
        .quantity { min-width: 32px; color: var(--primary-text-color); font-size: 20px; font-weight: 500; }
        button { font: inherit; cursor: pointer; border: none; -webkit-tap-highlight-color: transparent; }
        button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
        .step { width: 36px; height: 36px; border-radius: 50%; font-size: 20px; line-height: 1;
          color: var(--primary-color); background: var(--secondary-background-color); }
        .step:disabled { opacity: 0.35; cursor: default; }
        .log { width: 100%; height: 38px; border-radius: 10px; font-weight: 500;
          color: var(--text-primary-color, #fff); background: var(--primary-color); }
        .result { opacity: 0; pointer-events: none; font-size: 14px; font-weight: 500; line-height: 1.25; }
        .result ha-icon { --mdc-icon-size: 40px; }
        ha-card[data-state="busy"] .face { opacity: 0.25; pointer-events: none; }
        ha-card[data-state="done"] .face, ha-card[data-state="error"] .face { opacity: 0; pointer-events: none; }
        ha-card[data-state="done"] .result { opacity: 1; color: var(--success-color, #43a047); }
        ha-card[data-state="error"] .result { opacity: 1; color: var(--error-color, #db4437); }
      </style>
      <ha-card data-state="idle">
        <div class="face">
          <div class="title"><ha-icon></ha-icon><div class="name"></div></div>
          <div class="stepper">
            <button class="step minus" aria-label="Decrease quantity">&minus;</button>
            <div class="quantity" aria-live="polite"></div>
            <button class="step plus" aria-label="Increase quantity">+</button>
          </div>
          <button class="log">Log</button>
        </div>
        <div class="result" aria-live="polite"><ha-icon></ha-icon><div class="message"></div></div>
      </ha-card>`;
    const root = this.shadowRoot;
    this._card = root.querySelector("ha-card");
    root.querySelector(".title ha-icon").icon = this._config.icon || "mdi:pill";
    root.querySelector(".minus").addEventListener("click", () => this._step(-1));
    root.querySelector(".plus").addEventListener("click", () => this._step(1));
    root.querySelector(".log").addEventListener("click", () => this._log());
    this._showQuantity();
    this._label();
  }

  _label() {
    if (!this._card) return;
    const state = this._hass?.states[this._config.entity];
    this.shadowRoot.querySelector(".name").textContent =
      this._config.name || state?.attributes.friendly_name || this._config.entity;
  }

  _step(change) {
    this._quantity = Math.min(MAX_QUANTITY, Math.max(1, this._quantity + change));
    this._showQuantity();
  }

  _showQuantity() {
    const root = this.shadowRoot;
    root.querySelector(".quantity").textContent = this._quantity;
    root.querySelector(".minus").disabled = this._quantity <= 1;
    root.querySelector(".plus").disabled = this._quantity >= MAX_QUANTITY;
  }

  async _log() {
    if (this._card.dataset.state !== "idle" || !this._hass) return;
    this._card.dataset.state = "busy";
    let shown = ["done", "mdi:check-circle", "Dose logged successfully", DONE_MS];
    try {
      await Promise.all([
        // notifyOnError=false: the card shows the error itself.
        this._hass.callService(
          "guava_health", "log_dose", { quantity: this._quantity },
          { entity_id: this._config.entity }, false,
        ),
        sleep(MIN_FADE_MS),
      ]);
      this._quantity = 1;
      this._showQuantity();
    } catch (err) {
      shown = ["error", "mdi:alert-circle", err?.message || "Dose not logged", ERROR_MS];
    }
    const [state, icon, message, duration] = shown;
    this.shadowRoot.querySelector(".result ha-icon").icon = icon;
    this.shadowRoot.querySelector(".message").textContent = message;
    this._card.dataset.state = state;
    this.dispatchEvent(new CustomEvent("haptic", {
      detail: state === "done" ? "success" : "failure", bubbles: true, composed: true,
    }));
    await sleep(duration);
    this._card.dataset.state = "idle";
  }
}

customElements.define("guava-dose-card", GuavaDoseCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "guava-dose-card",
  name: "Guava dose card",
  description: "Choose a quantity, log a dose and see it confirmed on the card.",
});
