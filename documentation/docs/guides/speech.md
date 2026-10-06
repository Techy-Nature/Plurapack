# Optional speech

Speech is optional and **off by default**. Inference is provided by the configured
protected Modal endpoint. The repository includes the current production runner at
`deployment/chatterbox_runner_v05.py`. Plurapack needs no local GPU, CUDA, PyTorch,
or Chatterbox model installation.

## Operator setup

Set `PLURAPACK_TTS_URL` to the full Modal HTTP endpoint and
`PLURAPACK_TTS_API_KEY` to its Modal proxy Bearer credential (`wk-....ws-....`).
Both stay on the server. See the [configuration reference](../reference/configuration.md)
and `.env.example` for private Forgejo storage configuration.

Deploy the runner with Modal using the existing `hf-token` and
`gitgay-voice-reader` secrets. The latter supplies `GITGAY_TOKEN` and should remain
read-only. `chatterbox_runner_v05.py` keeps the established text/voice-ID contract,
adds semantic speech parts and active voice settings, and uses two Chatterbox
models: Nano for the lightweight ordinary-speech path and Original Chatterbox for
semantic formatting and CFG/exaggeration-controlled speech. If Modal gives the
updated deployment a different endpoint URL, update `PLURAPACK_TTS_URL` in Railway.

Plurapack's Forgejo token needs write access for upload/deletion. Modal uses its
separate read-only Forgejo token against the same private repository and branch.
Keep that repository private.

Optional controls are `PLURAPACK_TTS_QUEUE_LIMIT` (1–1000, default 8) and
`PLURAPACK_TTS_WORKERS` (1–4, default 1).

## Configure a member

```text
p;voice generic MEMBER Jordan.wav
p;voice upload MEMBER "My voice" send
p;voice list MEMBER
p;voice default MEMBER "My voice"
p;voice rename MEMBER "My voice" "New name"
p;voice delete MEMBER "New name"
p;voiceoff MEMBER
```

Attach one WAV or MP3 to the upload command. MP3 conversion requires `ffmpeg` on
Plurapack's host. Uploads are validated against the configured byte and duration
limits, assigned a UUID, and stored as `custom/<UUID>.wav` in private Forgejo.
Each member can keep multiple named custom voices; names are metadata and renaming
does not rename the recording. The dashboard also offers custom voice management.

For ordinary speech, Plurapack sends `text`, the namespaced `voice_id`, and any
active supported voice settings to Modal. A stored generic selection `Jordan.wav`
becomes `generic:Jordan`; custom selections become `custom:<storage UUID>`, even if
the custom display name is Jordan. Generic files stay at `generic/Jordan.wav`,
without renaming or a required index JSON. Modal retrieves the reference WAV from
Forgejo independently. Custom recordings are never uploaded to Modal on every
message.

The active v05 settings are `temperature`, `exaggeration`, `cfg_weight`, `seed`,
and `speed_factor`. Ordinary speech uses Chatterbox Nano by default. If a member
explicitly configures `exaggeration` or `cfg_weight`, v05 routes that message through
Original Chatterbox so those controls actually take effect. An explicitly selected
`speed_factor` is applied after generation with pitch-preserving time stretching.
Because time stretching can make short speech sound metallic, the built-in semantic
styles deliberately do **not** set `speed_factor`; it remains an opt-in per-member
control. Historical `language`, `split_text`, and `chunk_size` values remain valid
stored metadata for compatibility but are not used by the current English
Nano/Original runner.

Formatted speech uses the same namespaced `voice_id` plus a list of semantic
`parts`, each containing only spoken text and a style (`normal`, `emphasis`,
`mumble`, or `whisper`). Omitted text is removed inside Plurapack before the
request. No member/system names or custom voice display names are needed by Modal.

`send` delivers one `.wav` attachment replying to the Stoat or Fluxer proxy.
`local` publishes audio for the authenticated dashboard browser, where the user
must enable voice playback. `both` uses the same synthesized WAV for both
destinations. `off` does neither. Browser responses use `audio/wav`.

Modal accepts **at most 500 characters of spoken text per request**. Longer text
fails speech generation explicitly; it is never silently truncated. For formatted
speech, the limit is applied to the combined spoken parts after omissions. Text
proxying continues independently of speech failures. Requests and errors do not
log private text, voice UUIDs, credentials, or upstream response bodies.

## Browser / Local playback on mobile

Open the dashboard and explicitly tap **Enable voice playback** once per page
session. This plays a brief audible confirmation tone through the same persistent
audio element that plays subsequent WAV clips in order. Reloading requires a new
tap; browser permission is separate from each member's `off`, `local`, `send`, or
`both` setting. If the browser blocks playback, tap **Enable voice playback** again.
Plurapack stops automatic attempts until you do so.

**Disable voice playback** stops and resets the player and stops new local audio
requests. It does not change member settings or Stoat/Fluxer attachment delivery.
A clip already fetched (including a request completing just after disabling) is
kept only in page memory, so enabling again can replay it from the beginning
without consuming its one-use endpoint twice. Generated audio is never saved to
browser storage; its object URL is released when playback ends or stops. Unfetched
clips remain subject to the server's short expiry (120 seconds by default), so old
speech may be unavailable after a long pause. Multiple dashboard tabs compete for
the same one-use clips; use one tab for playback.

Where supported, Android browsers may expose lock-screen or notification media
controls through the **Media Session API**. Play and pause control the same player;
pause also holds subsequent clips, and stop disables browser playback. Metadata
uses only “Plurapack speech” / “Plurapack”, never message text or member details.
Media Session is optional and browser-controlled. It does **not** provide a
guaranteed permanent Android notification or uninterrupted background playback.

Browsers may throttle polling, suspend an inactive background page when nothing
is playing, or interrupt media when switching apps or locking the screen. Reopen
or refocus Plurapack if Android has suspended the browser. Returning to the page
triggers an immediate poll; if the page was reloaded or playback was blocked,
enable playback again. There is no silent looping audio, wake lock, or keepalive
service. A future native Android wrapper could provide a true foreground media
playback service if needed.

### Manual Android checks

1. Open the Plurapack dashboard.
2. Tap **Enable voice playback** and listen for the brief confirmation tone.
3. Trigger one TTS message for a member using `local` or `both`.
4. Confirm the generated WAV plays.
5. Trigger several messages rapidly and confirm ordered playback.
6. Switch to another app while a clip is playing.
7. Lock the screen while a clip is playing.
8. Check whether Android exposes media controls; try pause and play if available.
9. Return to Plurapack and trigger another clip.
10. Confirm playback still works without reloading.
11. Leave the browser idle/backgrounded, return later, and verify polling recovers
    with a newly generated clip (old clips may have expired).

Also check that disabling stops playback and new audio requests, re-enabling
resumes, and `send`/`both` attachments still reach Stoat or Fluxer independently.

## Semantic formatting

```text
p;voiceformat MEMBER on normal
p;voiceformat MEMBER on mumble
p;voiceformat MEMBER on whisper
p;voiceformat MEMBER on omit
p;voiceformat MEMBER off
```

The final argument controls how `~~crossed-out text~~` is spoken; it does **not**
set the entire member's speaking style. When formatting is enabled:

- ordinary and quoted text is spoken normally;
- `*single-asterisk actions*` are omitted;
- `**double-asterisk text**` is spoken with emphasis;
- `~~crossed-out text~~` follows the selected `normal`, `mumble`, `whisper`, or
  `omit` mode;
- Markdown delimiters themselves are never spoken.

`omit` is handled before inference, so omitted words never leave Plurapack. For
other formatted spans, Plurapack sends one semantic-parts request and v05 uses
Original Chatterbox for the formatted message. The built-in style generation
controls intentionally stay away from the very low CFG/exaggeration combinations
that produced metallic short spans in real use:

- emphasis: `exaggeration=0.75`, `cfg_weight=0.40`;
- mumble: `exaggeration=0.35`, `cfg_weight=0.30`;
- whisper: `exaggeration=0.30`, `cfg_weight=0.35`.

After generation, the styles use **non-temporal** waveform processing. `mumble`
softens consonant articulation with a gentle low-pass blend and slightly lowers
the level. `whisper` reduces low-frequency body and overall level for a softer,
thinner delivery. Neither style resamples, time-stretches, or changes the number
of samples, avoiding the phase-vocoder artifacts heard with the earlier presets.
`emphasis` gets only a small level lift. These are whisper-/mumble-like treatments;
Original Chatterbox does not expose a literal native whisper or mumble control.

A member may still explicitly configure `speed_factor`; that setting remains a
separate pitch-preserving time-stretch feature and is honored for normal and
formatted speech. The generated segment tensors are concatenated before encoding
**one** WAV; WAV containers are never concatenated byte-for-byte.

The first formatted message after a cold start may take longer because Original
Chatterbox is loaded lazily. Ordinary messages remain on Nano unless explicit
member CFG/exaggeration settings require Original. Nano shares the Turbo-family
inference path and does not support CFG/exaggeration, which is why styled speech is
kept on Original Chatterbox instead.

Edits and re-proxy operations invalidate stale queued/browser audio and enqueue
speech using the replacement text and selected member voice. Generation and
platform/browser delivery remain best effort and do not block text proxying.

## Existing records

Existing generic and UUID custom selections require no schema change. Old
`legacy_clone` records map to custom IDs only when their canonical UUID filename
matches that member's existing voice metadata. Otherwise speech reports a
sanitized configuration error: re-upload the reference as a custom voice or
explicitly select a known generic filename. Plurapack does not guess a namespace.
