# Troubleshooting

## The bot does not start

**`STOAT_BOT_TOKEN or FLUXER_BOT_TOKEN is required`**
: Set at least one token in the same process environment used to start Plurapack. Set both to connect to both platforms.

**Stoat dependency is not installed**
: Activate the intended virtual environment and run `python -m pip install -e .` from the checkout.

## A tagged message is not proxied

- Confirm the tag exactly matches the configured prefix and suffix.
- Confirm the account owns or is linked to the member's system.
- Confirm the message is not a bot message or a command.
- Ask the operator to verify view, send, and masquerade permissions.

## The original message remains

The bot posts the proxy before attempting to delete the source. A remaining source usually means it lacks delete permission. The proxy is intentionally preserved rather than losing the message.

## A member, alias, or form conflicts

Names, aliases, and proxy-tag pairs must be unique inside a system. Use a different value. Imports are atomic, so resolve every reported conflict and retry the full import.

## Editing does not work

React to a proxy owned by your shared system, then send replacement text in the same channel. Ensure the bot can read reactions and edit its own messages.

## Voice configuration is disabled

The operator must configure the protected Modal endpoint (`PLURAPACK_TTS_URL`)
and its proxy Bearer credential (`PLURAPACK_TTS_API_KEY`). For custom uploads,
configure private Forgejo storage and Plurapack's write-capable
`VOICE_STORAGE_API_KEY`. Modal uses a separate read-only Forgejo credential and
must read the same repository/branch. See the [speech guide](../guides/speech.md).

Select a known generic filename with `voice generic MEMBER Jordan.wav`, or upload
a custom recording. Legacy named clones require an explicit generic selection
or re-upload; Plurapack does not guess their namespace. Spoken input longer than
500 characters fails speech without truncation while the text proxy continues.
A rejected Modal request is reported with a sanitized error; verify the proxy
credential, deployment availability, selected reference, and rate limits without
sharing tokens, UUIDs, or private message contents.

## Get useful diagnostics

Operators should record the command attempted, visible error, Python version, and relevant permission settings. Remove tokens, link codes, member exports, database contents, and private message data before sharing logs.
