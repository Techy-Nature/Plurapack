# Plurapack

New to Plurapack? The text-only, website-ready user guide lives in [`documentation/`](documentation/README.md), with onboarding, everyday workflows, configuration, privacy, and troubleshooting documentation.

## Responsive dashboard

The repository includes a dependency-free dashboard prototype that can be hosted on GitHub Pages or any static web server. It provides responsive desktop and mobile layouts, independently scrollable member and profile panels on larger screens, member search, profile switching, and an interactive new-member dialog.

Open `index.html` directly, or serve the repository root locally:

```bash
python -m http.server 8000
```

Then visit `http://localhost:8000`. The member array in `app.js` remains preview data for an empty example system, while account and system identity are loaded from the authenticated API. The dashboard expects `GET /api/account` and `GET /api/systems/:systemId`, and posts new records to `POST /api/systems/:systemId/members` and `POST /api/systems/:systemId/members/:memberId/forms`. JSON responses use the same camel-case fields shown in the example member objects. Requests include same-origin credentials; a `401` redirects to `/login`, while a `403` displays a private-system state without leaking system data.

Plurapack is an early, self-hosted [Stoat](https://stoat.chat) member/headmate proxy. It welcomes plural systems of every origin and people who are questioning. It never asks for an origin, diagnosis, or proof of identity. It is a communication tool, **not** a diagnostic service.

## What works in this initial release

- Persistent SQLite systems (random 10-character SHA-256 fragments), members (5 characters), display names, proxy tags, avatar URLs, username colors, and voice preferences. Names select members; IDs remain stable across renames.
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
p;alias abc12 Al                # short selector; proxies still display the full name
p;form abc12 "Alex at Sea" https://example/avatar.png Blue fins and a long tail.
p;front 4f8ac                   # a form ID selects its linked member and form together
p;autoproxy Alex                # proxy Alex's untagged messages (Off by default)
p;autofront on                  # make autoproxy follow the current/first fronter
p;member Sam S: :S              # proxy with: S: hello :S
p;color abc12 7b68ee            # color that member's username (also accepts #7b68ee)
p;import pluralkit {"name":"...","members":[...]}  # paste a PluralKit JSON export
p;import tupperbox {"tuppers":[...]}                 # paste a Tupperbox JSON export
p;export plurapack              # attached backup; also: pluralkit or tupperbox
p;link                          # run on the existing owner account
p;verify abc123...              # run on the other account

React ✏️ / 📝, then send text   # edit one of your system's proxies
React ❌ / 🗑️                  # delete one of your system's proxies
Reply "Alex", "abc12", or "[alex]" to a proxy to change its member
```

Every command also has a one- or two-letter shortcut: `s` (setup), `m` (member),
`i` (import), `x` (export), `l` (link), `c` (color), `v` (verify), `vo` (voice),
`of` (voiceoff), `vf` (voiceformat), `a` (alias), `f` (form), `fr` (front),
`ap` (autoproxy), and `af` (autofront).

Aliases are short selectors only: the member's full name remains the name shown
on ordinary proxy messages and member cards. Forms are alternate presentations
with their own stable five-character IDs, display names, optional picture URLs,
and soma descriptions. A form ID is permanently connected to its member ID.
Passing it to `p;front` switches the current member and form atomically; using it
as a reply selector re-proxies with that form's display name and picture.

Change `PLURAPACK_PREFIX` to change the **command** prefix. Member input prefixes/suffixes are shortcuts, never identity keys.
Username colors are stored against stable member IDs and used on both new and re-proxied messages. Colors must be six-digit hex RGB values. Stoat requires the bot to have the Manage Roles permission to apply a masquerade color.

## Moving systems

`p;import pluralkit JSON` accepts the `name` and `members` fields from a PluralKit
JSON export, including the first `proxy_tags` entry, `avatar_url`, and `color`.
`p;import tupperbox JSON` accepts a `tuppers` array with each tupper's `name`,
two-item `brackets`, avatar, and color. The JSON may be pasted directly or in a
fenced code block. An account without a system gets one automatically; otherwise
members are added to its existing system. Imports are all-or-nothing when a name
or proxy tag conflicts. Members without external proxy tags receive `Name:` as a
predictable default. Voice configuration is deliberately not inferred or enabled.

`p;export` sends a versioned Plurapack JSON attachment. Use `p;export pluralkit`
or `p;export tupperbox` for a best-effort file shaped for that service. Transfer
files include system/member names, proxy tags, avatar URLs, and colors. They never
include owner account IDs, linking codes, proxy-message records, message content,
or voice settings. External IDs are not reused, so imported members receive new
Plurapack IDs. Treat exports as private files because member metadata can still be
sensitive. Service formats may evolve; inspect a generated file before relying on
it as your only backup.

## Optional local Chatterbox speech

Plurapack's server cannot force playback on somebody else's computer. `local` mode therefore requires a future companion client/browser extension; `send` uploads one MP3 associated with the proxy; `both` is for one local event plus one attachment; `off` (the default) does neither. The queue rejects work beyond `PLURAPACK_TTS_QUEUE_LIMIT`, can cancel by proxy-message ID, and never blocks text proxying.

The adapter targets devnen's Chatterbox-TTS-Server JSON `/tts` contract: `voice_mode=clone`, `reference_audio_filename`, and `output_format=mp3`. Set `PLURAPACK_TTS_URL` to the full endpoint (for example `http://127.0.0.1:8004/tts`), `PLURAPACK_TTS_QUEUE_LIMIT` to 1–1000 (default 8), `PLURAPACK_TTS_WORKERS` to 1–4 (default 1), and `PLURAPACK_VOICE_REFERENCE_DIR` to the local approved directory mirroring the server's `reference_audio` directory. Do not expose an unauthenticated voice-cloning endpoint to the internet.

Use `p;voice MEMBER FILENAME send {}` to enable attachments and `p;voiceoff MEMBER` to disable them. Files must already exist directly or below the approved directory; the bot stores and sends only their constrained relative identifier, never audio bytes from chat. Settings must be a JSON object and are limited to `temperature`, `exaggeration`, `cfg_weight`, `seed`, `speed_factor`, `language`, `split_text`, and `chunk_size`. Although the database reserves `local` and `both`, they are rejected because server-only Plurapack cannot cause client-side playback; only `send` currently produces output.

Semantic speech formatting is a separate opt-in and is **Off by default**. Enable it with
`p;voiceformat MEMBER on normal`; replace `normal` with `mumble`, `omit`, or `whisper` to
choose how `~~crossed-out text~~` sounds. When enabled, ordinary and quoted text is spoken,
`*single-asterisk actions*` is not spoken, Markdown punctuation is removed, and
`**double-asterisk text**` receives stronger emphasis. Mumble and whisper are best-effort
Chatterbox performances using lower exaggeration/configuration weight and adjusted speed;
results depend on the reference voice and model. Each differently styled span is rendered
separately and the MPEG streams are delivered together as one MP3 attachment. Disable the
interpretation without disabling voice attachments with `p;voiceformat MEMBER off`.

## Verified versus integration-pending

`pytest` mock-tests ID shape, authorization, shared stable identity, one-use account codes, attribution, command/duplicate suppression, reaction editing/deletion, reply re-proxying, and preservation of a source when posting fails. The installed stoat.py 1.2.1 API was locally inspected for message creation/reaction events, masqueraded sends, edits, fetches, and deletion. Live Stoat behavior, permissions, attachments, and Chatterbox synthesis remain untested; authorize a private test channel before testing them.

## Privacy and backups

The database necessarily maps Stoat account IDs to system/member IDs. Protect it like account data, restrict filesystem permissions, and back it up to preserve permanent IDs. Proxy content and raw link secrets are not retained. SQLite does not encrypt at rest; use full-disk encryption if that risk matters to your system.
