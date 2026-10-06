# Optional speech

Speech is optional and **off by default**. Inference is provided by the configured
protected Modal endpoint running Chatterbox Turbo (`chatterbox_runner_v03.py`).
Plurapack needs no local GPU, CUDA, PyTorch, or Chatterbox model installation.

## Operator setup

Set `PLURAPACK_TTS_URL` to the full Modal HTTP endpoint and
`PLURAPACK_TTS_API_KEY` to its Modal proxy Bearer credential (`wk-....ws-....`).
Both stay on the server. See the [configuration reference](../reference/configuration.md)
and `.env.example` for private Forgejo storage configuration.

Plurapack's Forgejo token needs write access for upload/deletion. Configure a
separate read-only Forgejo token on Modal, pointing to the same private repository
and branch. Keep that repository private.

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
Each member can keep multiple named voices; names are metadata and renaming does
not rename the recording. The dashboard also offers custom voice management.

During speech, Plurapack sends only `text` and `voice_id` to Modal. A stored generic
selection `Jordan.wav` becomes `generic:Jordan`; custom selections become
`custom:<storage UUID>`, even if the custom display name is Jordan. Generic files
stay at `generic/Jordan.wav`, without renaming or a required index JSON. Modal
retrieves the reference WAV from Forgejo independently. Custom recordings are
never uploaded to Modal on every message.

`send` delivers one `.wav` attachment replying to the Stoat or Fluxer proxy.
`local` publishes audio for the authenticated dashboard browser, where the user
must enable voice playback. `both` uses the same synthesized WAV for both
destinations. `off` does neither. Browser responses use `audio/wav`.

Modal v03 accepts **at most 500 characters of spoken text per request**. Longer
text fails speech generation explicitly; it is never silently truncated. Text
proxying continues independently of speech failures. The limit applies after
optional formatting/omissions. Requests and errors do not log private text,
voice UUIDs, credentials, or upstream response bodies.

## Semantic formatting and current limitations

```text
p;voiceformat MEMBER on normal
p;voiceformat MEMBER off
```

When enabled, ordinary and quoted text is spoken, single-asterisk actions are
omitted, and Markdown delimiters are removed. Crossed-out modes remain `normal`,
`mumble`, `omit`, and `whisper`; `omit` removes those words.

Modal v03 accepts only text and a voice ID. Stored voice settings (exaggeration,
configuration weight, speed, etc.) and emphasis/mumble/whisper preferences are
preserved, but currently do not alter delivery. Parsed spoken parts are joined
and synthesized in **one request producing one WAV**, never by concatenating WAV
containers. A future runner may support style parameters.

Edits and re-proxy operations invalidate stale queued/browser audio and enqueue
speech using the replacement text and selected member voice. Generation and
platform/browser delivery remain best effort and do not block text proxying.

## Existing records

Existing generic and UUID custom selections require no schema change. Old
`legacy_clone` records map to custom IDs only when their canonical UUID filename
matches that member's existing voice metadata. Otherwise speech reports a
sanitized configuration error: re-upload the reference as a custom voice or
explicitly select a known generic filename. Plurapack does not guess a namespace.
