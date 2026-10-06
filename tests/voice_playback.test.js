const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const VoicePlayback = require('../voice_playback.js');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const blocked = () => Object.assign(new Error('PRIVATE error detail'), { name: 'NotAllowedError' });
class Element extends EventTarget {
  constructor() { super(); this.textContent = ''; this.attributes = {}; }
  setAttribute(name, value) { this.attributes[name] = value; }
  emit(name) { this.dispatchEvent(new Event(name)); }
}
class Player extends Element {
  constructor() {
    super(); this.src = ''; this.paused = true; this.ended = false;
    this.plays = []; this.loads = []; this.pauses = 0; this.results = [];
  }
  play() {
    this.plays.push(this.src); this.paused = false; this.ended = false;
    const result = this.results.shift();
    return result ? result() : Promise.resolve();
  }
  pause() {
    this.pauses++;
    if (!this.paused) { this.paused = true; this.emit('pause'); }
  }
  load() { this.loads.push(this.src); this.ended = false; }
  removeAttribute(name) { if (name === 'src') this.src = ''; }
  end() { this.ended = true; this.paused = true; this.emit('ended'); }
}
function fixture({ media = true, unsupported = false } = {}) {
  const player = new Player(), button = new Element(), status = new Element();
  const document = new Element(), window = new Element();
  document.visibilityState = 'visible';
  const intervals = new Map(), timeouts = new Map();
  let timerId = 0;
  const timers = {
    setInterval(fn) { const id = ++timerId; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
    setTimeout(fn) { const id = ++timerId; timeouts.set(id, fn); return id; },
    clearTimeout(id) { timeouts.delete(id); },
  };
  const actions = {}, navigator = media ? { mediaSession: {
    setActionHandler(name, handler) {
      if (unsupported) throw new Error('unsupported');
      actions[name] = handler;
    },
  } } : {};
  const created = [], revoked = [], calls = [], logs = [];
  const state = { events: [], fetchHook: null, unauthorized: 0 };
  const response = (body, status = 200) => ({
    ok: status === 200, status,
    json: async () => body,
    blob: async () => body instanceof Blob ? body : new Blob([body], { type: 'audio/wav' }),
  });
  const controller = new VoicePlayback({
    player, button, status, document, window, navigator, timers,
    MediaMetadata: class { constructor(values) { Object.assign(this, values); } },
    urls: {
      createObjectURL(blob) { const url = `blob:${created.length}`; created.push({ url, blob }); return url; },
      revokeObjectURL(url) {
        assert.equal(player.src, '', 'detach media before revocation');
        assert.equal(player.paused, true, 'stop media before revocation');
        assert.equal(player.loads.at(-1), '', 'reset media before revocation');
        assert.ok(!revoked.includes(url), 'each URL is revoked only once');
        revoked.push(url);
      },
    },
    fetch: async (path, options) => {
      calls.push({ path, options });
      const result = state.fetchHook?.(path, options);
      if (result) return result;
      return response(path.endsWith('/events') ? state.events : path.split('/').at(-1));
    },
    log: (...args) => logs.push(args),
    onUnauthorized: () => state.unauthorized++,
  });
  const audioCalls = () => calls.filter(call => call.path.includes('/audio/'));
  const poll = async (...ids) => {
    state.events = ids.map(id => ({ id, text: 'PRIVATE speech', member: 'PRIVATE member' }));
    await controller.poll(); await flush();
  };
  const enableIdle = async () => {
    button.emit('click'); await flush(); player.end(); await flush();
  };
  return { controller, player, button, status, document, window, navigator, actions,
    created, revoked, calls, logs, state, response, audioCalls, poll, enableIdle, intervals, timeouts };
}

test('dashboard supplies exactly one persistent DOM player and starts disabled', async () => {
  const html = fs.readFileSync('index.html', 'utf8');
  assert.equal((html.match(/<audio\b/g) || []).length, 1);
  assert.match(html, /<audio id="voicePlayer" preload="auto" playsinline hidden>/);
  const app = fs.readFileSync('app.js', 'utf8');
  assert.match(app, /player:\$\("#voicePlayer"\)/);
  assert.doesNotMatch(app + fs.readFileSync('voice_playback.js', 'utf8'), /new Audio\(|localStorage|AudioContext/);
  const f = fixture();
  assert.equal(f.status.textContent, 'Browser playback waits for your permission.');
  await f.poll('A');
  assert.equal(f.calls.length, 0);
  assert.equal(f.player.plays.length, 0);
  f.button.emit('click');
  assert.equal(f.player.plays.length, 1, 'play happens synchronously within the gesture');
  assert.equal(f.player.loads[0], f.player.plays[0]);
  assert.equal(f.button.textContent, 'Disable voice playback');
  assert.equal(f.button.attributes['aria-pressed'], 'true');
  assert.equal(f.status.textContent, 'Voice playback enabled.');
  f.controller.enable();
  assert.equal(f.player.plays.length, 1);
  assert.equal(f.intervals.size, 1);
  f.controller.destroy();
});

test('enable confirmation is a finite audible WAV', async () => {
  const f = fixture(); f.controller.enable();
  const blob = f.created[0].blob;
  assert.equal(blob.type, 'audio/wav');
  const data = Buffer.from(await blob.arrayBuffer());
  assert.equal(data.subarray(0, 4).toString(), 'RIFF');
  assert.equal(data.subarray(8, 12).toString(), 'WAVE');
  assert.equal(data.readUInt32LE(40) / data.readUInt32LE(28), 0.12);
  assert.ok(data.subarray(44).some(value => value !== 0));
  assert.ok(!f.player.loop);
  f.controller.destroy();
});

test('A B C reuse one player in order; every Blob URL is safely revoked after use', async () => {
  const f = fixture(); await f.enableIdle();
  await f.poll('A', 'B', 'C');
  assert.deepEqual(f.audioCalls().map(x => x.path), ['/api/voice/audio/A']);
  assert.equal(f.status.textContent, 'Playing speech…');
  assert.equal(f.revoked.length, 1, 'only the finished cue has been revoked');
  for (const id of ['A', 'B', 'C']) {
    assert.equal(await f.created.at(-1).blob.text(), id);
    f.player.end(); await flush();
  }
  assert.deepEqual(f.audioCalls().map(x => x.path), ['A', 'B', 'C'].map(id => `/api/voice/audio/${id}`));
  assert.equal(f.player.plays.length, 4);
  assert.equal(f.created.length, f.revoked.length);
  assert.equal(f.status.textContent, 'Waiting for new speech…');
  f.controller.destroy();
});

test('blocked play stops all automatic attempts and retries retained audio on a new tap', async () => {
  const f = fixture(); await f.enableIdle();
  f.player.results.push(() => Promise.reject(blocked()));
  await f.poll('A', 'B');
  assert.equal(f.status.textContent, 'Playback was blocked. Tap Enable voice playback to try again.');
  assert.equal(f.button.textContent, 'Enable voice playback');
  assert.equal(f.intervals.size, 0);
  const attempts = f.player.plays.length;
  await f.controller.poll(); f.window.emit('focus'); f.window.emit('pageshow');
  f.actions.play(); await flush();
  assert.equal(f.player.plays.length, attempts);
  assert.equal(f.audioCalls().length, 1);
  f.button.emit('click');
  assert.equal(f.player.plays.length, attempts + 1, 'retained clip is retried inside the gesture');
  await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'A');
  f.player.end(); await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'B');
  assert.equal(f.audioCalls().length, 2, 'A is never fetched twice');
  assert.doesNotMatch(JSON.stringify(f.logs), /PRIVATE|\/audio\/|blob:/);
  f.controller.destroy();
});

test('blocking the enable cue leaves server events unconsumed', async () => {
  const f = fixture(); f.state.events = [{ id: 'A' }];
  f.player.results.push(() => Promise.reject(blocked()));
  f.controller.enable(); await flush();
  assert.equal(f.audioCalls().length, 0);
  assert.equal(f.controller.enabled, false);
  f.controller.destroy();
});

for (const failure of ['error', 'abort', 'rejection', 'sync throw']) {
  test(`${failure} in one clip does not stall later speech`, async () => {
    const f = fixture(); await f.enableIdle();
    if (failure === 'rejection') f.player.results.push(() => Promise.reject(new Error('private')));
    if (failure === 'sync throw') f.player.results.push(() => { throw new Error('private'); });
    await f.poll('A', 'B');
    if (['error', 'abort'].includes(failure)) f.player.emit(failure);
    await flush();
    assert.equal(await f.created.at(-1).blob.text(), 'B');
    assert.equal(f.controller.enabled, true);
    f.player.end(); await flush();
    assert.equal(f.created.length, f.revoked.length);
    f.controller.destroy();
  });
}

test('an expired or failed consume-on-read request is skipped without a retry', async () => {
  const f = fixture(); await f.enableIdle();
  f.state.fetchHook = path => path.endsWith('/A') ? f.response(null, 404) : null;
  await f.poll('A', 'B');
  assert.equal(await f.created.at(-1).blob.text(), 'B');
  await f.poll('A', 'B');
  assert.equal(f.audioCalls().length, 2);
  f.controller.destroy();
});

test('disable stops and resets audio, preserves unconsumed queue, and re-enable resumes', async () => {
  const f = fixture(); await f.enableIdle(); await f.poll('A', 'B');
  f.button.emit('click');
  assert.equal(f.player.src, ''); assert.equal(f.player.paused, true);
  assert.equal(f.status.textContent, 'Voice playback disabled.');
  assert.equal(f.created.length, f.revoked.length);
  const calls = f.calls.length;
  await f.poll('A', 'B', 'C'); f.player.end(); await flush();
  assert.equal(f.calls.length, calls);
  f.button.emit('click'); await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'A');
  f.player.end(); await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'B');
  assert.equal(f.intervals.size, 1);
  f.controller.destroy();
});

test('disabling during an audio fetch retains its consumed WAV without starting playback', async () => {
  const f = fixture(); await f.enableIdle(); const pending = deferred();
  f.state.fetchHook = path => path.endsWith('/A') ? pending.promise : null;
  await f.poll('A', 'B'); f.controller.disable();
  pending.resolve(f.response('A')); await flush();
  assert.equal(f.created.length, 1, 'only cue played');
  assert.equal(f.audioCalls().length, 1);
  f.controller.enable(); await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'A');
  assert.equal(f.audioCalls().length, 1);
  f.controller.destroy();
});

test('late play rejection after disable and re-enable cannot stop the new attempt', async () => {
  const f = fixture(); await f.enableIdle(); const pending = deferred();
  f.player.results.push(() => pending.promise); await f.poll('A');
  f.controller.disable(); f.controller.enable(); await flush();
  pending.reject(blocked()); await flush();
  assert.equal(f.controller.enabled, true);
  assert.equal(f.status.textContent, 'Playing speech…');
  f.controller.destroy();
});

test('event polling never overlaps, even across enable and lifecycle events', async () => {
  const f = fixture(), pending = deferred();
  f.state.fetchHook = path => path.endsWith('/events') ? pending.promise : null;
  f.controller.enable();
  f.window.emit('focus'); f.window.emit('pageshow'); f.document.emit('visibilitychange');
  for (const tick of f.intervals.values()) tick();
  assert.equal(f.calls.length, 1);
  f.controller.disable(); f.controller.enable();
  assert.equal(f.calls.length, 1);
  pending.resolve(f.response([])); await flush();
  assert.equal(f.controller.polling, false);
  f.state.fetchHook = null; await f.controller.poll();
  assert.equal(f.calls.length, 2);
  f.controller.destroy();
});

test('polling continues during playback and deduplicates queued, fetching, playing and consumed IDs', async () => {
  const f = fixture(); await f.enableIdle(); const pending = deferred();
  f.state.fetchHook = path => path.endsWith('/A') ? pending.promise : null;
  await f.poll('A', 'A', 'B', 'B');
  await f.poll('A', 'B');
  assert.deepEqual(f.controller.queue, ['B']);
  pending.resolve(f.response('A')); await flush();
  await f.poll('A', 'B', 'C', 'C');
  assert.deepEqual(f.controller.queue, ['B', 'C']);
  f.player.end(); await flush();
  await f.poll('A', 'B', 'C');
  f.player.end(); await flush(); f.player.end(); await flush();
  await f.poll('A', 'B', 'C');
  assert.deepEqual(f.audioCalls().map(x => x.path), ['A', 'B', 'C'].map(id => `/api/voice/audio/${id}`));
  f.controller.destroy();
});

test('deduplication history and pending event queue remain bounded', async () => {
  const f = fixture(); await f.enableIdle();
  for (let i = 0; i < 300; i++) f.controller.remember(`old-${i}`);
  assert.equal(f.controller.consumed.size, 256);
  f.actions.pause();
  await f.poll(...Array.from({ length: 200 }, (_, i) => `new-${i}`));
  assert.equal(f.controller.queued.size, 100);
  assert.equal(f.controller.queue.length, 100);
  assert.equal(f.audioCalls().length, 0);
  f.controller.destroy();
});

test('visibility, pageshow and focus recovery immediately poll but hidden changes do not', async () => {
  const f = fixture(); await f.enableIdle();
  let count = f.calls.length;
  f.document.visibilityState = 'hidden'; f.document.emit('visibilitychange');
  await flush(); assert.equal(f.calls.length, count);
  for (const [target, event] of [[f.document, 'visibilitychange'], [f.window, 'focus'], [f.window, 'pageshow']]) {
    f.document.visibilityState = 'visible'; target.emit(event); await flush();
    assert.equal(f.calls.length, ++count);
  }
  f.controller.destroy();
});

for (const options of [{ media: false }, { unsupported: true }]) {
  test(`Media Session ${options.media === false ? 'absence' : 'unsupported actions'} does not break playback`, async () => {
    const f = fixture(options); await f.enableIdle(); await f.poll('A');
    assert.equal(f.status.textContent, 'Playing speech…');
    f.player.end(); await flush();
    assert.equal(f.created.length, f.revoked.length);
    f.controller.destroy();
  });
}

test('Media Session controls the same player, pauses the queue, and contains no speech metadata', async () => {
  const f = fixture(); await f.enableIdle(); await f.poll('A', 'B');
  const session = f.navigator.mediaSession, url = f.player.src;
  assert.deepEqual({ ...session.metadata }, { title: 'Plurapack speech', album: 'Plurapack' });
  assert.equal(session.playbackState, 'playing');
  f.actions.pause();
  assert.equal(f.player.paused, true); assert.equal(session.playbackState, 'paused');
  await f.poll('A', 'B'); assert.equal(f.audioCalls().length, 1);
  f.actions.play(); await flush();
  assert.equal(f.player.plays.at(-1), url);
  assert.equal(f.created.length, 2, 'resume reuses the loaded clip URL');
  assert.equal(session.playbackState, 'playing');
  f.player.end(); await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'B');
  f.actions.stop();
  assert.equal(f.controller.enabled, false); assert.equal(f.player.src, '');
  assert.equal(session.playbackState, 'none'); assert.equal(session.metadata, null);
  f.controller.destroy();
});

test('an OS/browser pause and an interrupted play promise wait for explicit media resume', async () => {
  const f = fixture(); await f.enableIdle(); const pending = deferred();
  f.player.results.push(() => pending.promise); await f.poll('A', 'B');
  f.player.pause();
  pending.reject(Object.assign(new Error(), { name: 'AbortError' })); await flush();
  assert.equal(f.controller.paused, true); assert.equal(f.audioCalls().length, 1);
  f.actions.play(); await flush();
  assert.equal(f.status.textContent, 'Playing speech…');
  assert.equal(await f.created.at(-1).blob.text(), 'A');
  f.controller.destroy();
});

test('WAV bytes and MIME type reach the persistent player unchanged with private no-store requests', async () => {
  const f = fixture(); await f.enableIdle();
  const wav = new Blob(['RIFF\0\0\0\0WAVEdata'], { type: 'audio/wav' });
  f.state.fetchHook = path => path.includes('/audio/') ? f.response(wav) : null;
  await f.poll('A');
  assert.strictEqual(f.created.at(-1).blob, wav);
  const { options } = f.audioCalls()[0];
  assert.equal(options.headers.Accept, 'audio/wav');
  for (const { options } of f.calls) {
    assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
  }
  f.controller.destroy();
});

test('request timeout clears the polling guard and permits recovery', async () => {
  const f = fixture(); await f.enableIdle();
  f.state.fetchHook = (path, options) => new Promise((_, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('timeout')));
  });
  const pending = f.controller.poll();
  for (const timeout of [...f.timeouts.values()]) timeout();
  await pending;
  assert.equal(f.controller.polling, false);
  assert.equal(f.status.textContent, 'Voice playback temporarily unavailable.');
  f.state.fetchHook = null; await f.poll('A');
  assert.equal(f.status.textContent, 'Playing speech…');
  f.controller.destroy();
});

test('a play promise that never settles times out and releases the queue', async () => {
  const f = fixture(); await f.enableIdle();
  f.player.results.push(() => new Promise(() => {})); await f.poll('A', 'B');
  for (const timeout of [...f.timeouts.values()]) timeout();
  await flush();
  assert.equal(await f.created.at(-1).blob.text(), 'B');
  f.controller.destroy();
});

test('auth expiry releases private audio, stops polling, and clears account state', async () => {
  const f = fixture(); await f.enableIdle(); await f.poll('A', 'B');
  f.state.fetchHook = () => f.response(null, 401);
  await f.controller.poll();
  assert.equal(f.state.unauthorized, 1); assert.equal(f.controller.disposed, true);
  assert.equal(f.player.src, ''); assert.equal(f.controller.current, null);
  assert.equal(f.controller.queue.length, 0); assert.equal(f.intervals.size, 0);
  assert.equal(f.actions.play, null);
});

test('leaving the page releases URLs and returning from bfcache requires another tap', async () => {
  const f = fixture(); await f.enableIdle(); await f.poll('A');
  const pagehide = new Event('pagehide'); pagehide.persisted = true;
  f.window.dispatchEvent(pagehide); f.window.emit('pageshow'); await flush();
  assert.equal(f.controller.enabled, false); assert.equal(f.player.src, '');
  f.button.emit('click'); await flush();
  assert.equal(f.status.textContent, 'Playing speech…');
  f.window.emit('pagehide');
  assert.equal(f.controller.disposed, true);
  assert.equal(f.created.length, f.revoked.length);
});
