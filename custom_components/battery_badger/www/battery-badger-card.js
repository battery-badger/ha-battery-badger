// Battery Badger Lovelace card.
// No build step — loaded directly by Home Assistant as an ES module.
import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@3.3.3/index.js?module";

const COLORS = {
  CHARGE: "#1e6deb",
  HOLD: "#7a869f",
  DISCHARGE: "#e16914",
  EXPORT: "#662ced",
};

const LABELS = {
  CHARGE: "Charge",
  HOLD: "Hold",
  DISCHARGE: "Discharge",
  EXPORT: "Export",
};

function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

class BatteryBadgerCard extends LitElement {
  static get properties() {
    return { hass: {}, config: {} };
  }

  static get styles() {
    return css`
      ha-card {
        padding: 16px;
      }
      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      .header .name {
        font-weight: 700;
        font-size: 16px;
      }
      .header a {
        font-size: 12px;
        color: var(--primary-color);
        text-decoration: none;
      }
      .current {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 12px;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        padding: 6px 14px;
        border-radius: 999px;
        font-weight: 700;
        font-size: 14px;
        color: white;
      }
      .applied {
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .bar {
        display: flex;
        height: 12px;
        border-radius: 6px;
        overflow: hidden;
        margin: 8px 0 4px;
        background: var(--divider-color);
      }
      .seg {
        flex-grow: 1;
        position: relative;
      }
      .seg[data-current="1"] {
        outline: 2px solid var(--primary-text-color);
        outline-offset: 0;
        z-index: 1;
      }
      .ticks {
        display: flex;
        justify-content: space-between;
        font-size: 10px;
        color: var(--secondary-text-color);
        margin-bottom: 8px;
      }
      .legend {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .legend .dot {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 4px;
        margin-right: 4px;
        vertical-align: middle;
      }
      .error {
        color: var(--error-color, #b00020);
        font-size: 12px;
        margin-top: 8px;
      }
    `;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("battery-badger-card: `entity` is required");
    }
    this.config = config;
  }

  getCardSize() {
    return 3;
  }

  render() {
    if (!this.hass || !this.config) return html``;
    const state = this.hass.states[this.config.entity];
    if (!state) {
      return html`<ha-card
        ><div class="error">Entity ${this.config.entity} not found.</div></ha-card
      >`;
    }

    const segments = state.attributes.segments || [];
    const applied = state.attributes.applied_mode;

    const now = new Date();
    const current = segments.find((s) => {
      const start = new Date(s.start);
      const finish = new Date(s.finish);
      return start <= now && now < finish;
    });
    const currentColor = current ? COLORS[current.action] || "#999" : "#999";
    const currentLabel = current
      ? LABELS[current.action] || current.action
      : "Unknown";

    const withDurations = segments.map((s) => ({
      ...s,
      ms: new Date(s.finish) - new Date(s.start),
      isCurrent: s === current,
    }));

    const first = segments[0];
    const last = segments[segments.length - 1];
    const mid = segments[Math.floor(segments.length / 2)];

    return html`
      <ha-card>
        <div class="header">
          <span class="name">${this.config.title || "Battery Badger"}</span>
          <a href="/config/integrations/integration/battery_badger"
            >Edit settings</a
          >
        </div>
        <div class="current">
          <span class="badge" style="background:${currentColor}"
            >${currentLabel}</span
          >
          ${applied
            ? html`<span class="applied"
                >inverter set to <strong>${applied}</strong></span
              >`
            : html`<span class="applied">waiting for first mode apply…</span>`}
        </div>
        ${segments.length === 0
          ? html`<div class="error">
              No schedule yet — waiting for the next reading.
            </div>`
          : html`
              <div class="bar">
                ${withDurations.map(
                  (s) => html`
                    <div
                      class="seg"
                      data-current=${s.isCurrent ? "1" : "0"}
                      style="flex:${s.ms};background:${COLORS[s.action] ||
                      "#999"}"
                      title="${fmtTime(s.start)} – ${fmtTime(s.finish)} · ${s.action}"
                    ></div>
                  `
                )}
              </div>
              <div class="ticks">
                <span>${fmtTime(first.start)}</span>
                <span>${fmtTime(mid.start)}</span>
                <span>${fmtTime(last.finish)}</span>
              </div>
            `}
        <div class="legend">
          ${["CHARGE", "HOLD", "DISCHARGE", "EXPORT"].map(
            (k) => html`
              <span>
                <span class="dot" style="background:${COLORS[k]}"></span
                >${LABELS[k]}
              </span>
            `
          )}
        </div>
      </ha-card>
    `;
  }
}

customElements.define("battery-badger-card", BatteryBadgerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "battery-badger-card",
  name: "Battery Badger",
  description: "Charge/Hold/Discharge/Export schedule bar with current mode",
  preview: false,
});
