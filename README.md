# Plurapack

Plurapack is an early, self-hosted [Stoat](https://stoat.chat) member/headmate proxy. It welcomes plural systems of every origin and people who are questioning. It never asks for an origin, diagnosis, or proof of identity. It is a communication tool, **not** a diagnostic service.

## What works in this initial release

- Persistent SQLite systems (random 10-character SHA-256 fragments), members (5 characters), display names, proxy tags, avatar URLs, and voice preferences. Names select members; IDs remain stable across renames.
- Multiple authorized Stoat accounts can share one system and its stable IDs through a 15-character, one-use, 15-minute code. Only the hash of the code is stored.
- Text proxying through stoat.py masquerades. The replacement is posted and recorded before the source is deleted. Bot messages, commands, in-flight messages, and persisted duplicate source IDs are ignored.
- Every proxy record retains platform message IDs, system/member IDs, channel and initiating owner, but **not message content**.
- A bounded, cancellable, asynchronous speech queue and replaceable `SpeechBackend` interface. Playback is Off by default.

Reaction-driven edit/delete/ping and the production Chatterbox HTTP adapter are the next integration increment. The authorization queries and immutable attribution required for them are present, but these actions are deliberately not advertised as live commands yet. No Stoat messages were sent while developing this release.

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

Never commit `.env`, the SQLite database, or reference voice files. The bot needs permission in each target channel to view/send messages, use masquerades, upload files (when speech delivery is enabled), add/read reactions, edit its own messages, and delete the invoking user's source message. If deletion is denied, the proxy remains posted and the source is preserved; operators should grant only the permissions needed in intended proxy channels.

Initial commands:

```text
p;setup My system
p;member Alex [alex]            # proxy with: [alex] hello
p;member Sam S: :S              # proxy with: S: hello :S
p;link                          # run on the existing owner account
p;verify abc123...              # run on the other account
```

Change `PLURAPACK_PREFIX` to change the **command** prefix. Member input prefixes/suffixes are shortcuts, never identity keys.

## Optional local Chatterbox speech

Plurapack's server cannot force playback on somebody else's computer. `local` mode therefore requires a future companion client/browser extension; `send` uploads one MP3 associated with the proxy; `both` is for one local event plus one attachment; `off` (the default) does neither. The queue rejects work beyond `PLURAPACK_TTS_QUEUE_LIMIT`, can cancel by proxy-message ID, and never blocks text proxying.

For now, host [Chatterbox](https://github.com/resemble-ai/chatterbox) on the same trusted machine (preferably bound to `127.0.0.1`), keep reference audio outside the repository, and implement `SpeechBackend.synthesize()` for that service's endpoint. Do not expose an unauthenticated voice-cloning endpoint to the internet. `PLURAPACK_TTS_URL` is reserved for the forthcoming adapter and is inactive in this release.

## Verified versus integration-pending

`pytest` mock-tests ID shape, authorization, shared stable identity, one-use account codes, attribution, command/duplicate suppression, and preservation of a source when posting fails. The installed stoat.py 1.2.1 API was locally inspected for `commands.Bot`, `MessageCreateEvent`, `MessageMasquerade`, channel `send`, and message `delete`. Live Stoat behavior, permissions, reaction events, attachments, and Chatterbox synthesis remain untested; authorize a private test channel before testing them.

## Privacy and backups

The database necessarily maps Stoat account IDs to system/member IDs. Protect it like account data, restrict filesystem permissions, and back it up to preserve permanent IDs. Proxy content and raw link secrets are not retained. SQLite does not encrypt at rest; use full-disk encryption if that risk matters to your system.
