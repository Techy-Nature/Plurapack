# Configuration reference

Plurapack reads configuration from environment variables. It does not automatically load `.env` files.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `STOAT_BOT_TOKEN` | One platform token required | — | Private Stoat bot token. |
| `FLUXER_BOT_TOKEN` | One platform token required | — | Private Fluxer bot token. Set both tokens to run both connections together. |
| `PLURAPACK_PREFIX` | No | `p;` | Prefix for bot commands, not member tags. |
| `PLURAPACK_DATA_DIR` | No | — | Persistent data directory. The default database is created beneath it. |
| `PLURAPACK_DATABASE` | No | `PLURAPACK_DATA_DIR/plurapack.sqlite3`, or `plurapack.sqlite3` | Exact SQLite database path; takes precedence over `PLURAPACK_DATA_DIR`. |
| `PLURAPACK_TTS_URL` | No | disabled | Full protected Modal Chatterbox Turbo v03 HTTP(S) endpoint. |
| `PLURAPACK_TTS_API_KEY` | For protected Modal TTS | — | Modal proxy credential (`wk-....ws-....`), sent server-to-server as `Authorization: Bearer ...`. |
| `PLURAPACK_TTS_QUEUE_LIMIT` | No | `8` | Pending speech limit, from 1 to 1000. |
| `PLURAPACK_TTS_WORKERS` | No | `1` | Speech workers, from 1 to 4. |

Restart the bot after changing its process environment. Keep secrets out of Git, shell history where possible, support messages, and screenshots.

The database directory is created during startup. Plurapack reports a clear
error instead of silently using another location if it cannot use the
configured directory. Changing either storage variable does not move or delete
an existing database. Browser-audio clips remain in a bounded temporary spool;
`PLURAPACK_DATA_DIR` does not make them persistent.
# Private custom voice storage

Custom member voices use the `VoiceStorage` abstraction and are uploaded by the
dashboard, Stoat, or Fluxer to Plurapack—not by the browser directly to the
storage host. Configure `VOICE_STORAGE_PROVIDER=forgejo`,
`VOICE_FORGEJO_BASE_URL`, `VOICE_FORGEJO_OWNER`, `VOICE_FORGEJO_REPO`,
`VOICE_FORGEJO_BRANCH`, and the server-only `VOICE_STORAGE_API_KEY`. The token needs contents read/write access to the
private repository. `VOICE_MAX_UPLOAD_BYTES` controls the upload limit.
`VOICE_MAX_REFERENCE_SECONDS` defaults to 30 and rejects longer WAV references
before storage; set it to the Chatterbox operator's configured duration limit.

Custom references are validated WAV files stored only as
`custom/<random-uuid>.wav`; display names and member associations remain in
Plurapack's database. Existing generic references remain supported separately.
Renaming a member or voice never renames the stored object.

Voice source type is stored explicitly. `Jordan.wav` with generic source becomes
`generic:Jordan`; a custom UUID filename becomes `custom:<storage UUID>`.
Generic files remain at `generic/Jordan.wav`; custom files remain at
`custom/<UUID>.wav`. A custom display name never determines its storage identity.
No index JSON or file renaming is required.

At synthesis time Plurapack posts only `{"text": "...", "voice_id": "..."}` to
Modal. Modal independently reads the reference WAV from private Forgejo with its
own **read-only** token. `VOICE_STORAGE_API_KEY` is Plurapack's separate
**write-capable Forgejo credential** for uploads and deletion;
`PLURAPACK_TTS_API_KEY` is only for invoking Modal. Tokens and private raw URLs
are not exposed to clients. There is no reference-upload synchronization or
reference-audio fetch from Plurapack during TTS.

Modal returns `audio/wav`. Plurapack validates the WAV, limits response bytes
(default 16 MiB), and retains connection/response timeouts (5/120 seconds).
The current runner accepts at most **500 characters** per request. Overlong
spoken text raises a sanitized error without truncation or private-text logging.
Stored style/settings metadata remains supported, but v03 does not apply it;
formatted spoken parts are joined into a single request.

Existing UUID custom and explicit generic records need no database migration.
Legacy clones map only if their canonical UUID filename matches the member's
voice metadata. Otherwise re-upload as custom or explicitly select a generic
voice; an old named installed reference is not guessed into a namespace.

Use the production values in `.env.example`: base URL `https://git.gay`, owner
`TechyNestBots`, repository `plurapack-voice-index`, and branch `main`.
The default repository is now `plurapack-voice-index`; operators using another
existing repository must set `VOICE_FORGEJO_REPO` explicitly. Changing the
repository configuration does not move existing audio. Plurapack and Modal must
point to the repository containing the selected recordings.

Deleting a custom voice removes its current Forgejo entry, but Git history may
retain older content. It is not secure historical erasure. Member and system
permanent deletion retain the existing custom-recording cleanup flow.

The dashboard supports upload, rename, default selection, and deletion. Stoat
and Fluxer provide `voice upload MEMBER VOICE_NAME [PLAYBACK]` (with one WAV or
MP3 attachment), `voice list MEMBER`, `voice default MEMBER VOICE`,
`voice rename MEMBER VOICE NEW_NAME`, and `voice delete MEMBER VOICE`. Quote
names containing spaces; a voice may be selected by exact name or UUID.
`voice generic MEMBER FILENAME.wav` switches back to a bundled generic voice
without deleting custom voices. `voice settings MEMBER JSON` preserves the
per-member Chatterbox settings workflow (quote the JSON argument). MP3
requires `ffmpeg` on the bot host and is normalized to mono
24 kHz WAV before validation and private storage. Member and system deletion
remove current custom objects before deleting database records; a storage error
stops database deletion so the metadata remains available for retry.
The dashboard reports whether the selected source is generic or custom. It does
not offer a generic-voice dropdown yet; use the bot command with the existing
filenames under the private repository's `generic/` directory.
