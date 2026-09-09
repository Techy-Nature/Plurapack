# Optional speech

Speech is optional and **off by default**. It requires an operator-managed Chatterbox TTS server and approved reference files already present on the server. Users cannot upload reference audio through chat.

## Operator setup

Set the endpoint and approved reference directory before starting Plurapack:

```bash
export PLURAPACK_TTS_URL='http://127.0.0.1:8004/tts'
export PLURAPACK_VOICE_REFERENCE_DIR='/private/reference_audio'
```

Optional controls are `PLURAPACK_TTS_QUEUE_LIMIT` (1–1000, default 8) and `PLURAPACK_TTS_WORKERS` (1–4, default 1). Do not expose an unauthenticated voice-cloning endpoint to the internet.

## Configure a member

```text
p;voice MEMBER FILENAME send {}
p;voiceoff MEMBER
```

Only `send` playback currently produces output: one MP3 attachment associated with the proxy. Although the data model reserves local modes, a server-only bot cannot force playback on someone else's device.

## Semantic formatting

```text
p;voiceformat MEMBER on normal
p;voiceformat MEMBER off
```

When enabled, ordinary and quoted text is spoken, single-asterisk actions are omitted, Markdown punctuation is removed, and double-asterisk text receives stronger emphasis. Crossed-out text modes are `normal`, `mumble`, `omit`, and `whisper`.

Speech generation is best effort. Editing or re-proxying a message does not create replacement audio.
