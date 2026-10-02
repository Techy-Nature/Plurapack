# Plurapack

New to Plurapack? The text-only, website-ready user guide lives in [`documentation/`](documentation/README.md), with onboarding, everyday workflows, configuration, privacy, and troubleshooting documentation.

Plurapack is an early alpha, Railway-hosted [Stoat](https://stoat.chat) and [Fluxer](https://web.fluxer.app/) member/headmate proxy. It welcomes plural systems of every origin and people who are questioning. It never asks for an origin, diagnosis, or proof of identity. It is a communication tool, **not** a diagnostic service.

Invites:

Stoat: https://stoat.chat/bot/01M1ZB80QNH00RN1XEWH3D38TH

Fluxer: https://web.fluxer.app/oauth2/authorize?client_id=1548168978344448000&scope=bot&permissions=536983552

## Dashboard and Web API

The repository includes a dependency-free dashboard prototype that can be hosted on GitHub Pages or any static web server. It provides responsive desktop and mobile layouts, independently scrollable member and profile panels on larger screens, member search, profile switching, and an interactive new-member dialog.

The dashboard is backed by a FastAPI server that uses the same `Store` and
`PLURAPACK_DATABASE` SQLite file as the bot. Start it with:

```bash
export PLURAPACK_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
python -m plurapack.web
# equivalently, after installation: plurapack-web
```

Then visit `http://127.0.0.1:8000`. Set `PLURAPACK_WEB_HOST` and
`PLURAPACK_WEB_PORT` to change the bind address. Run the bot and Web API in two
terminals with the same `PLURAPACK_DATABASE` to run both. SQLite WAL mode and
short per-operation connections allow safe concurrent access. The existing
`owners.account_id` primary key intentionally limits each account to one linked
system; the account response uses a `systems` list only as a stable API shape.

### Railway deployment

Railway automatically builds the included `Dockerfile`. It installs Plurapack
and all declared Python dependencies (including Uvicorn), then starts the bot
and dashboard together with `python -m plurapack`; no custom start command is
required. Remove any existing Railway start-command override such as `uvicorn
...` so the image command is used. The server automatically listens on
Railway's `PORT` and on all interfaces.

Railway's container filesystem is ephemeral. To retain systems, members,
settings, login state, and other SQLite data across redeployments or container
replacement:

1. Create or attach a Railway Volume to the existing Plurapack service.
2. Mount the volume at `/data`.
3. Add `PLURAPACK_DATA_DIR=/data` to the service variables.
4. Keep the unified Start Command as `python -m plurapack` (or let the
   `Dockerfile` command run).

A Railway Volume is required for SQLite data to survive container replacement;
setting `PLURAPACK_DATA_DIR` alone does not make an ephemeral filesystem
persistent. Keep both the bot and dashboard in the **same Railway service** for
this deployment model. The launcher prints the selected database path at
startup so `/data/plurapack.sqlite3` is easy to verify.

`PLURAPACK_DATABASE` remains supported and takes precedence when set, using its
exact path. Otherwise, `PLURAPACK_DATA_DIR` places the default database at
`<data-dir>/plurapack.sqlite3`. With neither variable, the existing
`plurapack.sqlite3` current-directory default remains unchanged. Plurapack
creates missing database directories and fails startup rather than falling back
if configured storage cannot be used. It never automatically moves or deletes
an existing database; operators changing paths must deliberately move any data
they want to retain while the service is stopped.

At minimum, also configure `PLURAPACK_SESSION_SECRET` as a stable random value
of at least 32 characters and configure `STOAT_BOT_TOKEN` and/or
`FLUXER_BOT_TOKEN`.

The API provides `/api/account`, authorized system/member/form CRUD, front
retrieval and switching, and `/api/health`; interactive OpenAPI documentation is
at `/docs`. Plurapack does not rely on Stoat OAuth. Instead, `/login` creates a
five-minute, one-time code and asks the user to send `<prefix>login CODE` to the
Plurapack bot. The bot obtains the real account ID from the authenticated Stoat
message author, and the initiating browser exchanges its separate private secret
for the existing signed `plurapack_session` cookie. Neither the public attempt ID
nor the browser can select an account ID.

Run the bot and web process against the same `PLURAPACK_DATABASE`; otherwise the
bot cannot approve the web process's pending codes. Login codes use eight
unambiguous characters displayed as `XXXX-XXXX`, and both those codes and the
browser credentials are stored only as SHA-256 hashes. Attempts are single-use,
expire after five minutes, and records more than a day past expiration are
removed when another login begins. `POST /api/auth/logout` removes the local
session; it does not alter the user's Stoat account.

The session secret must be at least 32 random, private characters and must remain
stable across host-process restarts; changing it immediately invalidates every
existing login. Session cookies are `HttpOnly`, `SameSite=Lax`, scoped to `/`,
and last seven days, including across browser and host-process restarts when the
same secret is retained. `PLURAPACK_COOKIE_SECURE`
defaults to `true`, as required for production HTTPS. For local HTTP development
only, explicitly set `PLURAPACK_COOKIE_SECURE=false`; never use that setting on a
public deployment. No Stoat token, password, account ID, login code, browser
secret, or session value should be entered into the dashboard or written to logs.
Login creation is limited to 10 attempts per directly connected client per
minute per web process by default; change `PLURAPACK_LOGIN_START_LIMIT` if needed.
This small in-memory limit does not retain permanent IP history and intentionally ignores
`X-Forwarded-For`, so deployments behind a reverse proxy should enforce any
client-aware rate limit at that trusted proxy.

### Browser voice playback

Voice playback modes have destination-based meanings: `send` uploads the one
generated MP3 to Stoat, `local` offers it only to the authenticated dashboard,
and `both` routes that same synthesis result to both destinations. Dashboard
playback is opt-in per browser tab through **Enable voice playback**, is ordered,
and uses authenticated polling plus one-use audio retrieval. If several tabs for
the same account are open, the first one to retrieve a clip consumes it.

The unified supervisor runs the bot and Web API as separate child processes, so
ephemeral audio uses a private cross-process spool rather than SQLite. Both
processes use the same `PLURAPACK_DATABASE` (or explicitly share
`PLURAPACK_BROWSER_AUDIO_DIR`). Opaque clips expire after 120 seconds and the
spool retains at most 100 clips by default; operators can adjust these bounds
with `PLURAPACK_BROWSER_AUDIO_TTL` and `PLURAPACK_BROWSER_AUDIO_LIMIT`.
The default spool stays in the operating system's temporary directory even when
`PLURAPACK_DATA_DIR` is set, so a persistent volume does not become an audio
archive.

The existing frontend contract is: `GET /api/account` initializes account and
system identity; `GET /api/systems/:systemId` loads members; `POST
/api/systems/:systemId/members` accepts the new-member fields; `POST
/api/systems/:systemId/members/:memberId/forms` accepts the new-form fields; and
`PATCH /api/systems/:systemId/members/:memberId` currently changes a default
form. Member creation requires an explicit proxy prefix, matching the bot's
`member` command; the API does not derive one from the member name. Successful
creates return the new object, patches return the updated
object, deletes return `204`, validation errors return `422`, unauthenticated
requests return `401`, unauthorized systems return `403`, and missing or
mismatched nested resources return `404`.


## What works in this initial release

- Persistent SQLite systems (random 10-character SHA-256 fragments), members (5 characters), display names, proxy tags, avatar URLs, username colors, and voice preferences. Names select members; IDs remain stable across renames.
- Multiple authorized Stoat accounts (or one Fluxer and one Stoat account) can share one system and its stable IDs through a 15-character, one-use, 15-minute code. Only the hash of the code is stored.
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

Create a bot in Stoat, Fluxer, or both, and place its token in your environment
(the program does not automatically read `.env`). When both tokens are set,
one Plurapack process connects to both services and shares the configured database:

```bash
export STOAT_BOT_TOKEN='...'
export FLUXER_BOT_TOKEN='...'
export PLURAPACK_PREFIX='p;'
plurapack
```

To run directly from a source checkout on Windows or another PC without using
the installed console script, set the same environment variables and launch
the repository's entry point:

```powershell
$env:STOAT_BOT_TOKEN = "..."
$env:FLUXER_BOT_TOKEN = "..."
$env:PLURAPACK_PREFIX = "p;"
python main.py
```

From Windows Command Prompt, use `set STOAT_BOT_TOKEN=...` and/or
`set FLUXER_BOT_TOKEN=...`, then
`python main.py` instead.

Never commit `.env`, the SQLite database, or reference voice files. On Fluxer,
Plurapack uses a channel webhook named **Plurapack Proxy**, so it also needs
Manage Webhooks. The bot needs permission in each target channel to view/send
messages, upload files (when speech delivery is enabled), add/read reactions,
edit its own messages, and delete the invoking user's source message. Stoat
also requires masquerade permission. If deletion is denied, the proxy remains
posted and the source is preserved; operators should grant only the permissions
needed in intended proxy channels.

Initial commands:

```text
p;setup My system
p;member Alex [alex]            # proxy with: [alex] hello
p;memberproxy Alex A: :A        # add another tag; repeat for more
p;memberproxy Alex              # list Alex's tags (up to 100)
p;memberproxy-clear Alex        # clear all of Alex's tags
p;alias abc12 Al                # short selector; proxies still display the full name
p;form abc12 "Alex at Sea" https://example/avatar.png Blue fins and a long tail.
p;formproxy 4f8ac sea: :sea     # sea: proxy with this form even off-front :sea
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
p;viewinfo                     # system card, then member cards with arrow reactions
p;viewmember abc12             # one member card by stable ID or exact name
p;viewmembers                  # member cards only, two embeds per responsive page
p;deletemember abc12           # permanently remove member, forms, and records
p;deletesystem                 # reply to its warning with the exact system ID

React ✏️ / 📝, then send text   # edit one of your system's proxies
React ❌ / 🗑️                  # delete one of your system's proxies
Reply "Alex", "abc12", or "[alex]" to a proxy to change its member
```

Every command also has a one- or two-letter shortcut: `s` (setup), `m` (member),
`i` (import), `x` (export), `l` (link), `c` (color), `v` (verify), `vo` (voice),
`of` (voiceoff), `vf` (voiceformat), `a` (alias), `f` (form), `mt` (memberproxy),
`mc` (memberproxy-clear), `ft` (formproxy), `fc` (formproxy-clear), `fr` (front),
`ap` (autoproxy), `af` (autofront), `vi` (viewinfo), `ml` (viewmembers), `vm`
(viewmember), `dm` (deletemember), and `ds` (deletesystem).

View pagers use Unicode left/right arrows by default and accept configured Stoat
custom emoji IDs through `PLURAPACK_PREVIOUS_EMOJI_ID` and
`PLURAPACK_NEXT_EMOJI_ID`. Only the requesting account can turn its pages.
Before using `p;deletesystem`, create a private `p;export plurapack` JSON backup
if needed. The bot requires an exact system-ID reply to its warning before it
erases the system and all associated data.

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
