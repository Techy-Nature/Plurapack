# Configuration reference

Plurapack reads configuration from environment variables. It does not automatically load `.env` files.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `STOAT_BOT_TOKEN` | Yes | — | Private Stoat bot token. |
| `PLURAPACK_PREFIX` | No | `p;` | Prefix for bot commands, not member tags. |
| `PLURAPACK_DATABASE` | No | `plurapack.sqlite3` | SQLite database path. |
| `PLURAPACK_TTS_URL` | No | disabled | Full HTTP(S) Chatterbox `/tts` endpoint. |
| `PLURAPACK_VOICE_REFERENCE_DIR` | For voice setup | — | Approved local reference-audio directory. |
| `PLURAPACK_TTS_QUEUE_LIMIT` | No | `8` | Pending speech limit, from 1 to 1000. |
| `PLURAPACK_TTS_WORKERS` | No | `1` | Speech workers, from 1 to 4. |

Restart the bot after changing its process environment. Keep secrets out of Git, shell history where possible, support messages, and screenshots.
