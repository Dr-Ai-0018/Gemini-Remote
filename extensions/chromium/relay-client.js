// The one WebSocket from the extension to the local Relay (ws://127.0.0.1:<port>).
// Protocol: see src/gemini_remote/relay/browser.py. Everything browser-specific
// (WebSocket, storage, timers) is injected so the logic runs in tests too.
(function (root) {
  'use strict';

  const DEFAULT_PORT = 18776;
  const PING_MS = 20000; // also keeps the MV3 service worker alive (Chrome 116+)
  const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000];
  const POLICY_VIOLATION = 1008;
  const KEYS = { token: 'relayToken', port: 'relayPort' };

  // relay states shown in the UI:
  //   unpaired      no token yet: enter the code from the Relay terminal
  //   connecting    dialing / waiting for welcome
  //   connected     authenticated session
  //   offline       Relay unreachable or dropped us; retrying with backoff
  //   unauthorized  the Relay no longer accepts our token (unpaired there): pair again
  class RelayClient {
    constructor({ WebSocket, storage, version, timers = globalThis, onChange = () => {}, onCommand = () => {} }) {
      this.WebSocket = WebSocket;
      this.storage = storage;
      this.version = version;
      this.timers = timers;
      this.onChange = onChange;
      this.onCommand = onCommand;
      this.token = null;
      this.port = DEFAULT_PORT;
      this.state = { relay: 'unpaired', relayVersion: null, error: null, retryAt: null };
      this.browserReady = false;
      this.ws = null;
      this.attempt = 0;
      this.retryTimer = null;
      this.pingTimer = null;
      this.pending = null; // { resolve } of an ongoing pair()
    }

    snapshot() {
      return { ...this.state, port: this.port, paired: Boolean(this.token), extensionVersion: this.version };
    }

    async start() {
      const saved = await this.storage.get([KEYS.token, KEYS.port]);
      this.token = typeof saved[KEYS.token] === 'string' ? saved[KEYS.token] : null;
      if (validPort(saved[KEYS.port])) this.port = saved[KEYS.port];
      if (this.token) this.connect();
      else this._set({ relay: 'unpaired' });
    }

    // Dial with the stored token unless already connected or connecting.
    connect() {
      if (!this.token || this.ws || this.state.relay === 'unauthorized') return;
      this._open({ type: 'hello', token: this.token });
    }

    // Resolves { ok: true } or { ok: false, error } (bad_code, no_code, relay_unreachable, ...).
    pair(code) {
      const digits = String(code || '').replace(/\s+/g, '');
      if (!/^\d{6}$/.test(digits)) return Promise.resolve({ ok: false, error: 'code_format' });
      if (this.pending) return Promise.resolve({ ok: false, error: 'busy' });
      const before = this.state.relay;
      this._drop();
      return new Promise((resolve) => {
        this.pending = { resolve, before };
        this._open({ type: 'pair', code: digits });
      });
    }

    async unpair() {
      this._drop();
      this.token = null;
      await this.storage.remove([KEYS.token]);
      this._set({ relay: 'unpaired', relayVersion: null, error: null, retryAt: null });
    }

    async setPort(port) {
      const value = Number(port);
      if (!validPort(value)) return { ok: false, error: 'port_invalid' };
      await this.storage.set({ [KEYS.port]: value });
      this.port = value;
      this._drop();
      if (this.token) {
        this.state.relay = 'connecting'; // leave unauthorized: a different Relay may know us
        this.connect();
      } else {
        this._set({});
      }
      return { ok: true };
    }

    setBrowserReady(ready) {
      if (this.browserReady === ready) return;
      this.browserReady = ready;
      if (this.state.relay === 'connected') this._send({ type: 'status', browser_ready: ready });
    }

    send(message) {
      return this.state.relay === 'connected' && this._send(message);
    }

    // --- internals -----------------------------------------------------------

    _set(patch) {
      Object.assign(this.state, patch);
      this.onChange(this.snapshot());
    }

    _send(message) {
      try {
        this.ws.send(JSON.stringify(message));
        return true;
      } catch {
        return false;
      }
    }

    _open(first) {
      this.timers.clearTimeout(this.retryTimer);
      this.retryTimer = null;
      let ws;
      try {
        ws = new this.WebSocket(`ws://127.0.0.1:${this.port}`);
      } catch {
        this._closed(null, 1006, '');
        return;
      }
      this.ws = ws;
      this._set({ relay: 'connecting', error: null, retryAt: null });
      ws.onopen = () => {
        if (this.ws === ws) this._send({ ...first, extension_version: this.version });
      };
      ws.onmessage = (event) => {
        if (this.ws === ws) this._message(event.data);
      };
      ws.onclose = (event) => this._closed(ws, event.code, event.reason || '');
    }

    _message(raw) {
      let msg;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      switch (msg && msg.type) {
        case 'paired':
          this.token = msg.token;
          this.storage.set({ [KEYS.token]: msg.token });
          break;
        case 'welcome':
          this.attempt = 0;
          this._set({ relay: 'connected', relayVersion: msg.relay_version || null, error: null, retryAt: null });
          this._send({ type: 'status', browser_ready: this.browserReady });
          this.timers.clearInterval(this.pingTimer);
          this.pingTimer = this.timers.setInterval(() => this._send({ type: 'ping' }), PING_MS);
          this._settle({ ok: true });
          break;
        case 'command':
          this.onCommand(msg);
          break;
        default: // pong, ack: nothing to do yet
          break;
      }
    }

    _closed(ws, code, reason) {
      if (ws && this.ws !== ws) return; // an old socket we already dropped
      this.ws = null;
      this.timers.clearInterval(this.pingTimer);
      this.pingTimer = null;
      const pairing = this.pending;
      const error = code === POLICY_VIOLATION ? reason || 'refused' : 'relay_unreachable';
      this._settle({ ok: false, error });
      if (pairing) {
        // A failed pairing leaves things as they were before it.
        if (!this.token) this._set({ relay: 'unpaired', error });
        else if (pairing.before === 'unauthorized') this._set({ relay: 'unauthorized', error });
        else {
          this.attempt = 0;
          this._open({ type: 'hello', token: this.token });
        }
        return;
      }
      if (code === POLICY_VIOLATION) {
        // Our token was refused: retrying will not help until the user pairs again.
        this._set({ relay: 'unauthorized', error, retryAt: null });
        return;
      }
      if (!this.token) {
        this._set({ relay: 'unpaired', error });
        return;
      }
      const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
      this.attempt += 1;
      this._set({ relay: 'offline', error, retryAt: Date.now() + delay });
      this.retryTimer = this.timers.setTimeout(() => {
        this.retryTimer = null;
        this.connect();
      }, delay);
    }

    _settle(result) {
      if (!this.pending) return;
      const { resolve } = this.pending;
      this.pending = null;
      resolve(result);
    }

    _drop() {
      this.timers.clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.timers.clearInterval(this.pingTimer);
      this.pingTimer = null;
      const ws = this.ws;
      this.ws = null;
      if (ws) {
        try {
          ws.close(1000, 'bye');
        } catch {
          // already closing
        }
      }
    }
  }

  function validPort(value) {
    return Number.isInteger(value) && value >= 1 && value <= 65535;
  }

  root.GeminiRelayClient = Object.freeze({ RelayClient, DEFAULT_PORT, PING_MS, BACKOFF_MS, KEYS });
})(globalThis);
