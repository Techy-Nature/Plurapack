# Before you begin

There are two roles in a Plurapack setup:

- A **user** sends commands and proxied messages in Stoat.
- An **operator** installs the bot, protects its token and database, and grants it channel permissions.

You can be both. If someone already runs Plurapack in your server, skip installation and continue to [Your first system](first-system.md).

## What users need

- A Stoat account.
- Access to a server and channel containing the Plurapack bot.
- Permission from the server or system owner to use that space.

## What operators need

- Python 3.11 or newer.
- A bot created in Stoat and its private token.
- A machine that can stay online while the bot is needed.
- A plan for protecting and backing up the SQLite database.

The bot needs permission in intended proxy channels to view and send messages, use masquerades, upload files if speech is enabled, add and read reactions, edit its own messages, and delete invoking users' source messages. Grant it only the permissions it needs.

## A few terms

**System**
: A group of members and authorized owner accounts. Plurapack assigns it a stable 10-character ID.

**Member**
: A proxy identity with a name and tag. It receives a stable 5-character ID.

**Proxy tag**
: The text around a message that tells Plurapack which member should speak, such as `[alex] hello`.

**Alias**
: A short selector for commands. It does not replace the full display name shown on messages.

**Form**
: An alternate presentation connected to one member. A form has its own stable ID, display name, optional picture, and soma description.
