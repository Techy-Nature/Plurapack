# Plurapack

Plurapack is an early, self-hosted [Stoat](https://stoat.chat) member/headmate proxy. It welcomes plural systems of every origin and people who are questioning. It never asks for an origin, diagnosis, or proof of identity. It is a communication tool, **not** a diagnostic service.

## What works in this initial release

- Persistent SQLite systems (random 10-character SHA-256 fragments), members (5 characters), display names, proxy tags, avatar URLs, and voice preferences. Names select members; IDs remain stable across renames.
- Multiple authorized Stoat accounts can share one system and its stable IDs through a 15-character, one-use, 15-minute code. Only the hash of the code is stored.
- Text proxying through stoat.py masquerades. The replacement is posted and recorded before the source is deleted. Bot messages, commands, in-flight messages, and persisted duplicate source IDs are ignored.
- Every proxy record retains platform message IDs, system/member IDs, channel and initiating owner, but **not message content**.
- React to one of your proxies with ✏️ or 📝, then send its replacement text in the same channel, to edit it. React with ❌ or 🗑️ to delete it. Shared-system owners may manage one another's proxies.
- Re-proxy an existing message by replying to it with only the new member's display name, stable five-character ID, or proxy prefix. The replacement is posted and recorded before the old proxy and selector reply are removed.
- A bounded, cancellable, asynchronous speech queue and Chatterbox HTTP backend. Playback is Off by default. Edits and re-proxy operations intentionally do not generate replacement audio in this first release; a newly tagged text proxy does.

No live Stoat messages or Chatterbox requests were sent while developing this release.

## Install and run

Requires Python 3.11+.

```bash
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Create a bot in Stoat, place its token in your environment (the program does not automatically read `.env`), then run:

```bash
export STOAT_BOT_TOKEN='...'
export PLURAPACK_PREFIX='p;'
plurapack
```

To run directly from a source checkout on Windows or another PC without using
the installed console script, set the same environment variables and launch
the repository's entry point:

```powershell
$env:STOAT_BOT_TOKEN = "..."
$env:PLURAPACK_PREFIX = "p;"
python main.py
```

From Windows Command Prompt, use `set STOAT_BOT_TOKEN=...` and then
`python main.py` instead.

Never commit `.env`, the SQLite database, or reference voice files. The bot needs permission in each target channel to view/send messages, use masquerades, upload files (when speech delivery is enabled), add/read reactions, edit its own messages, and delete the invoking user's source message. If deletion is denied, the proxy remains posted and the source is preserved; operators should grant only the permissions needed in intended proxy channels.

Initial commands:

```text
p;setup My system
p;member Alex [alex]            # proxy with: [alex] hello
p;member Sam S: :S              # proxy with: S: hello :S
p;link                          # run on the existing owner account
p;verify abc123...              # run on the other account

React ✏️ / 📝, then send text   # edit one of your system's proxies
React ❌ / 🗑️                  # delete one of your system's proxies
Reply "Alex", "abc12", or "[alex]" to a proxy to change its member
```

Change `PLURAPACK_PREFIX` to change the **command** prefix. Member input prefixes/suffixes are shortcuts, never identity keys.

## Optional local Chatterbox speech

Plurapack's server cannot force playback on somebody else's computer. `local` mode therefore requires a future companion client/browser extension; `send` uploads one MP3 associated with the proxy; `both` is for one local event plus one attachment; `off` (the default) does neither. The queue rejects work beyond `PLURAPACK_TTS_QUEUE_LIMIT`, can cancel by proxy-message ID, and never blocks text proxying.

The adapter targets devnen's Chatterbox-TTS-Server JSON `/tts` contract: `voice_mode=clone`, `reference_audio_filename`, and `output_format=mp3`. Set `PLURAPACK_TTS_URL` to the full endpoint (for example `http://127.0.0.1:8004/tts`), `PLURAPACK_TTS_QUEUE_LIMIT` to 1–1000 (default 8), `PLURAPACK_TTS_WORKERS` to 1–4 (default 1), and `PLURAPACK_VOICE_REFERENCE_DIR` to the local approved directory mirroring the server's `reference_audio` directory. Do not expose an unauthenticated voice-cloning endpoint to the internet.

Use `p;voice MEMBER FILENAME send {}` to enable attachments and `p;voiceoff MEMBER` to disable them. Files must already exist directly or below the approved directory; the bot stores and sends only their constrained relative identifier, never audio bytes from chat. Settings must be a JSON object and are limited to `temperature`, `exaggeration`, `cfg_weight`, `seed`, `speed_factor`, `language`, `split_text`, and `chunk_size`. Although the database reserves `local` and `both`, they are rejected because server-only Plurapack cannot cause client-side playback; only `send` currently produces output.

## Verified versus integration-pending

`pytest` mock-tests ID shape, authorization, shared stable identity, one-use account codes, attribution, command/duplicate suppression, reaction editing/deletion, reply re-proxying, and preservation of a source when posting fails. The installed stoat.py 1.2.1 API was locally inspected for message creation/reaction events, masqueraded sends, edits, fetches, and deletion. Live Stoat behavior, permissions, attachments, and Chatterbox synthesis remain untested; authorize a private test channel before testing them.

## Privacy and backups

The database necessarily maps Stoat account IDs to system/member IDs. Protect it like account data, restrict filesystem permissions, and back it up to preserve permanent IDs. Proxy content and raw link secrets are not retained. SQLite does not encrypt at rest; use full-disk encryption if that risk matters to your system.
