# Configuration reference

Plurapack reads configuration from environment variables. It does not automatically load `.env` files.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `STOAT_BOT_TOKEN` | One platform token required | — | Private Stoat bot token. |
| `FLUXER_BOT_TOKEN` | One platform token required | — | Private Fluxer bot token. Set both tokens to run both connections together. |
| `PLURAPACK_PREFIX` | No | `p;` | Prefix for bot commands, not member tags. |
| `PLURAPACK_DATA_DIR` | No | — | Persistent data directory. The default database is created beneath it. |
| `PLURAPACK_DATABASE` | No | `PLURAPACK_DATA_DIR/plurapack.sqlite3`, or `plurapack.sqlite3` | Exact SQLite database path; takes precedence over `PLURAPACK_DATA_DIR`. |
| `PLURAPACK_TTS_URL` | No | disabled | Full HTTP(S) Chatterbox `/tts` endpoint. |
| `PLURAPACK_VOICE_REFERENCE_DIR` | For voice setup | — | Approved local reference-audio directory. |
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

Deleting a custom voice removes its current repository entry, but Git history
can retain older content. It is not secure historical erasure. A future backend
can implement `VoiceStorage` (`put_voice`, `get_voice`, `delete_voice`, and
`exists`) to provide object-store deletion without changing adapters or APIs.

At synthesis time Plurapack retrieves the default custom WAV using its storage
UUID, uploads it server-to-server to Chatterbox's `/upload_reference` endpoint,
and requests `/tts` in `clone` mode with `<uuid>.wav`. Generic voices are not
uploaded: they use Chatterbox's bundled `voices/` directory through
`voice_mode=predefined` and `predefined_voice_id`. Successfully synchronized references are
cached in the Plurapack process; a missing-reference response invalidates the
cache and causes one re-upload. Set `PLURAPACK_TTS_API_KEY` when a protected
reverse proxy accepts a Bearer credential. Chatterbox itself has no native API
authentication, so it must otherwise be reachable only over a trusted private
deployment path. Voice bytes and credentials are never sent to browsers or chat
clients.

The supported Chatterbox server does not currently expose an API for deleting
files from `reference_audio/`. Deleting a voice removes the Forgejo object and
Plurapack metadata, but a reference synchronized before deletion can remain on
the operator-managed Chatterbox host. Operators should periodically clean that
private directory. Plurapack does invalidate its process-local synchronization
cache on deletion. TODO: integrate remote reference deletion if upstream adds a
safe authenticated endpoint.

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
not offer a generic-voice dropdown yet because Plurapack has no reliable local
catalog of the operator's Chatterbox `voices/` directory; use the bot command
until a server-side catalog endpoint is integrated.
