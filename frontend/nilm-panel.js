// This part was majorily written by Github Copilot based on ideas, concepts, diagram workflows and sketches provided by me, 
// but has been modified and extended.

class NilmManagementPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    // Keep in sync with COMPONENT_VERSION in const.py / manifest.json so the
    // value shown in the console matches the cache-busted JS URL.
    this._panelBuild = '2.4.1';

    this._hass = null;
    this._tab = 'appliances';

    this._devices = {};
    this._appliancesMeta = [];

    this._modelStatus = {};
    this._trainStatus = {};
    this._totalPower = null;
    this._predictedPower = null;

    this._trainAppliance = '__all__';
    this._trainFrom = this._localIso(this._daysAgo(60));
    this._trainTo   = this._localIso(new Date());

    this._sendFrom = this._localIso(this._daysAgo(60));
    this._sendTo   = this._localIso(new Date());

    this._finetuneAppliance = '__all__';
    this._finetuneFrom = this._localIso(this._daysAgo(7));
    this._finetuneTo   = this._localIso(new Date());

    this._calibrateAppliance = '__all__';

    this._confirmingRemoveAll = false;

    this._newApplianceName = '';
    this._newApplianceThreshold = 10;
    this._newApplianceSensitivity = 'medium';
    this._thresholdDraft = {};
    this._sensitivityDraft = {};
    this._linkDraft = {};

    this._trainLog = '';
    this._notification = null;
    this._notifTimer = null;

    this._pollingStarted = false;
    this._pollingInterval = null;
  }

  _daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d;
  }

  _localIso(date) {
    const p = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`;
  }

  // Snapshot all form input values from the DOM into state before re-rendering,
  // so polling-triggered renders never clobber what the user has typed/selected.
  _snapshotInputs() {
    const root = this.shadowRoot;
    if (!root) return;
    const str = (sel, prop) => { const el = root.querySelector(sel); if (el && el.value !== '') this[prop] = el.value; };
    const num = (sel, prop) => { const el = root.querySelector(sel); if (el && el.value !== '') this[prop] = parseFloat(el.value) || this[prop]; };

    str('#inp-send-from',              '_sendFrom');
    str('#inp-send-to',                '_sendTo');
    str('#sel-train-app',              '_trainAppliance');
    str('#inp-train-from',             '_trainFrom');
    str('#inp-train-to',               '_trainTo');
    str('#sel-finetune-app',           '_finetuneAppliance');
    str('#inp-finetune-from',          '_finetuneFrom');
    str('#inp-finetune-to',            '_finetuneTo');
    str('#sel-calibrate-app',          '_calibrateAppliance');
    str('#inp-new-appliance-name',     '_newApplianceName');
    num('#inp-new-appliance-threshold',   '_newApplianceThreshold');

    root.querySelectorAll('input[data-threshold-id]').forEach((inp) => {
      if (inp.value !== '') this._thresholdDraft[inp.dataset.thresholdId] = parseFloat(inp.value) || 0;
    });

    root.querySelectorAll('select[data-link-sel]').forEach((sel) => {
      if (sel.value) this._linkDraft[sel.dataset.linkSel] = sel.value;
    });
  }

  _notify(msg, type = 'ok') {
    clearTimeout(this._notifTimer);
    this._notification = { msg, type };
    this._render();
    this._notifTimer = setTimeout(() => {
      this._notification = null;
      this._render();
    }, 4000);
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._pollingStarted) {
      console.info('[NILM panel] build:', this._panelBuild);
      this._pollingStarted = true;
      this._fetchData();
      this._pollingInterval = setInterval(() => this._fetchData(), 5000);
    }
  }

  disconnectedCallback() {
    clearInterval(this._pollingInterval);
    this._pollingStarted = false;
  }

  async _fetchData() {
    if (!this._hass) return;

    try {
      const [devices, modelStatus, trainStatus, appliancesResp] = await Promise.all([
        this._hass.callApi('GET', 'nilm/devices'),
        this._hass.callApi('GET', 'nilm/model_status'),
        this._hass.callApi('GET', 'nilm/train_status'),
        this._hass.callApi('GET', 'nilm/appliances'),
      ]);

      this._devices = devices?.appliances || {};
      this._totalPower = devices?.total_power ?? devices?.total_power_active ?? null;
      this._predictedPower = devices?.predicted_power ?? Object.values(this._devices).reduce((acc, item) => acc + (item?.power || 0), 0);

      this._modelStatus = modelStatus || {};
      this._trainStatus = trainStatus || {};

      const raw = appliancesResp?.appliances;
      this._appliancesMeta = Array.isArray(raw)
        ? raw
        : (Array.isArray(appliancesResp) ? appliancesResp : []);
    } catch (error) {
      console.error('NILM panel fetch error', error);
    }

    // Don't re-render while the user is actively editing a form field
    // replacing innerHTML destroys the focused element and resets its value.
    const active = this.shadowRoot?.activeElement;
    if (!active || (active.tagName !== 'INPUT' && active.tagName !== 'SELECT')) {
      this._render();
    }
  }

  _entityPower(entityId) {
    if (!this._hass || !entityId) return null;
    const state = this._hass.states?.[entityId];
    if (!state) return null;
    const value = parseFloat(state.state);
    return Number.isNaN(value) ? null : value;
  }

  _fmt(value) {
    if (value == null) return '---';
    return `${Math.round(value)} W`;
  }

  _displayName(id) {
    return String(id || '').replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  _timeAgo(epoch) {
    if (!epoch) return '---';
    const seconds = Math.floor((Date.now() / 1000) - epoch);
    if (seconds < 60)    return 'just now';
    if (seconds < 3600)  return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`;
    const days = Math.floor(seconds / 86400);
    if (days < 30) return `${days} day${days !== 1 ? 's' : ''} ago`;
    const months = Math.floor(days / 30);
    if (months < 12) return `${months} month${months !== 1 ? 's' : ''} ago`;
    return `${Math.floor(months / 12)} year${Math.floor(months / 12) !== 1 ? 's' : ''} ago`;
  }


  _css() {
    return `
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

      :host {
        display: block;
        min-height: 100vh;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
        font-size: 14px;
      }


      .tabs {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        background: var(--card-background-color);
        border-bottom: 1px solid var(--divider-color);
      }

      .tab {
        padding: 16px 24px;
        text-align: center;
        font-size: 14px;
        font-weight: 500;
        letter-spacing: 0.03em;
        cursor: pointer;
        color: var(--secondary-text-color);
        border-right: 1px solid var(--divider-color);
        transition: color 0.15s, background 0.15s;
      }

      .tab:last-child { border-right: none; }
      .tab:hover { color: var(--primary-text-color); background: var(--secondary-background-color); }
      .tab.active {
        color: var(--primary-color);
        background: var(--secondary-background-color);
        box-shadow: inset 0 -2px 0 var(--primary-color);
      }


      .meta {
        background: var(--card-background-color);
        border-bottom: 1px solid var(--divider-color);
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        padding: 12px 24px;
        font-size: 13px;
        text-align: center;
        color: var(--secondary-text-color);
        gap: 8px;
      }

      .meta b { color: var(--primary-text-color); font-weight: 600; }

      .content {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 14px;
        flex: 1;
      }

      .card-list { display: flex; flex-direction: column; gap: 10px; }

      .card {
        background: var(--card-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.12));
        padding: 14px 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        transition: box-shadow 0.2s;
      }

      .card:hover { box-shadow: 0 4px 14px rgba(0,0,0,0.18); }

      .card-left { display: flex; align-items: baseline; gap: 10px; }

      .app-name { font-weight: 600; font-size: 16px; color: var(--primary-text-color); }
      .app-sep  { color: var(--divider-color); font-size: 18px; }
      .app-power { color: var(--secondary-text-color); font-size: 14px; }

      .state-chip {
        width: 200px;
        border-radius: calc(var(--ha-card-border-radius, 12px) - 2px);
        text-align: center;
        padding: 10px 16px;
        font-size: 13px;
        font-weight: 600;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }

      .state-chip.on  { background: var(--success-color-tint, rgba(76,175,80,0.15)); color: var(--success-color, #4caf50); }
      .state-chip.off { background: var(--error-color-tint,   rgba(244,67,54,0.12)); color: var(--error-color,   #f44336); }

      .linked-box {
        width: 240px;
        border: 1px solid var(--divider-color);
        border-radius: calc(var(--ha-card-border-radius, 12px) - 2px);
        text-align: center;
        padding: 10px 14px;
        font-size: 12px;
        line-height: 1.7;
        color: var(--secondary-text-color);
        display: flex;
        flex-direction: column;
        justify-content: center;
      }

      .linked-box b { color: var(--primary-text-color); }

      .train-group {
        background: var(--card-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.12));
        padding: 16px;
        display: flex;
        gap: 10px;
        align-items: stretch;
      }

      .train-cell {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 10px 14px;
        display: flex;
        flex-direction: column;
        align-items: center;
        flex: 1;
        gap: 6px;
        text-align: center;
        background: var(--secondary-background-color);
      }

      .cell-label {
        font-size: 10px;
        letter-spacing: 0.07em;
        text-transform: uppercase;
        color: var(--secondary-text-color);
        font-weight: 600;
      }

      .train-cell select, .train-cell input {
        background: transparent;
        color: var(--primary-text-color);
        border: none;
        outline: none;
        text-align: center;
        font-size: 13px;
        width: 100%;
      }

      .train-cell select option { background: var(--card-background-color); color: var(--primary-text-color); }

      .btn {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
        font-weight: 500;
        padding: 10px 20px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        white-space: nowrap;
        transition: background 0.15s, opacity 0.15s;
        letter-spacing: 0.02em;
      }

      .btn:hover { background: var(--divider-color); }
      .btn:disabled { opacity: 0.4; cursor: default; }

      .btn.primary {
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        border-color: var(--primary-color);
      }
      .btn.primary:hover { filter: brightness(1.1); }

      .btn.green {
        background: var(--success-color-tint, rgba(76,175,80,0.12));
        color: var(--success-color, #4caf50);
        border-color: var(--success-color, #4caf50);
      }
      .btn.green:hover { filter: brightness(1.1); }

      .btn.red {
        background: var(--error-color-tint, rgba(244,67,54,0.1));
        color: var(--error-color, #f44336);
        border-color: var(--error-color, #f44336);
      }
      .btn.red:hover { filter: brightness(1.1); }

      .btn.sm { padding: 7px 12px; font-size: 12px; border-radius: 6px; }

      .progress-wrap {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        height: 28px;
        position: relative;
        overflow: hidden;
        background: var(--secondary-background-color);
      }

      .progress-fill {
        background: var(--primary-color);
        opacity: 0.75;
        height: 100%;
        transition: width 0.4s ease;
      }

      .progress-text {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: 600;
        color: var(--primary-text-color);
      }

      .log {
        background: var(--card-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.12));
        overflow: hidden;
        flex: 1;
      }

      .log-title {
        text-align: center;
        padding: 10px;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.07em;
        text-transform: uppercase;
        color: var(--secondary-text-color);
        border-bottom: 1px solid var(--divider-color);
        background: var(--secondary-background-color);
      }

      .log-body {
        padding: 14px;
        min-height: 140px;
        font-family: 'Roboto Mono', monospace;
        font-size: 12px;
        line-height: 1.6;
        color: var(--secondary-text-color);
        white-space: pre-wrap;
        word-break: break-all;
      }

      .section {
        background: var(--card-background-color);
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.12));
        overflow: hidden;
      }

      .section-title {
        text-align: center;
        padding: 10px;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.07em;
        text-transform: uppercase;
        color: var(--secondary-text-color);
        border-bottom: 1px solid var(--divider-color);
        background: var(--secondary-background-color);
      }

      .section-body { padding: 14px; display: flex; flex-direction: column; gap: 10px; }

      .add-row { display: flex; gap: 10px; align-items: stretch; }

      .add-cell {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 10px 14px;
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        background: var(--secondary-background-color);
      }

      .add-cell input, .add-cell select {
        background: transparent;
        color: var(--primary-text-color);
        border: none;
        outline: none;
        text-align: center;
        font-size: 13px;
        width: 100%;
      }

      .add-cell select option { background: var(--card-background-color); color: var(--primary-text-color); }


      .manage-row {
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        padding: 10px;
        display: flex;
        gap: 8px;
        align-items: center;
        background: var(--secondary-background-color);
      }

      .manage-cell {
        border: 1px solid var(--divider-color);
        border-radius: 6px;
        padding: 8px 10px;
        flex: 1;
        text-align: center;
        font-size: 12px;
        line-height: 1.6;
        color: var(--secondary-text-color);
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: 2px;
        background: var(--card-background-color);
      }

      .manage-cell b { color: var(--primary-text-color); font-weight: 600; }

      .manage-cell input, .manage-cell select {
        background: transparent;
        color: var(--primary-text-color);
        border: none;
        outline: none;
        text-align: center;
        font-size: 12px;
        width: 100%;
      }

      .manage-cell select option { background: var(--card-background-color); color: var(--primary-text-color); }

      .actions { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; }

      .empty {
        text-align: center;
        padding: 40px;
        color: var(--secondary-text-color);
        font-size: 14px;
      }

      .notif {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 999;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 18px;
        border-radius: var(--ha-card-border-radius, 12px);
        font-size: 13px;
        font-weight: 500;
        background: var(--card-background-color);
        box-shadow: var(--ha-card-box-shadow, 0 4px 16px rgba(0,0,0,0.2));
        border: 1px solid var(--divider-color);
        animation: slideIn 0.2s ease;
      }

      @keyframes slideIn {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: translateY(0); }
      }

      .notif.ok    { border-color: var(--success-color, #4caf50); color: var(--success-color, #4caf50); }
      .notif.error { border-color: var(--error-color,   #f44336); color: var(--error-color,   #f44336); }

      .notif-icon { font-size: 15px; }
    `;
  }

  _render() {
    if (!this._hass) return;
    this._snapshotInputs();

    const modelName = this._modelStatus?.model_name || '---';
    const appCount = this._appliancesMeta.length || Object.keys(this._devices).length;

    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <div>
        <div class="tabs">
            <div class="tab ${this._tab === 'appliances' ? 'active' : ''}" data-tab="appliances">Appliances</div>
            <div class="tab ${this._tab === 'train' ? 'active' : ''}" data-tab="train">Train</div>
            <div class="tab ${this._tab === 'parameters' ? 'active' : ''}" data-tab="parameters">Parameters</div>
          </div>

          <div class="meta">
            <span>Model &nbsp;<b>${modelName}</b></span>
            <span>Appliances &nbsp;<b>${appCount}</b></span>
            <span>Predicted Power &nbsp;<b>${this._fmt(this._predictedPower)}</b></span>
            <span>Real Power &nbsp;<b>${this._fmt(this._totalPower)}</b></span>
          </div>

          <div class="content">
            ${this._tab === 'appliances' ? this._renderAppliances() : ''}
            ${this._tab === 'train' ? this._renderTrain() : ''}
            ${this._tab === 'parameters' ? this._renderParameters() : ''}
        </div>
      </div>
      ${this._notification ? `
        <div class="notif ${this._notification.type}">
          <span class="notif-icon">${this._notification.type === 'ok' ? '✓' : '✗'}</span>
          <span>${this._notification.msg}</span>
        </div>` : ''}
    `;

    this._attachListeners();
  }

  _renderAppliances() {
    const names = this._appliancesMeta.length
      ? this._appliancesMeta.map((item) => item?.name).filter(Boolean)
      : Object.keys(this._devices);

    if (!names.length) {
      return '<div class="empty">No appliances detected yet.</div>';
    }

    return `
      <div class="card-list">
        ${names.map((id) => {
          const prediction = this._devices[id] || { power: 0, state: 0 };
          const linkedEntity = this._devices[id]?.linked_entity;
          const isOn = prediction.state === 1;

          const right = linkedEntity
            ? `<div class="linked-box"><b>${linkedEntity}</b><br>Real : ${this._fmt(this._entityPower(linkedEntity))}</div>`
            : `<div class="state-chip ${isOn ? 'on' : 'off'}">${isOn ? 'ON' : 'OFF'}</div>`;

          return `
            <div class="card">
              <div class="card-left">
                <span class="app-name">${this._displayName(id)}</span>
                <span class="app-sep">·</span>
                <span class="app-power">${this._fmt(prediction.power)}</span>
              </div>
              ${right}
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  _renderTrain() {
    const names = this._appliancesMeta.map((item) => item?.name).filter(Boolean);
    const appOptions = names.map((name) => `<option value="${name}">${this._displayName(name)}</option>`).join('');
    const progress = this._trainStatus?.progress ?? 0;
    const isTraining = this._trainStatus?.is_training ?? false;
    const isFinetuning = this._trainStatus?.is_finetuning ?? false;
    const isBusy = isTraining || isFinetuning;
    const samplesCount = this._trainStatus?.samples_count ?? 0;
    const defaultLog = JSON.stringify({
      training: this._trainStatus?.training_results || {},
      finetuning: this._trainStatus?.finetuning_results || {},
    }, null, 2);

    const removeBtn = this._confirmingRemoveAll
      ? `<button class="btn red" data-action="remove-all-confirm" ${isBusy ? 'disabled' : ''}>Confirm?</button>`
      : `<button class="btn red" data-action="remove-all-data" ${isBusy ? 'disabled' : ''}>Remove All Data</button>`;

    return `
      <div class="train-group">
        <div class="train-cell">
          <span class="cell-label">Data</span>
          <span style="font-size:13px;font-weight:600;">${samplesCount}</span>
          <span style="font-size:10px;color:var(--secondary-text-color);">samples on backend</span>
        </div>
        <div class="train-cell">
          <span class="cell-label">From</span>
          <input id="inp-send-from" type="datetime-local" value="${this._sendFrom}">
        </div>
        <div class="train-cell">
          <span class="cell-label">To</span>
          <input id="inp-send-to" type="datetime-local" value="${this._sendTo}">
        </div>
        <button class="btn green" data-action="send-data" ${isBusy ? 'disabled' : ''}>Send Data</button>
        ${removeBtn}
      </div>

      <div class="train-group">
        <div class="train-cell">
          <span class="cell-label">Appliance</span>
          <select id="sel-calibrate-app">
            <option value="__all__" ${this._calibrateAppliance === '__all__' ? 'selected' : ''}>All</option>
            ${appOptions}
          </select>
        </div>
        <button class="btn" data-action="calibrate" ${isBusy ? 'disabled' : ''}>Calibrate</button>
        <button class="btn red" data-action="calibrate-delete" ${isBusy ? 'disabled' : ''}>Delete Calibrate</button>
      </div>

      <div class="train-group">
        <div class="train-cell">
          <span class="cell-label">Appliance</span>
          <select id="sel-train-app">
            <option value="__all__" ${this._trainAppliance === '__all__' ? 'selected' : ''}>All Linked</option>
            ${appOptions}
          </select>
        </div>
        <div class="train-cell">
          <span class="cell-label">From</span>
          <input id="inp-train-from" type="datetime-local" value="${this._trainFrom}">
        </div>
        <div class="train-cell">
          <span class="cell-label">To</span>
          <input id="inp-train-to" type="datetime-local" value="${this._trainTo}">
        </div>
        <button class="btn primary" data-action="train" ${isBusy ? 'disabled' : ''}>${isTraining ? 'Training…' : 'Train'}</button>
      </div>

      <div class="train-group">
        <div class="train-cell">
          <span class="cell-label">Appliance</span>
          <select id="sel-finetune-app">
            <option value="__all__" ${this._finetuneAppliance === '__all__' ? 'selected' : ''}>All Linked</option>
            ${appOptions}
          </select>
        </div>
        <div class="train-cell">
          <span class="cell-label">From</span>
          <input id="inp-finetune-from" type="datetime-local" value="${this._finetuneFrom}">
        </div>
        <div class="train-cell">
          <span class="cell-label">To</span>
          <input id="inp-finetune-to" type="datetime-local" value="${this._finetuneTo}">
        </div>
        <button class="btn" data-action="finetune" ${isBusy ? 'disabled' : ''}>${isFinetuning ? 'Fine-tuning…' : 'Finetune'}</button>
      </div>

      <div class="progress-wrap">
        <div class="progress-fill" style="width:${progress}%"></div>
        <div class="progress-text">${progress} %</div>
      </div>

      <div class="log">
        <div class="log-title">Detailed Info</div>
        <div class="log-body">${this._trainLog || defaultLog || 'No active job.'}</div>
      </div>
    `;
  }

  _renderParameters() {
    const isNilmEntity = (id) => /^(sensor|binary_sensor)\.nilm_/i.test(id);
    const powerSensors = Object.keys(this._hass.states || {})
      .filter((id) => {
        if (isNilmEntity(id)) return false;
        const state = this._hass.states[id];
        return state?.attributes?.device_class === 'power' || state?.attributes?.unit_of_measurement === 'W';
      })
      .sort();

    const applianceNames = this._appliancesMeta.map((item) => item?.name).filter(Boolean);
    const isBusy = (this._trainStatus?.is_training ?? false) || (this._trainStatus?.is_finetuning ?? false);
    const dis = isBusy ? 'disabled' : '';

    return `
      <div class="section">
        <div class="section-title">Add Appliance to Model</div>
        <div class="section-body">
          <div class="add-row">
            <div class="add-cell">
              <span class="cell-label">Name</span>
              <input id="inp-new-appliance-name" type="text" value="${this._newApplianceName}" placeholder="e.g. Fridge" ${dis}>
            </div>
            <div class="add-cell">
              <span class="cell-label">Threshold (W)</span>
              <input id="inp-new-appliance-threshold" type="number" min="0" step="0.1" value="${this._newApplianceThreshold}" ${dis}>
            </div>
            <button class="btn green" data-action="add-appliance" ${dis}>Add</button>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Manage Appliances</div>
        <div class="section-body">
          ${applianceNames.length ? applianceNames.map((name) => {
            const meta = this._appliancesMeta.find((item) => item?.name === name) || {};
            const info = this._devices[name] || {};
            const linked = info.linked_entity;
            const predicted = this._devices[name]?.power ?? 0;
            const threshold = this._thresholdDraft[name] ?? meta.threshold ?? 0;
            const currentReal = linked ? this._fmt(this._entityPower(linked)) : '---';
            const finetuneCount = meta.finetune_count ?? 0;
            const samplesCount = meta.samples_count ?? 0;

            return `
              <div class="manage-row">
                <div class="manage-cell">
                  <b>${this._displayName(name)}</b>
                  <span>Predicted : ${this._fmt(predicted)}</span>
                </div>

                <div class="manage-cell">
                  <span>Last Trained ${this._timeAgo(meta.last_trained)}</span>
                  <span>Finetuned ${finetuneCount} time${finetuneCount !== 1 ? 's' : ''}</span>
                  <span>Last Finetuned ${this._timeAgo(meta.last_finetuned)}</span>
                  <span>Samples count on backend : ${samplesCount}</span>
                </div>

                <div class="manage-cell">
                  <b style="font-size:10px;letter-spacing:0.07em;text-transform:uppercase;">Threshold</b>
                  <span>Current : ${Math.round(meta.threshold ?? 0)} W</span>
                  <input data-threshold-id="${name}" type="number" min="0" step="0.1" value="${threshold}" placeholder="New threshold" ${dis}>
                </div>

                <div class="manage-cell">
                  ${linked
                    ? `<span>Sensor from HA</span><b>${linked}</b><span>Current Power : ${currentReal}</span>`
                    : `<b style="font-size:10px;letter-spacing:0.07em;text-transform:uppercase;">Link</b><select data-link-sel="${name}" ${dis}><option value="">Select Sensor</option>${powerSensors.map((e) => `<option value="${e}" ${this._linkDraft[name] === e ? 'selected' : ''}>${e}</option>`).join('')}</select>`
                  }
                </div>

                <div class="actions">
                  <button class="btn sm" data-action="set-threshold" data-id="${name}" ${dis}>Apply</button>
                  ${linked
                    ? `<button class="btn sm red" data-action="unlink" data-id="${name}" ${dis}>Unlink</button>`
                    : `<button class="btn sm green" data-action="link-one" data-id="${name}" ${dis}>Link</button>`}
                  ${linked && finetuneCount > 0
                    ? `<button class="btn sm red" data-action="delete-finetune" data-id="${name}" ${dis}>Delete Finetune</button>`
                    : ''}
                  <button class="btn sm red" data-action="remove-appliance" data-id="${name}" ${dis}>Delete</button>
                </div>
              </div>
            `;
          }).join('') : '<div class="empty">No appliances in model.</div>'}
        </div>
      </div>
    `;
  }

  // Visual feedback on a button: changes text/class, then the subsequent re-render restores it
  _btnBusy(btn, label = '…') {
    btn.disabled = true;
    btn.dataset.origLabel = btn.textContent;
    btn.textContent = label;
  }

  _attachListeners() {
    const root = this.shadowRoot;

    root.querySelectorAll('.tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        this._tab = tab.dataset.tab;
        this._render();
      });
    });

    // Model selection is managed via the integration's config entry, not the panel.
    const inpSendFrom = root.querySelector('#inp-send-from');
    const inpSendTo = root.querySelector('#inp-send-to');
    if (inpSendFrom) inpSendFrom.addEventListener('change', (e) => { this._sendFrom = e.target.value; });
    if (inpSendTo) inpSendTo.addEventListener('change', (e) => { this._sendTo = e.target.value; });

    const selTrainApp = root.querySelector('#sel-train-app');
    const inpTrainFrom = root.querySelector('#inp-train-from');
    const inpTrainTo = root.querySelector('#inp-train-to');
    const selFinetuneApp = root.querySelector('#sel-finetune-app');
    const inpFinetuneFrom = root.querySelector('#inp-finetune-from');
    const inpFinetuneTo = root.querySelector('#inp-finetune-to');

    if (selTrainApp) selTrainApp.addEventListener('change', (e) => { this._trainAppliance = e.target.value; });
    if (inpTrainFrom) inpTrainFrom.addEventListener('change', (e) => { this._trainFrom = e.target.value; });
    if (inpTrainTo) inpTrainTo.addEventListener('change', (e) => { this._trainTo = e.target.value; });
    if (selFinetuneApp) selFinetuneApp.addEventListener('change', (e) => { this._finetuneAppliance = e.target.value; });
    if (inpFinetuneFrom) inpFinetuneFrom.addEventListener('change', (e) => { this._finetuneFrom = e.target.value; });
    if (inpFinetuneTo) inpFinetuneTo.addEventListener('change', (e) => { this._finetuneTo = e.target.value; });

    const selCalibrateApp = root.querySelector('#sel-calibrate-app');
    if (selCalibrateApp) selCalibrateApp.addEventListener('change', (e) => { this._calibrateAppliance = e.target.value; });


    const sendDataBtn = root.querySelector('[data-action=send-data]');
    if (sendDataBtn) {
      sendDataBtn.addEventListener('click', async () => {
        const from = root.querySelector('#inp-send-from')?.value || this._sendFrom;
        const to = root.querySelector('#inp-send-to')?.value || this._sendTo;
        this._sendFrom = from;
        this._sendTo = to;

        this._btnBusy(sendDataBtn, 'Sending…');
        this._trainLog = `[${new Date().toLocaleTimeString()}] Pushing sensor data (${from} → ${to})…\n`;
        this._render();

        try {
          const pushResp = await this._hass.callApi('POST', 'nilm/push_data', { start: new Date(from).toISOString(), end: new Date(to).toISOString() });
          this._trainLog += `Data push: ${JSON.stringify(pushResp)}\n`;
          this._notify('Data sent successfully', 'ok');
        } catch (pushErr) {
          this._trainLog += `Error: data push failed (${pushErr})\n`;
          this._notify(`Data push failed: ${pushErr}`, 'error');
        }

        await this._fetchData();
      });
    }

    const removeAllBtn = root.querySelector('[data-action=remove-all-data]');
    if (removeAllBtn) {
      removeAllBtn.addEventListener('click', () => {
        this._confirmingRemoveAll = true;
        this._render();
        setTimeout(() => {
          if (this._confirmingRemoveAll) {
            this._confirmingRemoveAll = false;
            this._render();
          }
        }, 4000);
      });
    }

    const removeAllConfirmBtn = root.querySelector('[data-action=remove-all-confirm]');
    if (removeAllConfirmBtn) {
      removeAllConfirmBtn.addEventListener('click', async () => {
        this._confirmingRemoveAll = false;
        this._btnBusy(removeAllConfirmBtn, 'Removing…');
        try {
          await this._hass.callApi('DELETE', 'nilm/reset_data');
          this._trainLog = `[${new Date().toLocaleTimeString()}] All data removed.\n`;
          this._notify('All data removed', 'ok');
        } catch (error) {
          this._trainLog += `Error: ${error}\n`;
          this._notify(`Remove failed: ${error}`, 'error');
        }
        await this._fetchData();
      });
    }

    const trainBtn = root.querySelector('[data-action=train]');
    if (trainBtn) {
      trainBtn.addEventListener('click', async () => {
        const selected = root.querySelector('#sel-train-app')?.value || this._trainAppliance;
        const from = root.querySelector('#inp-train-from')?.value || this._trainFrom;
        const to = root.querySelector('#inp-train-to')?.value || this._trainTo;
        this._trainAppliance = selected;
        this._trainFrom = from;
        this._trainTo = to;

        const configs = this._buildConfigs(selected, from, to);
        this._btnBusy(trainBtn, 'Training…');
        this._trainLog = `[${new Date().toLocaleTimeString()}] Starting training…\n${JSON.stringify({ configs }, null, 2)}\n`;
        this._render();

        try {
          const response = await this._hass.callApi('POST', 'nilm/train', { configs });
          this._trainLog += `${JSON.stringify(response, null, 2)}\n`;
          this._notify('Training started', 'ok');
        } catch (error) {
          this._trainLog += `Error: ${error}\n`;
          this._notify(`Training failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    }

    const finetuneBtn = root.querySelector('[data-action=finetune]');
    if (finetuneBtn) {
      finetuneBtn.addEventListener('click', async () => {
        const selected = root.querySelector('#sel-finetune-app')?.value || this._finetuneAppliance;
        const from = root.querySelector('#inp-finetune-from')?.value || this._finetuneFrom;
        const to = root.querySelector('#inp-finetune-to')?.value || this._finetuneTo;
        this._finetuneAppliance = selected;
        this._finetuneFrom = from;
        this._finetuneTo = to;

        const configs = this._buildConfigs(selected, from, to);
        this._btnBusy(finetuneBtn, 'Fine-tuning…');
        this._trainLog = `[${new Date().toLocaleTimeString()}] Triggering fine-tune…\n${JSON.stringify({ configs }, null, 2)}\n`;
        this._render();

        try {
          const response = await this._hass.callApi('POST', 'nilm/finetune', { configs });
          this._trainLog += `${JSON.stringify(response, null, 2)}\n`;
          this._notify('Fine-tune started', 'ok');
        } catch (error) {
          this._trainLog += `Error: ${error}\n`;
          this._notify(`Fine-tune failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    }

    const calibrateBtn = root.querySelector('[data-action=calibrate]');
    if (calibrateBtn) {
      calibrateBtn.addEventListener('click', async () => {
        const selected = root.querySelector('#sel-calibrate-app')?.value || this._calibrateAppliance;
        this._calibrateAppliance = selected;

        const appliances = (selected && selected !== '__all__') ? [selected] : null;
        this._btnBusy(calibrateBtn, 'Calibrating…');
        this._trainLog = `[${new Date().toLocaleTimeString()}] Starting calibration…\n${JSON.stringify({ appliances }, null, 2)}\n`;
        this._render();

        try {
          const response = await this._hass.callApi('POST', 'nilm/calibrate', { appliances });
          this._trainLog += `${JSON.stringify(response, null, 2)}\n`;
          this._notify('Calibration done', 'ok');
        } catch (error) {
          this._trainLog += `Error: ${error}\n`;
          this._notify(`Calibration failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    }

    const calibrateDeleteBtn = root.querySelector('[data-action=calibrate-delete]');
    if (calibrateDeleteBtn) {
      calibrateDeleteBtn.addEventListener('click', async () => {
        const selected = root.querySelector('#sel-calibrate-app')?.value || this._calibrateAppliance;
        this._calibrateAppliance = selected;

        const appliance = (selected && selected !== '__all__') ? selected : null;
        this._btnBusy(calibrateDeleteBtn, 'Reverting…');
        this._trainLog = `[${new Date().toLocaleTimeString()}] Reverting calibration…\n${JSON.stringify({ appliance }, null, 2)}\n`;
        this._render();

        try {
          const response = await this._hass.callApi('POST', 'nilm/calibrate_delete', { appliance });
          this._trainLog += `${JSON.stringify(response, null, 2)}\n`;
          this._notify('Calibration reverted', 'ok');
        } catch (error) {
          this._trainLog += `Error: ${error}\n`;
          this._notify(`Calibration revert failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    }

    const inpNewApp = root.querySelector('#inp-new-appliance-name');
    const inpNewThreshold = root.querySelector('#inp-new-appliance-threshold');

    if (inpNewApp) inpNewApp.addEventListener('input', (e) => { this._newApplianceName = e.target.value; });
    if (inpNewThreshold) inpNewThreshold.addEventListener('change', (e) => { this._newApplianceThreshold = parseFloat(e.target.value) || this._newApplianceThreshold; });

    const addApplianceBtn = root.querySelector('[data-action=add-appliance]');
    if (addApplianceBtn) {
      addApplianceBtn.addEventListener('click', async () => {
        const appliance = (root.querySelector('#inp-new-appliance-name')?.value || '').trim();
        const threshold = parseFloat(root.querySelector('#inp-new-appliance-threshold')?.value || this._newApplianceThreshold);
        if (!appliance) return;
        // Mirror the backend's validate_safe_name regex so we surface a friendly
        // error before the round-trip and avoid logging a 400 on the server.
        if (!/^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$/.test(appliance)) {
          this._notify('Invalid name. Allowed: letters, digits, "-", "_" (1-64 chars, must start alphanumeric).', 'error');
          return;
        }

        this._btnBusy(addApplianceBtn);
        try {
          await this._hass.callApi('POST', 'nilm/appliance_add', { appliance, threshold });
          this._newApplianceName = '';
          this._notify(`"${appliance}" added`, 'ok');
        } catch (error) {
          this._notify(`Add failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    }

    root.querySelectorAll('input[data-threshold-id]').forEach((input) => {
      input.addEventListener('change', (e) => {
        this._thresholdDraft[e.target.dataset.thresholdId] = parseFloat(e.target.value) || 0;
      });
    });

    root.querySelectorAll('[data-action=set-threshold]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const appliance = btn.dataset.id;
        const input = root.querySelector(`input[data-threshold-id="${appliance}"]`);
        const threshold = parseFloat(input?.value ?? this._thresholdDraft[appliance] ?? 0);

        this._btnBusy(btn);
        try {
          await this._hass.callApi('POST', 'nilm/appliance_params', { appliance, threshold });
          this._notify(`${appliance}: ${threshold} W`, 'ok');
        } catch (error) {
          this._notify(`Update failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    });

    root.querySelectorAll('[data-action=link-one]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const appliance = btn.dataset.id;
        const entity = root.querySelector(`select[data-link-sel="${appliance}"]`)?.value;
        if (!appliance || !entity) {
          this._notify('Select a sensor first', 'error');
          return;
        }

        this._btnBusy(btn);
        try {
          await this._hass.callApi('POST', 'nilm/link_device', { device_id: appliance, linked_entity: entity });
          this._notify(`Linked to ${entity}`, 'ok');
        } catch (error) {
          this._notify(`Link failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    });

    root.querySelectorAll('[data-action=unlink]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        this._btnBusy(btn);
        try {
          await this._hass.callApi('POST', 'nilm/unlink_device', { device_id: btn.dataset.id });
          this._notify('Sensor unlinked', 'ok');
        } catch (error) {
          this._notify(`Unlink failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    });

    root.querySelectorAll('[data-action=delete-finetune]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const appliance = btn.dataset.id;
        if (!appliance) return;

        this._btnBusy(btn);
        try {
          await this._hass.callApi('POST', 'nilm/finetune_delete', { appliance });
          this._notify(`Finetune deleted for "${appliance}"`, 'ok');
        } catch (error) {
          this._notify(`Finetune delete failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    });

    root.querySelectorAll('[data-action=remove-appliance]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const appliance = btn.dataset.id;
        if (!appliance) return;

        this._btnBusy(btn);
        try {
          await this._hass.callApi('POST', 'nilm/appliance_remove', { appliance });
          try { await this._hass.callApi('POST', 'nilm/unlink_device', { device_id: appliance }); } catch (_) {}
          this._notify(`"${appliance}" deleted`, 'ok');
        } catch (error) {
          this._notify(`Delete failed: ${error}`, 'error');
        }

        await this._fetchData();
      });
    });

  }

  _buildConfigs(selectedAppliance, from, to) {
    const range = [new Date(from).toISOString(), new Date(to).toISOString()];

    if (selectedAppliance && selectedAppliance !== '__all__') {
      return { [selectedAppliance]: range };
    }

    const names = this._appliancesMeta.map((item) => item?.name).filter(Boolean);

    const linked = names.reduce((acc, name) => {
      if (this._devices[name]?.linked_entity) acc[name] = range;
      return acc;
    }, {});

    if (Object.keys(linked).length) return linked;

    return names.reduce((acc, name) => {
      acc[name] = range;
      return acc;
    }, {});
  }
}

customElements.define('nilm-management-panel', NilmManagementPanel);
