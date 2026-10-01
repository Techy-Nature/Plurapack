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
