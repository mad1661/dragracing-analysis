/**
 * Timing Talk JavaScript SDK
 * Drop this into any website to stream live drag racing timing data.
 *
 * Usage:
 *   const tt = new TimingTalk("your_api_key");
 *   const tracks = await tt.getTracks();
 *   tt.stream("device_id", (data) => console.log(data));
 */

(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.TimingTalk = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {

  var DEFAULT_BASE = "https://nhra-timing-api.web.app/api";

  function TimingTalk(apiKey, opts) {
    if (!apiKey) throw new Error("TimingTalk: API key is required");
    opts = opts || {};
    this._apiKey = apiKey;
    this._base = opts.baseUrl || DEFAULT_BASE;
    this._eventSource = null;
  }

  // ── Track Directory (public, no key needed) ───────────────────────────────

  TimingTalk.prototype.getTracks = function (opts) {
    opts = opts || {};
    var params = [];
    if (opts.q) params.push("q=" + encodeURIComponent(opts.q));
    if (opts.online) params.push("online=true");
    var qs = params.length ? "?" + params.join("&") : "";
    return this._fetch("/tracks" + qs, false).then(function (r) { return r.tracks; });
  };

  TimingTalk.prototype.getLiveTracks = function () {
    return this._fetch("/tracks/live?_=" + Date.now(), false).then(function (r) { return r.tracks; });
  };

  TimingTalk.prototype.getTrack = function (trackId) {
    return this._fetch("/tracks/" + trackId, false);
  };

  // ── Live Streaming (API key required) ─────────────────────────────────────

  /**
   * Stream live data from a device using Server-Sent Events.
   * @param {string} deviceId
   * @param {Function} onData - Called with each data event
   * @param {Function|Object} [onErrorOrOpts] - Error callback or options { source: "serial"|"tcp", onError: fn }
   */
  TimingTalk.prototype.stream = function (deviceId, onData, onErrorOrOpts) {
    var self = this;
    this.stopStream();

    var onError, source;
    if (typeof onErrorOrOpts === "object" && onErrorOrOpts !== null) {
      onError = onErrorOrOpts.onError;
      source = onErrorOrOpts.source;
    } else {
      onError = onErrorOrOpts;
    }

    var url = this._base + "/stream/" + deviceId + "?key=" + encodeURIComponent(this._apiKey);
    if (source) url += "&source=" + encodeURIComponent(source);
    this._eventSource = new EventSource(url);

    this._eventSource.onmessage = function (event) {
      try {
        onData(JSON.parse(event.data));
      } catch (e) {
        onData({ raw: event.data });
      }
    };

    this._eventSource.onerror = function (event) {
      if (onError) onError(event);
    };

    return function () { self.stopStream(); };
  };

  TimingTalk.prototype.stopStream = function () {
    if (this._eventSource) {
      this._eventSource.close();
      this._eventSource = null;
    }
  };

  // ── Polling & History (API key required) ──────────────────────────────────

  /**
   * Get the latest data snapshot.
   * @param {string} deviceId
   * @param {Object} [opts] - { source: "serial"|"tcp" } to filter by channel
   */
  TimingTalk.prototype.getLatest = function (deviceId, opts) {
    opts = opts || {};
    var qs = opts.source ? "?source=" + encodeURIComponent(opts.source) : "";
    return this._fetch("/latest/" + deviceId + qs, true);
  };

  TimingTalk.prototype.getHistory = function (deviceId, opts) {
    opts = opts || {};
    var params = [];
    if (opts.limit) params.push("limit=" + opts.limit);
    if (opts.after) params.push("after=" + encodeURIComponent(opts.after));
    var qs = params.length ? "?" + params.join("&") : "";
    return this._fetch("/history/" + deviceId + qs, true);
  };

  TimingTalk.prototype.poll = function (deviceId, onData, intervalMs) {
    var self = this;
    var interval = intervalMs || 2000;
    var running = true;

    (function loop() {
      if (!running) return;
      self.getLatest(deviceId)
        .then(function (data) { onData(data); })
        .catch(function () {})
        .finally(function () {
          if (running) setTimeout(loop, interval);
        });
    })();

    return function () { running = false; };
  };

  // ── Internal ──────────────────────────────────────────────────────────────

  TimingTalk.prototype._fetch = function (path, withAuth) {
    var headers = {};
    if (withAuth) {
      headers["X-API-Key"] = this._apiKey;
    }
    return fetch(this._base + path, { headers: headers, cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("API error: " + res.status);
        return res.json();
      });
  };

  return TimingTalk;
});
