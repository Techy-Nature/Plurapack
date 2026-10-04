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
`VOICE_FORGEJO_BRANCH`, `VOICE_FORGEJO_USERNAME`, and the server-only
`VOICE-STORAGE-API-KEY`. The token needs contents read/write access to the
private repository. `VOICE_MAX_UPLOAD_BYTES` controls the upload limit.

Custom references are validated WAV files stored only as
`custom/<random-uuid>.wav`; display names and member associations remain in
Plurapack's database. Existing generic references remain supported separately.
Renaming a member or voice never renames the stored object.

Deleting a custom voice removes its current repository entry, but Git history
can retain older content. It is not secure historical erasure. A future backend
can implement `VoiceStorage` (`put_voice`, `get_voice`, `delete_voice`, and
`exists`) to provide object-store deletion without changing adapters or APIs.
