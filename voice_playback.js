(function (root, factory) {
  const controller = factory();
  if (typeof module === "object" && module.exports) module.exports = controller;
  root.PlurapackVoicePlayback = controller;
})(globalThis, function () {
  "use strict";

  // A short, audible confirmation for the explicit enable gesture. No looping,
  // silence, Web Audio context, or background keepalive is involved.
  function enableCue() {
    const rate = 16000, samples = 1920;
    const bytes = new ArrayBuffer(44 + samples * 2), wav = new DataView(bytes);
    const text = (offset, value) => [...value].forEach((c, i) => wav.setUint8(offset + i, c.charCodeAt(0)));
    text(0, "RIFF"); wav.setUint32(4, bytes.byteLength - 8, true);
    text(8, "WAVEfmt "); wav.setUint32(16, 16, true);
    wav.setUint16(20, 1, true); wav.setUint16(22, 1, true);
    wav.setUint32(24, rate, true); wav.setUint32(28, rate * 2, true);
    wav.setUint16(32, 2, true); wav.setUint16(34, 16, true);
    text(36, "data"); wav.setUint32(40, samples * 2, true);
    for (let i = 0; i < samples; i++) {
      const envelope = Math.sin(Math.PI * i / samples);
      wav.setInt16(44 + i * 2, 6000 * envelope * Math.sin(2 * Math.PI * 660 * i / rate), true);
    }
    return new Blob([bytes], { type: "audio/wav" });
  }

  class VoicePlayback {
    constructor({ player, button, status, document, window, navigator, fetch,
                  urls = URL, MediaMetadata = globalThis.MediaMetadata,
                  timers = globalThis, log = () => {}, onUnauthorized = () => {} }) {
      Object.assign(this, { player, button, status, document, window, navigator, fetch,
        urls, MediaMetadata, timers, log, onUnauthorized });
      this.enabled = false;
      this.paused = false;
      this.polling = false;
      this.fetching = false;
      this.disposed = false;
      this.queue = [];
      this.queued = new Set();
      this.consumed = new Set();
      this.current = null;
      this.active = null;
      this.interval = null;
      this.requests = new Set();
      this.handlers = [];
      this.listen(button, "click", () => this.enabled ? this.disable() : this.enable());
      this.listen(document, "visibilitychange", () => {
        if (document.visibilityState === "visible") this.poll();
      });
      this.listen(window, "focus", () => this.poll());
      this.listen(window, "pageshow", () => this.poll());
      this.listen(window, "pagehide", event => event.persisted ? this.disable() : this.destroy());
      this.update("Browser playback waits for your permission.");
    }

    listen(target, name, handler) {
      target.addEventListener(name, handler);
      this.handlers.push(() => target.removeEventListener(name, handler));
    }

    update(message) {
      this.button.textContent = this.enabled ? "Disable voice playback" : "Enable voice playback";
      this.button.setAttribute("aria-pressed", String(this.enabled));
      this.status.textContent = message;
    }

    enable() {
      if (this.disposed || this.enabled) return;
      this.enabled = true;
      this.paused = false;
      this.prepareMediaSession();
      this.update("Voice playback enabled.");
      this.log("info", "Voice playback enabled");
      // play() must run synchronously inside the click, before any fetch/await.
      // A retained, already-consumed clip can itself supply the gesture playback.
      this.start(this.current?.blob || enableCue(), !this.current?.blob);
      if (this.enabled) {
        this.interval = this.timers.setInterval(() => this.poll(), 2000);
        this.poll();
      }
    }

    disable(message = "Voice playback disabled.") {
      this.enabled = false;
      this.paused = false;
      this.timers.clearInterval(this.interval);
      this.interval = null;
      this.releasePlayer();
      // Keep at most one fetched Blob in memory. Its endpoint is consume-on-read;
      // neither disabling during a fetch nor an autoplay rejection can refetch it.
      this.mediaState("none");
      this.update(message);
      this.log("info", "Voice playback disabled");
    }

    prepareMediaSession() {
      if (this.mediaPrepared) return;
      this.mediaPrepared = true;
      if (!this.navigator.mediaSession) {
        this.log("info", "Media Session unavailable");
        return;
      }
      for (const [action, handler] of Object.entries({
        play: () => this.resume(), pause: () => this.pause(), stop: () => this.disable(),
      })) {
        try { this.navigator.mediaSession.setActionHandler(action, handler); }
        catch { /* Individual actions are not supported by every browser. */ }
      }
    }

    mediaState(state) {
      const session = this.navigator.mediaSession;
      if (!session) return;
      try {
        session.playbackState = state;
        session.metadata = state === "none" || !this.MediaMetadata ? null :
          new this.MediaMetadata({ title: "Plurapack speech", album: "Plurapack" });
      } catch { /* Notifications are best effort, independent of playback. */ }
    }

    pause() {
      if (!this.enabled) return;
      this.paused = true;
      if (this.active) {
        this.active.attempt++;
        this.timers.clearTimeout(this.active.startTimeout);
      }
      this.player.pause();
      this.mediaState("paused");
      this.update("Voice playback paused. Resume with media controls, or disable and enable again.");
    }

    resume() {
      // Media controls cannot silently re-enable permission after a rejection.
      if (!this.enabled || !this.paused) return;
      this.paused = false;
      if (this.active) this.play(this.active);
      else {
        this.update("Waiting for new speech…");
        this.pump();
      }
      this.poll();
    }

    releasePlayer() {
      const active = this.active;
      this.active = null;
      if (active) {
        this.timers.clearTimeout(active.startTimeout);
        for (const [name, handler] of Object.entries(active.listeners)) {
          this.player.removeEventListener(name, handler);
        }
      }
      this.player.pause();
      this.player.removeAttribute("src");
      this.player.load();
      // Detach and reset the media resource before releasing its URL.
      if (active?.url) this.urls.revokeObjectURL(active.url);
    }

    start(blob, cue = false) {
      if (!this.enabled || this.paused || this.active) return;
      const active = { cue, attempt: 0, listeners: {}, url: null };
      this.active = active;
      try {
        active.url = this.urls.createObjectURL(blob);
        this.player.src = active.url;
        this.player.load();
        active.listeners = {
          ended: () => this.finish(active, false),
          error: () => this.finish(active, true),
          abort: () => this.finish(active, true),
          pause: () => {
            if (this.active === active && !this.player.ended && !this.paused) this.pause();
          },
        };
        for (const [name, handler] of Object.entries(active.listeners)) {
          this.player.addEventListener(name, handler);
        }
        this.play(active);
      } catch { this.finish(active, true); }
    }

    play(active) {
      const attempt = ++active.attempt;
      const valid = () => this.active === active && active.attempt === attempt && this.enabled && !this.paused;
      this.timers.clearTimeout(active.startTimeout);
      active.startTimeout = this.timers.setTimeout(() => {
        if (valid()) this.finish(active, true);
      }, 15000);
      const failed = error => {
        if (!valid()) return;
        if (error?.name === "NotAllowedError") {
          this.log("warn", "Voice playback blocked");
          this.disable("Playback was blocked. Tap Enable voice playback to try again.");
        } else this.finish(active, true);
      };
      try {
        Promise.resolve(this.player.play()).then(() => {
          if (!valid()) return;
          this.timers.clearTimeout(active.startTimeout);
          if (!active.cue) {
            this.update("Playing speech…");
            this.mediaState("playing");
            this.log("info", "Voice playback started");
          }
        }, failed);
      } catch (error) { failed(error); }
    }

    finish(active, failed) {
      if (this.active !== active) return;
      this.releasePlayer();
      this.mediaState("none");
      if (!active.cue) this.current = null;
      this.log(failed ? "warn" : "info", failed ? "Voice clip unavailable" : "Voice playback ended");
      if (failed && active.cue) {
        this.disable("Voice playback temporarily unavailable.");
        return;
      }
      this.update(failed ? "Voice playback temporarily unavailable." : "Waiting for new speech…");
      this.pump();
    }

    remember(id) {
      this.consumed.add(id);
      if (this.consumed.size > 256) this.consumed.delete(this.consumed.values().next().value);
    }

    async request(path, audio = false) {
      const controller = new AbortController();
      this.requests.add(controller);
      const timeout = this.timers.setTimeout(() => controller.abort(), 15000);
      try {
        const response = await this.fetch(path, { credentials: "same-origin", cache: "no-store",
          headers: { Accept: audio ? "audio/wav" : "application/json" }, signal: controller.signal });
        if (response.status === 401) {
          this.destroy();
          this.onUnauthorized();
        }
        if (!response.ok) throw new Error("Voice request failed");
        return await (audio ? response.blob() : response.json());
      } finally {
        this.timers.clearTimeout(timeout);
        this.requests.delete(controller);
      }
    }

    async pump() {
      if (!this.enabled || this.paused || this.active || this.fetching || this.disposed) return;
      if (this.current?.blob) { this.start(this.current.blob); return; }
      const id = this.queue.shift();
      if (!id) return;
      this.queued.delete(id);
      const clip = { id, blob: null };
      this.current = clip;
      this.remember(id);
      this.fetching = true;
      try {
        const blob = await this.request(`/api/voice/audio/${encodeURIComponent(id)}`, true);
        if (!this.disposed) clip.blob = blob;
      } catch {
        if (this.current === clip) this.current = null;
        // A failed response may already have consumed the event. Never blindly
        // retry its endpoint, and never log response bodies or private IDs.
        if (this.enabled) this.update("Voice playback temporarily unavailable.");
        this.log("warn", "Voice clip unavailable");
      } finally {
        this.fetching = false;
      }
      this.pump();
    }

    async poll() {
      if (!this.enabled || this.polling || this.disposed) return;
      this.polling = true;
      try {
        const events = await this.request("/api/voice/events");
        if (!this.enabled || this.disposed) return;
        for (const event of events) {
          const id = event.id;
          if (typeof id !== "string" || !id || this.queued.has(id) ||
              this.current?.id === id || this.consumed.has(id)) continue;
          if (this.queue.length >= 100) break;
          this.queue.push(id);
          this.queued.add(id);
          this.log("info", "Voice event queued");
        }
        if (!this.active && !this.current && !this.queue.length && !this.paused) {
          this.update("Waiting for new speech…");
        }
        this.pump(); // Polling is independent of a clip's potentially long playback.
      } catch {
        if (this.enabled && !this.active) this.update("Voice playback temporarily unavailable.");
        this.log("warn", "Voice event polling unavailable");
      } finally {
        this.polling = false;
      }
    }

    destroy() {
      if (this.disposed) return;
      this.disposed = true;
      this.disable();
      for (const controller of this.requests) controller.abort();
      this.current = null;
      this.queue.length = 0;
      this.queued.clear();
      this.consumed.clear();
      this.handlers.forEach(remove => remove());
      if (this.mediaPrepared && this.navigator.mediaSession) {
        for (const action of ["play", "pause", "stop"]) {
          try { this.navigator.mediaSession.setActionHandler(action, null); } catch { /* Optional. */ }
        }
      }
    }
  }
  return VoicePlayback;
});
