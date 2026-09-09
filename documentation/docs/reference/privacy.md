# Privacy and safety

## What the database stores

Plurapack stores the relationship between Stoat account IDs, system IDs, members, forms, and proxy-message metadata. Proxy records include platform message IDs, channel ID, system/member IDs, initiating owner, timestamps, and deletion state.

## What proxy records do not store

Plurapack does **not** retain proxied message content in its database. Link codes are stored only as hashes. Portable exports exclude account IDs, linking codes, proxy-message records, message content, and voice settings.

## Operational responsibilities

- Protect the SQLite database like account data.
- Restrict access to bot tokens, exports, and voice reference files.
- Use full-disk encryption when unencrypted SQLite storage is a concern.
- Grant the bot only the permissions required in intended channels.
- Keep voice cloning endpoints private and authenticated at the network boundary.
- Obtain appropriate consent before configuring a person's voice reference.

## Boundaries

Plurapack welcomes all systems and questioning people without requiring personal medical information. It is not a diagnostic tool, medical provider, crisis service, or identity authority.
