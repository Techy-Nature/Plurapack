# Privacy and safety

## What the database stores

Plurapack stores the relationship between Stoat account IDs, system IDs, members, forms, and proxy-message metadata. Proxy records include platform message IDs, channel ID, system/member IDs, initiating owner, timestamps, and deletion state.

## What proxy records do not store

Plurapack does **not** retain proxied message content in its database. Link codes are stored only as hashes. Portable exports exclude account IDs, linking codes, proxy-message records, message content, and voice settings.

## Public API

The [public API](../guides/public-api.md) only shows what a system has made public with `privacy`. Systems, members, groups, and the current front all start private. API tokens are stored only as hashes, can only be created in a direct message with the bot, and are deleted along with their system. The API never returns account IDs, link codes, voice data, or proxy-message records.

## Operational responsibilities

- Protect the SQLite database like account data.
- Restrict access to bot tokens, exports, and voice reference files.
- Use full-disk encryption when unencrypted SQLite storage is a concern.
- Grant the bot only the permissions required in intended channels.
- Keep voice cloning endpoints private and authenticated at the network boundary.
- Obtain appropriate consent before configuring a person's voice reference.

## Boundaries

Plurapack welcomes all systems and questioning people without requiring personal medical information. It is not a diagnostic tool, medical provider, crisis service, or identity authority.
